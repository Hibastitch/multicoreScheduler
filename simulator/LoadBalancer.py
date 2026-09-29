"""
Direct analog of the verified pipeline:

    sched_balance_domains() -> should_we_balance() -> update_sg_lb_stats()
    -> group_classify() -> sched_balance_find_src_group() -> calculate_imbalance()
    -> migrate

and separately:

    sched_balance_newidle() -- cost/probability-gated reactive pulling

Simplifications, stated explicitly:
  - group_classify() collapses to 3 states (has_spare/fully_busy/overloaded)
    instead of the real ~7-way enum (misfit/asym/imbalanced/smt/llc/etc).
  - calculate_imbalance() implements only the migrate_load formula (the
    min() trick from the overloaded/overloaded case) plus a simple
    has-spare task-count path; the other migration_type branches
    (misfit, asym_packing, smt_balance, llc_balance) are not modeled.
  - newidle cost-gating is a rough EMA success-rate proxy for
    sd->newidle_ratio / sd->max_newidle_lb_cost, not the real formula.
"""

import random

from Topology import GROUP_HAS_SPARE, GROUP_OVERLOADED, CAPACITY_SCALE, classify_group, STATS, Domain, domain_chain

# VERIFIED (2026-09-27, Linux v7.2, kernel/sched/sched.h): sysctl_sched_
# nr_migrate defaults to SCHED_NR_MIGRATE_BREAK (core.c:191), which is 32
# outside CONFIG_PREEMPT_RT (sched.h:3100-3107) -- this constant was
# already right, now confirmed against source rather than assumed. Real
# loop_max = min(sysctl_sched_nr_migrate, busiest->nr_running)
# (fair.c:13341); the flat 32 here achieves the same practical bound
# since the while loop already stops once src_core.rq is empty.
MAX_MIGRATE_PER_PASS = 32

# adjust_numa_imbalance()-style constants. Real Linux derives these from
# machine topology; we use small, documented stand-ins since the exact
# real values (NUMA_IMBALANCE_MIN, imb_numa_nr) were never opened this
# session -- treat these as approximations, not verified numbers.
NUMA_IMBALANCE_MIN = 32          # capacity-scale units; below this, forgive small imbalances
NUMA_DST_BUSY_THRESHOLD = 2      # running tasks; above this, skip the forgiveness check

# TASK 8 FIX 5 (2026-09-29, docs/FIDELITY_AUDIT.md §8, NOTEBOOK.md
# 2026-09-29c): the corrected values, verified against fair.c:2177
# (#define NUMA_IMBALANCE_MIN 2, hardcoded) and this topology's own
# adjust_numa_imbalance() derivation (topology.c:2870-2934: nr_llcs=3 at
# the onehop level for this 4-node/8-core-per-node/3-node-onehop layout,
# both onehop's and machine's upward-propagation factor=1, giving
# imb_numa_nr=3 at both NUMA levels). Selected by
# LoadBalancer._adjust_numa_imbalance() when numa_fix=True, in place of
# the legacy NUMA_IMBALANCE_MIN/NUMA_DST_BUSY_THRESHOLD above.
NUMA_IMBALANCE_MIN_KERNEL = 2
NUMA_DST_BUSY_THRESHOLD_KERNEL = 3

# TASK 8 FIX 4 (2026-09-29, docs/FIDELITY_AUDIT.md §7): task_hot()'s
# cache-hot window (fair.c:10291-10329) -- sysctl_sched_migration_cost =
# 500,000 ns = 0.5 sim-ms (fair.c:82). Read by
# LoadBalancer._cache_hot_blocked() when cache_hot=True.
CACHE_HOT_THRESHOLD_MS = 0.5

# RNG ISOLATION (Fix 3d, 2026-09-26, see Readme.md and WorkloadGenerator.py's
# module docstring for the full story): the legacy_ema newidle gate's
# random.random() call must NOT share Python's global `random` module with
# WorkloadGenerator, or a scheduler-dependent draw count silently diverges
# the "same seed" workload between a baseline and burst-aware run. This
# balancer gets its own random.Random(seed + NEWIDLE_RNG_SEED_OFFSET)
# instance instead -- offset large enough that it can never collide with
# any actual seed used as a WorkloadGenerator seed in a sweep (this repo's
# seed sweeps stay well under 10_000).
NEWIDLE_RNG_SEED_OFFSET = 10_000_000


