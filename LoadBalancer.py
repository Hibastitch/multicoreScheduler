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

from Topology import GROUP_HAS_SPARE, GROUP_OVERLOADED, CAPACITY_SCALE, classify_group, STATS

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
                 imbalance_model="kernel"):
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

    def _find_checker(self, domain):
        siblings = domain.cores()
        STATS.checker_calls += 1
        STATS.checker_cores_scanned += len(siblings)
        idle = [c for c in siblings if c.is_idle()]
        return idle[0] if idle else min(siblings, key=lambda c: c.core_id)

    def is_designated_checker(self, domain, core):
        return self._find_checker(domain).core_id == core.core_id

    # ---------------- periodic pass ----------------

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
            if now - last_balance >= interval:
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
                return self._migrate_tasks(busiest, local_core, imbalance_tasks, now, tag)
            # legacy + busiest overloaded: falls through to the generic
            # min()-trick below -- this is the mismatch Fix C corrects
            # (see Readme.md: that formula's real use is overloaded-vs-
            # overloaded, not has-spare-vs-overloaded).

        # migrate_load-style sizing: verified min() trick
        imbalance = min(
            (busiest_avg - domain_avg) * b_cap,
            (domain_avg - local_avg) * local_cap,
        ) / CAPACITY_SCALE

        if domain.is_numa:
            imbalance = self._adjust_numa_imbalance(imbalance, local_run)
        if imbalance <= 0:
            return 0
        if 100 * busiest_avg <= domain.imbalance_pct * local_avg:
            return 0  # same imbalance_pct conservatism check as the overloaded path

        return self._migrate_load(busiest, local_core, imbalance, now, tag)

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
            return self._migrate_util(busiest, local_group, local_core, now, tag)

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
        return self._migrate_tasks(busiest, local_core, imbalance_tasks, now, tag)

    def _migrate_util(self, busiest_group, local_group, dst_core, now, tag):
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
            task = src_core.rq[0]
            self._do_migrate(task, src_core, dst_core, now, tag)
            moved_util += task.util_avg
            n_migrated += 1
            if force_one:
                break
        return n_migrated

    def _adjust_numa_imbalance(self, imbalance, dst_running):
        """
        Verified two-gate logic from adjust_numa_imbalance():
          Gate 1: destination already busy enough -> skip forgiveness,
                  return imbalance unchanged.
          Gate 2: (only reached if Gate 1 doesn't apply) small imbalance
                  -> forgive it entirely, protecting a communicating pair
                  of tasks that should stay local.
        Thresholds (NUMA_IMBALANCE_MIN, NUMA_DST_BUSY_THRESHOLD) are our
        own stand-ins -- the real numeric values were never opened this
        session, so treat these as approximations, not verified numbers.
        """
        if dst_running > NUMA_DST_BUSY_THRESHOLD:
            return imbalance
        if imbalance <= NUMA_IMBALANCE_MIN:
            return 0
        return imbalance

    def _migrate_load(self, busiest_group, dst_core, imbalance, now, tag):
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
            candidates = [t for t in src_core.rq if t.weight >= min_task_weight] or src_core.rq
            task = max(candidates, key=lambda t: t.weight)
            self._do_migrate(task, src_core, dst_core, now, tag)
            moved += task.weight
            n_migrated += 1

        if moved < imbalance:
            self.nr_balance_failed[key] = failed + 1
        else:
            self.nr_balance_failed[key] = 0
        return n_migrated

    def _migrate_tasks(self, busiest_group, dst_core, n_tasks, now, tag):
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
            task = src_core.rq[0]
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
        if self.migration_penalty:
            task.remaining_time += self.migration_penalty
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
        task = src.rq[0]  # peek; _do_migrate performs the actual removal
        self._do_migrate(task, src, core, now, "newidle")
        return True