class LoadBalancer:
    # FINAL CONFIGURATION (2026-09-27i, see Readme.md): defaults below are
    # the fully-verified A+B+C fidelity fix chain (newidle transition edge,
    # per-CPU balance timers, kernel calculate_imbalance()), confirmed
    # against pinned kernel source and stress-tested through Task 6's full
    # investigation. This SUPERSEDES the earlier "opt-in, old-behavior-by-
    # default" stance for these three flags -- `results.csv` and any other
    # CSV generated before 2026-09-27i reflects the OLD defaults
    # (newidle_mode="legacy_ema", per_cpu_last_balance=False,
    # imbalance_model="legacy") and is not comparable to a fresh run
    # without passing those three kwargs explicitly. Pass them explicitly
    # to reproduce pre-2026-09-27i behavior exactly.
    def __init__(self, machine_domain, cores_by_id, logger=None, migration_penalty=0.0,
                 newidle_mode="transition", seed=0, per_cpu_last_balance=True,
                 imbalance_model="kernel", checker_model="kernel",
                 busy_factor=16, cache_hot=True, numa_fix=True,
                 penalty_model="all"):
        self.machine = machine_domain
        self.cores_by_id = cores_by_id
        self.migrations = 0
        self.logger = logger
        self.nr_balance_failed = {}  # id(core) -> consecutive failed-to-fully-balance count

        # FIDELITY GAP, Task 3b (see Readme.md ledger): "legacy_ema"
        # polls try_newidle() on EVERY idle tick, gated by an EMA
        # success-rate (core.newidle_cost_avg) that Task 3 found decays to
        # near-zero during any quiet stretch and self-suppresses from
        # there -- nothing like real Linux's actual mechanism. "transition"
        # (DEFAULT since 2026-09-27i) attempts newidle balancing exactly
        # ONCE, on the busy->idle edge (Core.run()), with no random gate at
        # all -- closer to real Linux, which fires newidle on that same
        # transition, gated by avg_idle vs max_newidle_lb_cost (a real cost
        # budget, not modeled here -- see the ledger entry for what's and
        # isn't approximated) and driven by nohz idle-balance kicks rather
        # than per-tick polling once idle. Pass newidle_mode="legacy_ema"
        # for the original behavior.
        self.newidle_mode = newidle_mode
        # RNG ISOLATION (Fix 3d) -- private instance, see the module-level
        # comment above NEWIDLE_RNG_SEED_OFFSET. Only consulted in
        # legacy_ema mode; transition mode has no random gate at all.
        self._newidle_rng = random.Random(seed + NEWIDLE_RNG_SEED_OFFSET)

        # FIDELITY GAP (see Readme.md ledger): real migrations pay for lost
        # cache/TLB locality on the destination core; this sim has no such
        # charge by default (migration_penalty=0.0). When set, _do_migrate()
        # adds this many ms to the migrated task's remaining_time as a
        # flat, unverified stand-in for that cost -- not derived from any
        # real hardware measurement, just a knob to test whether results
        # (esp. burst-aware's edge) survive migrations no longer being free.
        self.migration_penalty = migration_penalty

        # TASK 9a (2026-09-29, docs/NOTEBOOK.md 2026-09-29h pre-
        # registration, motivated by the Task 8 v2 grid finding every
        # threshold config harmful on bursty_high_s64/avg_slowdown at
        # penalty=2ms): "all" (default, exact pre-Task-9 behavior) charges
        # migration_penalty to EVERY migrated task unconditionally, even
        # one that has never run and therefore has no warm cache to lose
        # -- real Linux's own can_migrate_task()/task_hot() (fair.c:
        # 10291-10329, cited already in this file's cache_hot flag)
        # establishes that a task's cache-hotness is governed by
        # p->se.exec_start, which __sched_fork() sets to 0 at fork
        # (core.c:4568) and only overwrites once the task is actually
        # picked to run (update_stats_curr_start(), fair.c:2150-2156) --
        # the SAME kernel fact already used to gate cache_hot above.
        # "ran_only" charges the penalty only when Task.last_ran_until is
        # not None (the task has executed at least once) -- applies
        # identically to periodic/newidle/burst migrations, and to both
        # LoadBalancer and BurstAwareLoadBalancer (this is a cost-model
        # parameter, not a burst-path-specific one). See _do_migrate()
        # below for the exact application, and self.penalty_charges for
        # the new per-run counter this adds.
        self.penalty_model = penalty_model
        self.penalty_charges = 0

        # verified: SD_SERIALIZE is a real atomic global lock in
        # sched_balance_rq() -- only one core machine-wide runs a NUMA-level
        # pass at a time; everyone else bails immediately rather than wait.
        self._numa_balance_locked = False

        # FIX A (2026-09-26, see Readme.md): real Linux keeps a per-CPU
        # COPY of each sched_domain (for_each_domain(cpu, sd) walks a
        # per-cpu allocated hierarchy), so sd->last_balance/
        # sd->balance_interval are per (cpu, domain) -- a non-designated
        # CPU's should_we_balance()==false bail only touches ITS OWN
        # copy, never blocking the actual elected CPU's turn.
        #
        # False = legacy behavior (shared Domain.last_balance/
        # .balance_interval): found in Task 6 prep to have a real bug --
        # ANY core whose climb reaches a domain with an elapsed interval
        # resets the SHARED timestamp unconditionally, before checking
        # whether it's the designated checker, so a lower-core_id sibling
        # permanently steals that domain's balancing opportunity from the
        # actual (often idle-elected) checker every cycle. See
        # task6_why_periodic_stalls.py's trace: core 30 stole n3-pair3's
        # timer from core 31 on every one of 178 checks; core 31 was never
        # even asked once.
        #
        # True (DEFAULT since 2026-09-27i) = per (core_id, id(domain))
        # state, lazily created in _balance_state() below -- matches the
        # per-cpu-copy model. Pass False for the pre-Fix-A behavior.
        self.per_cpu_last_balance = per_cpu_last_balance
        self._per_cpu_balance_state = {}  # (core_id, id(domain)) -> {"last_balance", "balance_interval"}

        # FIX C (2026-09-27, see Readme.md): "legacy" keeps the single
        # generic min()-trick formula for the has-spare-local case
        # regardless of busiest's type. "kernel" (DEFAULT since
        # 2026-09-27i) ports calculate_imbalance()'s actual 3-way branch
        # for local_type==group_has_spare (fair.c:12642-
        # 12703, v7.2): migrate_util (busiest overloaded AND the domain
        # doesn't share an LLC -- Domain.share_llc, verified against
        # sd_init()/default_topology(), topology.c ~2032-2103) vs
        # sibling_imbalance/nr_running-diff (busiest group_weight==1 --
        # always true at this sim's pair level -- or prefer_sibling) vs
        # idle-cpu-count diff (otherwise, the ONE branch "legacy" already
        # had). Needs Task/Core's util_avg (Fix C companion change) for
        # migrate_util's sizing and per-task cost.
        self.imbalance_model = imbalance_model

        # FIX D (2026-09-29, see Readme.md): "legacy" (default) elects the
        # periodic-balance checker from domain.cores() -- the domain's
        # WHOLE SPAN, including neighbor cores counted in it (via ring
        # reuse, Topology.py's home_children) that never actually climb
        # this exact domain object. At onehop/machine levels, where spans
        # overlap, this can elect a checker that is_designated_checker()
        # will NEVER see call itself in -- the periodic pass for that
        # domain is then structurally, silently skipped forever (verified
        # directly: development/topology_audit/, up to ~70% of onehop/
        # machine-level checks under heavy load). "kernel" ports
        # should_we_balance() (fair.c:13162-13221, v7.2): elect from the
        # LOCAL GROUP containing the asking core (`env->sd->groups`, the
        # per-cpu-oriented first group) restricted to that group's real
        # "balance mask" -- cores whose OWN home chain resolves to that
        # exact group, matching build_balance_mask()'s cpumask_equal()
        # check (topology.c:1199-1225) that excludes reused-neighbor
        # cores the same way. Election within that restricted candidate
        # set: first fully-idle-CORE (both SMT threads idle) at levels
        # without SD_SHARE_CPUCAPACITY, else first idle SMT-busy CPU, else
        # group_balance_cpu() (lowest core_id in the balance mask) --
        # should_we_balance()'s exact 3-tier preference. At the pair/SMT
        # level itself (SD_SHARE_CPUCAPACITY set), the local group is
        # degenerate (just the asking core itself), so BOTH siblings
        # independently pass -- matches should_we_balance() returning
        # true unconditionally at the base level, real Linux's actual
        # behavior, not modeled by "legacy" at all.
        #
        # DEFAULT CHANGED 2026-09-29 (Task 8, see NOTEBOOK.md 2026-09-29c/d
        # and docs/FIDELITY_AUDIT.md): "kernel" is now the default,
        # following the Step 2 baseline-only ablation
        # (development/fidelity_audit/task8_step2_baseline_ablation.py).
        # Pass checker_model="legacy" explicitly to reproduce pre-Task-8
        # behavior exactly.
        self.checker_model = checker_model

        # TASK 8 FIX 2 (2026-09-29, docs/FIDELITY_AUDIT.md §1, NOTEBOOK.md
        # 2026-09-29c): get_sd_balance_interval(sd, cpu_busy) (fair.c:
        # 13565-13586) multiplies the periodic-check interval by
        # sd->busy_factor when the checking CPU is busy. busy_factor=1 is
        # a pure no-op multiplier -- exact pre-Task-8 behavior.
        # busy_factor=16 (DEFAULT since 2026-09-29) matches sd_init()'s
        # real default (topology.c:1958). See _get_balance_interval()
        # below for the exact application (check-time only, never
        # persisted). Pass busy_factor=1 explicitly to reproduce
        # pre-Task-8 behavior exactly.
        self.busy_factor = busy_factor

        # TASK 8 FIX 4 (2026-09-29, docs/FIDELITY_AUDIT.md §7): False
        # never consults Task.last_ran_until -- exact pre-Task-8 behavior
        # (every candidate task is migratable regardless of how recently
        # it ran). True (DEFAULT since 2026-09-29) enables
        # can_migrate_task()'s cache-hot refusal (fair.c:10291-10329,
        # 10817-10832) via _cache_hot_blocked() below, applied as a
        # candidate filter in every path that selects a queued task to
        # migrate (periodic, newidle, burst -- all share this balancer's
        # migrate_* methods). Pass cache_hot=False explicitly to
        # reproduce pre-Task-8 behavior exactly.
        self.cache_hot = cache_hot

        # TASK 8 FIX 5 (2026-09-29, docs/FIDELITY_AUDIT.md §8): False
        # keeps the legacy NUMA_IMBALANCE_MIN=32/NUMA_DST_BUSY_
        # THRESHOLD=2 constants AND keeps calling
        # _adjust_numa_imbalance() from the generic migrate_load path in
        # _balance_domain() (the pre-Task-8, if undocumented, behavior).
        # True (DEFAULT since 2026-09-29) switches _adjust_numa_
        # imbalance() to the corrected NUMA_IMBALANCE_MIN_KERNEL/
        # NUMA_DST_BUSY_THRESHOLD_KERNEL constants above AND stops
        # calling it from that generic path -- real calculate_imbalance()
        # (fair.c:12577-12753) only ever calls adjust_numa_imbalance()
        # from the group_has_spare branch (fair.c:12693), never from the
        # "both overloaded" migrate_load branch (fair.c:12718-12753) --
        # see _balance_domain() below. Pass numa_fix=False explicitly to
        # reproduce pre-Task-8 behavior exactly.
        self.numa_fix = numa_fix

    def _balance_state(self, core, domain):
        """Only meaningful when per_cpu_last_balance=True. One entry per
        (core, domain) pair actually visited by that core's own climb --
        never shared with any other core's climb through the same
        domain, unlike the legacy Domain.last_balance/.balance_interval
        this replaces."""
        key = (core.core_id, id(domain))
        state = self._per_cpu_balance_state.get(key)
        if state is None:
            state = {"last_balance": 0, "balance_interval": domain.min_interval}
            self._per_cpu_balance_state[key] = state
        return state

    # ---------------- hooks (no-op in baseline; used by burst-aware) ----------------

    def on_task_placed(self, core, now):
        pass

    def sample_queues(self, cores, now):
        pass

    # ---------------- group_classify()-lite ----------------

    def classify(self, group):
        return classify_group(group)

    def _find_group_containing(self, groups, core):
        for g in groups:
            members = g.cores() if hasattr(g, "cores") else [g]
            if core in members:
                return g
        return groups[0]

    # ---------------- should_we_balance()-lite ----------------

    def _is_core_idle(self, core):
        """Real is_core_idle(cpu): are ALL of this cpu's SMT siblings
        idle too, not just this one cpu -- core.parent is always the
        pair/SMT-level Domain for a raw Core."""
        return all(sib.is_idle() for sib in core.parent.cores())

    def _balance_mask(self, group):
        """build_balance_mask()-lite (topology.c:1199-1225): the subset
        of `group`'s span whose OWN home chain resolves to this exact
        group object -- excludes cores merely counted in `group`'s span
        as a reused ring neighbor (Domain.home_children), matching the
        kernel's cpumask_equal(sg_span, sibling->child span) check.
        `group` may be a raw Core (pair-level, degenerate single-CPU
        group) or a Domain."""
        if not isinstance(group, Domain):
            return [group]
        candidates = [c for c in group.cores() if group in domain_chain(c)]
        return candidates or [min(group.cores(), key=lambda c: c.core_id)]

    def _find_checker_kernel(self, domain, from_core):
        """should_we_balance()-lite (fair.c:13162-13221, v7.2): elect
        from the LOCAL GROUP containing `from_core` (env->sd->groups is
        always the asking cpu's own per-cpu-oriented first group),
        restricted to that group's real balance mask. First fully-idle
        CORE at levels without SD_SHARE_CPUCAPACITY (only the pair/SMT
        level has it here), else first idle-but-SMT-busy cpu, else
        group_balance_cpu() (lowest core_id in the mask)."""
        local_group = self._find_group_containing(domain.groups(), from_core)
        candidates = self._balance_mask(local_group)
        STATS.checker_calls += 1
        STATS.checker_cores_scanned += len(candidates)

        share_cpu_capacity = (domain.level == Domain.LEVEL_PAIR)
        idle_smt = None
        for c in candidates:
            if not c.is_idle():
                continue
            if not share_cpu_capacity and not self._is_core_idle(c):
                if idle_smt is None:
                    idle_smt = c
                continue
            return c
        if idle_smt is not None:
            return idle_smt
        return min(candidates, key=lambda c: c.core_id)

    def _find_checker(self, domain, from_core=None):
        # "legacy" (default), or the burst path's existing no-from_core
        # call (BurstScheduler.py, left unchanged): whole-span election,
        # byte-for-byte the original behavior.
        if self.checker_model != "kernel" or from_core is None:
            siblings = domain.cores()
            STATS.checker_calls += 1
            STATS.checker_cores_scanned += len(siblings)
            idle = [c for c in siblings if c.is_idle()]
            return idle[0] if idle else min(siblings, key=lambda c: c.core_id)
        return self._find_checker_kernel(domain, from_core)

    def is_designated_checker(self, domain, core):
        return self._find_checker(domain, core).core_id == core.core_id

    # ---------------- periodic pass ----------------

    def _get_balance_interval(self, base_interval, core):
        """
        TASK 8 FIX 2: get_sd_balance_interval(sd, cpu_busy) (fair.c:
        13565-13586). busy_factor multiplies the CHECK-TIME interval
        only -- never the persisted backoff state (periodic_balance()'s
        new_interval computation below uses the UNSCALED `interval`,
        matching real Linux: sd->balance_interval's own doubling/reset,
        fair.c:13507/13556-13557, is unaffected by busy_factor). -1 when
        busy (fair.c:13581-13582): real Linux applies this in jiffies
        AFTER the busy_factor multiply, a ~1-jiffy anti-alignment offset
        to desynchronize near-multiple domain periods -- this sim's "ms"
        units already map 1:1 to jiffies elsewhere (sysctl_sched_
        migration_cost=0.5ms, Task.last_ran_until's threshold), so the
        same -1 is applied here, floored at 1. msecs_to_jiffies
        (fair.c:13574) is a no-op at that 1:1 mapping. max_load_balance_
        interval (fair.c:13584, = HZ*ncpus/10, ~3200 "ms" for this
        32-core topology at the implicit HZ=1000 this sim already
        assumes elsewhere) is NOT implemented: every domain's own
        max_interval here (<=64) is already far below it -- a structural
        no-op at this scale, documented rather than coded (docs/
        FIDELITY_AUDIT.md §1).
        """
        if self.busy_factor == 1 or core.is_idle():
            return base_interval
        return max(base_interval * self.busy_factor - 1, 1)

    def periodic_balance(self, core, now):
        STATS.periodic_calls += 1
        d = core.parent
        while d is not None:
            STATS.periodic_levels_walked += 1
            # FIX A (see __init__/Readme.md): per_cpu_last_balance=True
            # reads/writes THIS core's own (core, domain) state instead
            # of the domain-shared fields, matching real Linux's per-cpu
            # sd copies. Legacy (default) path unchanged byte-for-byte.
            if self.per_cpu_last_balance:
                state = self._balance_state(core, d)
                last_balance, interval = state["last_balance"], state["balance_interval"]
            else:
                last_balance, interval = d.last_balance, d.balance_interval

            # ADDED 2026-09-24: checked against balance_interval, not the
            # static min_interval -- see the backoff adjustment below.
            # TASK 8 FIX 2: `interval` itself (used below for the
            # persisted backoff state) stays UNSCALED; only the
            # comparison uses the busy_factor-scaled check_interval.
            check_interval = self._get_balance_interval(interval, core)
            if now - last_balance >= check_interval:
                if self.per_cpu_last_balance:
                    state["last_balance"] = now
                else:
                    d.last_balance = now
                if self.is_designated_checker(d, core):
                    if d.is_numa:
                        # verified: try_acquire, non-blocking -- if another
                        # core is already mid-NUMA-pass, bail immediately
                        if self._numa_balance_locked:
                            d = d.parent
                            continue
                        self._numa_balance_locked = True
                        try:
                            n = self._balance_domain(d, core, now)
                        finally:
                            self._numa_balance_locked = False
                    else:
                        n = self._balance_domain(d, core, now)
                    # verified: sd_init()'s busy_factor/max_interval backoff
                    # -- a domain that keeps finding nothing to fix gets
                    # checked less often (doubling, capped at max_interval,
                    # previously computed and never read -- see Topology.py);
                    # one that actually migrates something gets checked at
                    # full frequency again. Only adjusted when a check was
                    # actually attempted (this core was the designated
                    # checker and, for NUMA domains, won the lock) -- not
                    # adjusted just because this core wasn't the one asking,
                    # which is a different reason for no migration entirely.
                    new_interval = d.min_interval if n else min(interval * 2, d.max_interval)
                    if self.per_cpu_last_balance:
                        state["balance_interval"] = new_interval
                    else:
                        d.balance_interval = new_interval
            d = d.parent

    def _balance_domain(self, domain, local_core, now, tag="periodic"):
        groups = domain.groups()
        if len(groups) < 2:
            return 0

        stats = {id(g): self.classify(g) for g in groups}

        local_group = self._find_group_containing(groups, local_core)
        local_type, local_load, local_cap, local_idle, local_run = stats[id(local_group)]

        candidates = [g for g in groups if g is not local_group]
        busiest = max(candidates, key=lambda g: stats[id(g)][1])
        b_type, b_load, b_cap, b_idle, b_run = stats[id(busiest)]

        if b_type < local_type:
            return 0  # local isn't clearly worse off -> balanced (line 12973 analog)

        domain_load = sum(s[1] for s in stats.values())
        domain_cap = sum(s[2] for s in stats.values())
        domain_avg = domain_load * CAPACITY_SCALE / domain_cap
        local_avg = local_load * CAPACITY_SCALE / local_cap
        busiest_avg = b_load * CAPACITY_SCALE / b_cap

        if local_type == GROUP_OVERLOADED:
            if local_avg >= busiest_avg:
                return 0
            if local_avg >= domain_avg:
                return 0
            if 100 * busiest_avg <= domain.imbalance_pct * local_avg:
                return 0  # verified imbalance_pct conservatism check

        if local_type == GROUP_HAS_SPARE:
            if self.imbalance_model == "kernel":
                # FIX C: real Linux's has-spare block ALWAYS handles
                # busiest internally (fair.c:12642-12703) -- never falls
                # through to the generic min()-trick below, regardless
                # of busiest's type. Matches that here: this branch
                # returns unconditionally, unlike legacy below.
                return self._balance_has_spare_kernel(
                    domain, local_core, local_group, busiest, b_type,
                    local_idle, b_idle, local_run, b_run, now, tag,
                )
            if b_type != GROUP_OVERLOADED:
                imbalance_tasks = max(0, (local_idle - b_idle)) // 2  # verified >>=1 halving
                if imbalance_tasks <= 0:
                    return 0
                return self._migrate_tasks(busiest, local_core, imbalance_tasks, now, tag, domain)
            # legacy + busiest overloaded: falls through to the generic
            # min()-trick below -- this is the mismatch Fix C corrects
            # (see Readme.md: that formula's real use is overloaded-vs-
            # overloaded, not has-spare-vs-overloaded).

        # migrate_load-style sizing: verified min() trick
        imbalance = min(
            (busiest_avg - domain_avg) * b_cap,
            (domain_avg - local_avg) * local_cap,
        ) / CAPACITY_SCALE

        # TASK 8 FIX 5: real calculate_imbalance() (fair.c:12577-12753)
        # never calls adjust_numa_imbalance() from this "both overloaded"
        # generic migrate_load branch -- ONLY from the group_has_spare
        # branch (fair.c:12693, ported in _balance_has_spare_kernel()
        # below). numa_fix=False keeps the legacy (if undocumented)
        # behavior of calling it here too; numa_fix=True matches real
        # Linux by skipping it.
        if domain.is_numa and not self.numa_fix:
            imbalance = self._adjust_numa_imbalance(imbalance, local_run)
        if imbalance <= 0:
            return 0
        if 100 * busiest_avg <= domain.imbalance_pct * local_avg:
            return 0  # same imbalance_pct conservatism check as the overloaded path

        return self._migrate_load(busiest, local_core, imbalance, now, tag, domain)

    def _balance_has_spare_kernel(self, domain, local_core, local_group, busiest, b_type,
                                   local_idle, b_idle, local_run, b_run, now, tag):
        """
        FIX C (2026-09-27, see Readme.md and LoadBalancer.__init__):
        calculate_imbalance()'s actual local_type==group_has_spare
        branch (fair.c:12642-12703, kernel v7.2) -- three sub-cases keyed
        on busiest, not the single formula "legacy" mode always used:

          1. busiest overloaded AND domain doesn't share an LLC
             (Domain.share_llc) -> migrate_util (fair.c:12643-12670),
             see _migrate_util() below.
          2. busiest->group_weight==1 (always true at this sim's pair
             level -- each "group" there IS one core) OR prefer_sibling
             -> migrate_task sized by sibling_imbalance()
             (fair.c:12672-12678, 11644-11677): busiest_nr_running -
             local_nr_running (equal-sized-groups case only -- this
             topology never has unequal sibling group sizes at any one
             level, so sibling_imbalance()'s unequal-cores normalization
             branch is never reached and isn't ported), plus the "take
             advantage of an empty sched group" nudge (fair.c:11671-11674).
          3. Otherwise -> migrate_task sized by idle-cpu-count diff
             (fair.c:12681-12687) -- the ONE branch "legacy" mode already
             had.

        prefer_sibling (fair.c:12551-12552, "the flags of a sched group
        are those of the child domain") is approximated here as "the
        busiest group's own level isn't a NUMA level" (`not
        busiest.is_numa`) -- this simulator has no separate sched_group-
        vs-child-domain distinction to look up more literally, but this
        matches sd_init()'s actual behavior (topology.c ~2013: NUMA
        levels strip SD_PREFER_SIBLING from their OWN flags; nothing else
        strips it) for every level in this topology.

        All three sub-cases get the SAME NUMA adjustment (fair.c:12690-
        12697, only sub-cases 2/3 -- migrate_util has no NUMA step in the
        source) and final >>=1 halving (fair.c:12700) that "legacy" mode
        already applied to its one branch.
        """
        if b_type == GROUP_OVERLOADED and not domain.share_llc:
            return self._migrate_util(busiest, local_group, local_core, now, tag, domain)

        busiest_weight_one = not hasattr(busiest, "cores")  # raw Core group == pair level
        prefer_sibling = not getattr(busiest, "is_numa", False)
        if busiest_weight_one or prefer_sibling:
            raw = max(0, b_run - local_run)
            if raw <= 1 and local_run == 0 and b_run > 1:
                raw = 2  # fair.c:11671-11674
        else:
            raw = max(0, local_idle - b_idle)

        if domain.is_numa:
            raw = self._adjust_numa_imbalance(raw, local_run)

        imbalance_tasks = raw // 2
        if imbalance_tasks <= 0:
            return 0
        return self._migrate_tasks(busiest, local_core, imbalance_tasks, now, tag, domain)

    def _migrate_util(self, busiest_group, local_group, dst_core, now, tag, domain=None):
        """
        FIX C, branch 1 of _balance_has_spare_kernel(): fill local's
        spare capacity when busiest is overloaded and the domain doesn't
        share an LLC (fair.c:12643-12670, v7.2). Sized in util_avg units
        (Core.util_avg / Task.util_avg -- see those files' Fix C
        comments), NOT weight/load: `imbalance = max(local_capacity,
        local_util) - local_util`. Each candidate task costs its OWN
        util_avg (near-zero for a task that's never run --
        Core._initial_util_avg(), port of post_init_entity_util_avg(),
        fair.c:1315-1351) rather than a static weight -- this is WHY real
        Linux can move many fresh queued tasks in one pass here: they're
        each almost free in utilization terms until they actually run.

        Simplification vs detach_tasks() (fair.c:10968-10975): real Linux
        SKIPS a candidate whose own cost exceeds the remaining budget and
        tries the NEXT one; this stops at the first such task instead.
        Not expected to matter for the scenarios this was built against
        (every candidate is a never-run task with near-zero util_avg),
        but flagged as an explicit, undocumented-as-exact simplification.
        """
        local_cores = local_group.cores() if hasattr(local_group, "cores") else [local_group]
        local_util = sum(c.util_avg for c in local_cores)
        local_cap = len(local_cores) * CAPACITY_SCALE
        imbalance = max(local_cap, local_util) - local_util

        busiest_cores = busiest_group.cores() if hasattr(busiest_group, "cores") else [busiest_group]
        busiest_cores = [c for c in busiest_cores if c.rq]
        if not busiest_cores:
            return 0
        src_core = max(busiest_cores, key=lambda c: c.load())

        force_one = False
        if imbalance <= 0:
            # fair.c:12660-12667 -- local is (newly) idle but the
            # computed budget rounded to <=0; still try to pull ONE
            # waiting task rather than give up entirely.
            if not dst_core.is_idle():
                return 0
            force_one = True

        moved_util = 0.0
        n_migrated = 0
        loop_max = MAX_MIGRATE_PER_PASS
        while src_core.rq and n_migrated < loop_max:
            if dst_core.is_idle() and len(src_core.rq) <= 1:
                break
            if not force_one and moved_util >= imbalance:
                break
            # TASK 8 FIX 4: filter cache-hot candidates before picking --
            # can_migrate_task() (fair.c:10291-10329, 10817-10832).
            pool = self._filter_cache_hot(src_core.rq, src_core, domain, now)
            if not pool:
                break
            task = pool[0]
            self._do_migrate(task, src_core, dst_core, now, tag)
            moved_util += task.util_avg
            n_migrated += 1
            if force_one:
                break
        return n_migrated

    def _cache_hot_blocked(self, task, src_core, domain, now):
        """
        TASK 8 FIX 4 (2026-09-29, docs/FIDELITY_AUDIT.md §7): can_migrate_
        task()'s cache-hot gate (fair.c:10291-10329, condition at
        10817-10832: `if (!hot || nr_balance_failed > cache_nice_tries)
        return 1 (allow);`). A task is hot if
        `now - task.last_ran_until < CACHE_HOT_THRESHOLD_MS` --
        never-run tasks (last_ran_until is None) are never hot, matching
        __sched_fork()'s p->se.exec_start=0 at fork (core.c:4568), only
        overwritten by update_stats_curr_start() (fair.c:2150-2156) once
        a task actually runs. Bypassed once this (src_core, domain
        level)'s failure count exceeds cache_nice_tries (real per-level
        default, topology.c:2002-2023: pair=0, node=1, onehop/machine=2,
        Topology.Domain.cache_nice_tries). Real Linux's other two
        bypasses -- active balance, NUMA-preferred destination -- are
        not modeled; this sim has neither concept.
        """
        if not self.cache_hot or domain is None:
            return False
        if task.last_ran_until is None:
            return False
        if (now - task.last_ran_until) >= CACHE_HOT_THRESHOLD_MS:
            return False
        failed = self.nr_balance_failed.get(id(src_core), 0)
        return failed <= domain.cache_nice_tries

    def _filter_cache_hot(self, tasks, src_core, domain, now):
        """Candidate pool with cache-hot (and not bypassed) tasks
        removed, preserving queue order. A no-op (returns `tasks`
        unchanged) when cache_hot=False -- the default -- so every
        existing call site/positional caller that predates this
        parameter is unaffected."""
        if not self.cache_hot:
            return tasks
        return [t for t in tasks if not self._cache_hot_blocked(t, src_core, domain, now)]

    def _adjust_numa_imbalance(self, imbalance, dst_running):
        """
        Verified two-gate logic from adjust_numa_imbalance() (fair.c:
        2179-2198):
          Gate 1: destination already busy enough -> skip forgiveness,
                  return imbalance unchanged.
          Gate 2: (only reached if Gate 1 doesn't apply) small imbalance
                  -> forgive it entirely, protecting a communicating pair
                  of tasks that should stay local.
        TASK 8 FIX 5 (2026-09-29): numa_fix=False (default) keeps the
        legacy NUMA_IMBALANCE_MIN/NUMA_DST_BUSY_THRESHOLD stand-ins.
        numa_fix=True switches to the verified constants
        (NUMA_IMBALANCE_MIN_KERNEL=2, fair.c:2177;
        NUMA_DST_BUSY_THRESHOLD_KERNEL=3, this topology's own imb_numa_nr
        derivation, topology.c:2870-2934 -- see the module-level comment
        above those constants).
        """
        min_threshold = NUMA_IMBALANCE_MIN_KERNEL if self.numa_fix else NUMA_IMBALANCE_MIN
        busy_threshold = NUMA_DST_BUSY_THRESHOLD_KERNEL if self.numa_fix else NUMA_DST_BUSY_THRESHOLD
        if dst_running > busy_threshold:
            return imbalance
        if imbalance <= min_threshold:
            return 0
        return imbalance

    def _migrate_load(self, busiest_group, dst_core, imbalance, now, tag, domain=None):
        busiest_cores = busiest_group.cores() if hasattr(busiest_group, "cores") else [busiest_group]
        busiest_cores = [c for c in busiest_cores if c.rq]
        if not busiest_cores:
            return 0
        src_core = max(busiest_cores, key=lambda c: c.load())

        # self-relaxing tiny-task skip (verified: load < 16 unless nr_balance_failed)
        key = id(src_core)
        failed = self.nr_balance_failed.get(key, 0)
        min_task_weight = 16 if failed == 0 else max(1, 16 >> min(failed, 4))

        moved = 0.0
        n_migrated = 0
        loop_max = MAX_MIGRATE_PER_PASS
        while src_core.rq and moved < imbalance and n_migrated < loop_max:
            # anti-livelock: destination idle + this would empty the source -> stop
            if dst_core.is_idle() and len(src_core.rq) <= 1:
                break
            # TASK 8 FIX 4: filter cache-hot candidates first, THEN apply
            # the existing tiny-task-weight preference within what's left.
            pool = self._filter_cache_hot(src_core.rq, src_core, domain, now)
            if not pool:
                break
            candidates = [t for t in pool if t.weight >= min_task_weight] or pool
            task = max(candidates, key=lambda t: t.weight)
            self._do_migrate(task, src_core, dst_core, now, tag)
            moved += task.weight
            n_migrated += 1

        if moved < imbalance:
            self.nr_balance_failed[key] = failed + 1
        else:
            self.nr_balance_failed[key] = 0
        return n_migrated

    def _migrate_tasks(self, busiest_group, dst_core, n_tasks, now, tag, domain=None):
        busiest_cores = busiest_group.cores() if hasattr(busiest_group, "cores") else [busiest_group]
        busiest_cores = [c for c in busiest_cores if c.rq]
        if not busiest_cores:
            return 0
        src_core = max(busiest_cores, key=lambda c: len(c.rq))

        moved = 0
        loop_max = MAX_MIGRATE_PER_PASS
        while src_core.rq and moved < n_tasks and moved < loop_max:
            if dst_core.is_idle() and len(src_core.rq) <= 1:
                break
            # TASK 8 FIX 4: filter cache-hot candidates before peeking.
            pool = self._filter_cache_hot(src_core.rq, src_core, domain, now)
            if not pool:
                break
            task = pool[0]
            self._do_migrate(task, src_core, dst_core, now, tag)
            moved += 1
        return moved

    def _do_migrate(self, task, src_core, dst_core, now, tag="periodic"):
        src_core.rq.remove(task)
        # rebase vruntime: subtract old core's baseline, add new core's (verified fix)
        # rebase against avg_vruntime (V), consistent with the corrected
        # EEVDF baseline used everywhere else -- not min_vruntime
        task.vruntime = task.vruntime - src_core.avg_vruntime() + dst_core.avg_vruntime()
        task.prev_core = src_core.core_id
        task.migrations += 1
        # TASK 9a: "ran_only" skips the charge for a task that has never
        # run (task.last_ran_until is None) -- see __init__'s
        # penalty_model comment for the kernel citation. "all" (default)
        # is the exact pre-Task-9 unconditional charge.
        if self.migration_penalty and (self.penalty_model == "all" or task.last_ran_until is not None):
            task.remaining_time += self.migration_penalty
            self.penalty_charges += 1
        dst_core.rq.append(task)
        self.migrations += 1
        if self.logger:
            self.logger.log(now, "migration", task_id=task.task_id,
                             src=src_core.core_id, dst=dst_core.core_id, trigger=tag)

    # ---------------- newly-idle path ----------------

    def try_newidle(self, core, now):
        STATS.newidle_calls += 1
        d = core.parent
        while d is not None:
            STATS.newidle_levels_walked += 1
            if self.newidle_mode == "transition":
                # No random gate -- Core.run() already ensures this is
                # called at most once per busy->idle transition, which is
                # the actual gate in this mode.
                if self._newidle_attempt(d, core, now):
                    return True
            else:
                if self._newidle_rng.random() < core.newidle_cost_avg:
                    success = self._newidle_attempt(d, core, now)
                    core.newidle_cost_avg = 0.9 * core.newidle_cost_avg + 0.1 * (1.0 if success else 0.0)
                    if success:
                        return True
            d = d.parent
        return False

    def _newidle_attempt(self, domain, core, now):
        groups = domain.groups()
        if len(groups) < 2:
            return False
        local_group = self._find_group_containing(groups, core)
        candidates = [g for g in groups if g is not local_group]
        busiest = max(candidates, key=lambda g: self.classify(g)[1])
        busiest_cores = busiest.cores() if hasattr(busiest, "cores") else [busiest]
        busiest_cores = [c for c in busiest_cores if c.rq]
        if not busiest_cores:
            return False
        src = max(busiest_cores, key=lambda c: c.load())
        # FIXED 2026-09-24 (was src.rq[-1], the newest-queued task): real
        # Linux's detach_one_task() always walks rq->cfs_tasks from the
        # TAIL (list_for_each_entry_reverse), i.e. prefers whichever task
        # has been waiting LONGEST -- a free proxy for "least likely to
        # be cache-hot," since it's had the most time to go cold. Same
        # policy for periodic-triggered and newidle-triggered migration
        # in real Linux; both funnel through detach_tasks(). Our rq is
        # append()-ordered (opposite physical end from real Linux's
        # list_add-at-head), so "oldest still queued" is src.rq[0], not
        # src.rq[-1] -- rq[-1] was picking the NEWEST task, the opposite
        # of real Linux's preference, and inconsistent with
        # _migrate_tasks()'s src_core.rq[0] elsewhere in this file,
        # which already got this right.
        # TASK 8 FIX 4: filter cache-hot candidates before peeking --
        # same can_migrate_task() gate as the periodic/burst paths above.
        pool = self._filter_cache_hot(src.rq, src, domain, now)
        if not pool:
            return False
        task = pool[0]  # peek; _do_migrate performs the actual removal
        self._do_migrate(task, src, core, now, "newidle")
        return True