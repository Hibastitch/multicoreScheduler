"""
The research contribution: everything else in this simulator (placement,
periodic balancing, newly-idle balancing) is the verified baseline. This
class adds exactly one new mechanism on top of it -- the thing the
verified pipeline structurally lacks: a push signal from "a burst just
landed here" to "rebalance now," instead of waiting for the next
periodic interval or a core happening to go idle nearby.
"""

from LoadBalancer import LoadBalancer
from Topology import domain_chain, STATS
from BurstDetector import BurstDetector


class BurstAwareLoadBalancer(LoadBalancer):
    """
    Same balancing logic as the verified baseline (_balance_domain is
    inherited, unchanged). The only addition: BurstDetector fires from
    observable signals alone, and when it does, we walk the domain
    chain immediately instead of waiting for the periodic interval.
    """

    # FINAL CONFIGURATION (2026-09-27i, see Readme.md): mirrors
    # LoadBalancer's defaults (transition/True/kernel) plus the
    # detector-calibration result forwarded via **detector_kwargs to
    # BurstDetector (arrival_rate_threshold=1.5, combine="and" --
    # see BurstDetector.py). Pass the legacy values explicitly (and
    # arrival_rate_threshold=0.8, combine="or") to reproduce
    # pre-2026-09-27i behavior.
    def __init__(self, machine_domain, cores_by_id, logger=None, migration_penalty=0.0,
                 newidle_mode="transition", seed=0, per_cpu_last_balance=True,
                 imbalance_model="kernel", checker_model="kernel",
                 busy_factor=16, cache_hot=True, numa_fix=True,
                 penalty_model="all", burst_gap_gate=False, burst_idle_check=False,
                 burst_resets_timer=False, **detector_kwargs):
        # TASK 8 (2026-09-29, NOTEBOOK.md 2026-09-29c/d): busy_factor/
        # cache_hot/numa_fix forwarded straight through to LoadBalancer --
        # they affect the SHARED _balance_domain()/migrate_*() pipeline
        # this class's on_task_placed() calls into (see FIDELITY_AUDIT.md
        # §13), same as periodic/newidle. DEFAULTS CHANGED 2026-09-29 to
        # match LoadBalancer's own new defaults (16/True/True) --
        # pass busy_factor=1, cache_hot=False, numa_fix=False explicitly
        # to reproduce pre-Task-8 behavior. Note: busy_factor only ever
        # matters for periodic_balance()'s own interval check, which this
        # class never calls -- forwarded here only so the SAME balancer
        # instance's `self.busy_factor` attribute exists and is
        # consistent, not because the burst trigger itself uses it.
        # checker_model also defaults to "kernel" now for consistency,
        # but (FIDELITY_AUDIT.md §13, §3) has NO effect on the burst
        # path's own checker election -- on_task_placed() calls
        # _find_checker(domain) with no from_core (BurstScheduler.py
        # below), which always takes the legacy whole-span-election
        # branch regardless of this setting. Unchanged, by design (see
        # NOTEBOOK.md 2026-09-29c's pre-registration: "Burst path:
        # UNCHANGED").
        super().__init__(machine_domain, cores_by_id, migration_penalty=migration_penalty,
                          newidle_mode=newidle_mode, seed=seed,
                          per_cpu_last_balance=per_cpu_last_balance,
                          imbalance_model=imbalance_model,
                          checker_model=checker_model,
                          busy_factor=busy_factor, cache_hot=cache_hot, numa_fix=numa_fix,
                          penalty_model=penalty_model)
        self.detector = BurstDetector(**detector_kwargs)

        # TASK 9b (2026-09-29, docs/NOTEBOOK.md 2026-09-29h pre-
        # registration, motivated by the Task 8 v2 grid finding every
        # threshold config harmful on bursty_high_s64 at penalty=2ms):
        # False (default, exact pre-Task-9 behavior) balances every
        # domain in the burst chain unconditionally, using
        # _find_checker(domain)'s legacy whole-span election (first idle
        # core, else lowest core_id) as BOTH the acting checker and the
        # balance destination. True requires the destination to be the
        # explicitly least-loaded core in the domain AND at least 2
        # nr_running (queued+running, Core.running_count()) behind the
        # burst core before balancing that domain at all -- see
        # on_task_placed() below for the exact gate and
        # self.gate_skipped_domains for the new per-run counter this adds.
        self.burst_gap_gate = burst_gap_gate
        self.gate_skipped_domains = 0

        # TASK 10 (2026-09-30, NOTEBOOK.md 2026-09-30d pre-registration,
        # motivated by the 2026-09-30c finding that burst_gap_gate's
        # DESTINATION change -- not its gap check -- best explains v3's
        # bursty_high_s64 harm, and by the 2026-09-30b Task 10a
        # diagnostic finding that "any core idle machine-wide" cleanly
        # discriminates bursty_high_s64 from the other workloads while
        # gap-based checks almost never fire): False (default, exact
        # pre-Task-10 behavior) is unaffected by this flag. True adds
        # exactly ONE check, evaluated ONCE per trigger (not per domain
        # like burst_gap_gate) right before the domain_chain walk below:
        # if no core anywhere on the machine is idle, skip the walk
        # entirely (all levels); otherwise run it EXACTLY as the
        # ungated code already does -- same _find_checker() election,
        # same _balance_domain() call, no destination change of any
        # kind. Orthogonal to burst_gap_gate (both can be set; v4 uses
        # this flag alone). See self.idle_check_skipped_triggers below
        # for the new per-run counter, and LoadBalancer.__init__'s
        # burst_migrations_dst_busy for a related one (this check only
        # asks whether SOME core is idle, not whether the SPECIFIC
        # destination _find_checker() picks within a given domain is).
        self.burst_idle_check = burst_idle_check
        self.idle_check_skipped_triggers = 0
        # Measurement only (2026-10-01, docs/NOTEBOOK.md), no behavior
        # change: idle_check_runs counts how many times the scan below
        # actually ran; idle_check_cores_read counts the real number of
        # Core.is_idle() calls made (the scan stops at the first idle
        # core, same as the any() it replaces -- see on_task_placed()).
        # Deliberately NOT added to sched_cores_scanned or any other
        # Topology.WorkStats field -- those exist for the shared
        # baseline pipeline (classify_group()/_find_checker()); this is
        # a new, separate site, kept separate so the published
        # sched_cores_scanned numbers are unaffected.
        self.idle_check_runs = 0
        self.idle_check_cores_read = 0
        self.logger = logger
        self.burst_triggers = 0
        # Pre-registered design ablation (2026-09-27, see Readme.md).
        # True = original behavior -- a successful burst-triggered
        # migration resets the SAME (checker, domain) last_balance
        # periodic_balance() reads (or the shared Domain field,
        # pre-Fix-A). False (DEFAULT since 2026-09-27d) = the burst path
        # moves tasks but does NOT touch last_balance at all. The n=30
        # ablation found the reset causes roughly HALF of stacked_burst/
        # low's harm on top of batch-splitting's own share (both
        # mechanisms independently confirmed real -- see the
        # 2026-09-27b/c ledger entries), while costing negligible
        # medium/high benefit -- so False is now the default; pass
        # True explicitly to reproduce the original behavior.
        self.burst_resets_timer = burst_resets_timer
        self._cooldown = {}  # id(pair_domain) -> last trigger time, avoid re-firing every tick

        # Task 4 funnel counters (2026-09-26), see Readme.md: detector_fires
        # (raw BurstDetector.is_burst()==True, BEFORE the cooldown gate) >=
        # burst_balance_attempts (post-cooldown, self.burst_triggers under
        # another name -- kept as a property below rather than duplicate
        # state) >= burst_triggered_migrations (actual task-count moved by
        # those attempts, summed across every domain level's
        # _balance_domain() call). Distinguishes "the detector saw
        # something" from "we actually acted on it" from "acting on it
        # actually moved anything."
        self.detector_fires = 0
        self.detector_fire_times = []
        self.burst_triggered_migrations = 0

    @property
    def burst_balance_attempts(self):
        return self.burst_triggers

    def on_task_placed(self, core, now):
        pair_domain = core.parent
        if pair_domain is None:
            return

        STATS.burst_check_calls += 1
        self.detector.record_arrival(pair_domain, now)
        triggered, rate, growth = self.detector.is_burst(pair_domain, core)

        if self.logger:
            self.logger.log(now, "burst_check", core=core.core_id,
                             arrival_rate=rate, queue_growth=growth, triggered=triggered)

        if not triggered:
            return

        self.detector_fires += 1
        self.detector_fire_times.append(now)

        last = self._cooldown.get(id(pair_domain), -1e9)
        if now - last < self.detector.arrival_window:
            return  # already handled this burst, don't re-trigger every arrival
        self._cooldown[id(pair_domain)] = now

        self.burst_triggers += 1
        if self.logger:
            self.logger.log(now, "burst_trigger", core=core.core_id,
                             arrival_rate=rate, queue_growth=growth)

        # TASK 10: single machine-wide check, evaluated once for this
        # trigger, BEFORE any domain is walked -- see __init__'s
        # burst_idle_check comment. No effect at all when the flag is
        # False (the default).
        #
        # Measurement only (2026-10-01): an explicit loop replacing the
        # original any(c.is_idle() for c in self.cores_by_id.values()),
        # so idle_check_cores_read can count the real number of reads --
        # same iteration order (self.cores_by_id.values()), same
        # short-circuit on the first idle core, so found_idle is
        # identical to what any(...) would have returned; nothing about
        # WHICH cores get scanned or WHEN the walk below runs changes.
        if self.burst_idle_check:
            self.idle_check_runs += 1
            found_idle = False
            for c in self.cores_by_id.values():
                self.idle_check_cores_read += 1
                if c.is_idle():
                    found_idle = True
                    break
            if not found_idle:
                self.idle_check_skipped_triggers += 1
                return

        for domain in domain_chain(core):
            STATS.burst_balance_levels_walked += 1
            if self.burst_gap_gate:
                # TASK 9b: dst = least-loaded OTHER core in this domain
                # (nr_running = queued+running, Core.running_count();
                # ties -> lowest core_id). Skip the domain entirely
                # unless src (the burst core) is at least 2 nr_running
                # ahead of dst -- with a gap of 1, dst finishes its own
                # extra task in the same time src would have reached the
                # moved task, so moving it cannot finish it any earlier
                # (pure migration overhead, including the Task 9a
                # penalty when one applies, for zero benefit). dst
                # becomes the local_core _balance_domain() compares
                # against, replacing the legacy checker as the balance
                # destination.
                others = [c for c in domain.cores() if c is not core]
                if not others:
                    continue
                dst = min(others, key=lambda c: (c.running_count(), c.core_id))
                gap = core.running_count() - dst.running_count()
                if gap < 2:
                    self.gate_skipped_domains += 1
                    continue
                checker = dst
            else:
                checker = self._find_checker(domain)
            n = self._balance_domain(domain, checker, now, tag="burst")
            if n:
                self.burst_triggered_migrations += n
                # FIX A (see Readme.md, LoadBalancer.__init__): update the
                # SAME (checker, domain) state periodic_balance() reads,
                # not the shared Domain field, when per_cpu_last_balance
                # is on -- otherwise a burst-triggered reset here would
                # silently go back to being globally shared even in "per
                # -cpu" mode. Skipped entirely when burst_resets_timer is
                # False (the design ablation above).
                if self.burst_resets_timer:
                    if self.per_cpu_last_balance:
                        self._balance_state(checker, domain)["last_balance"] = now
                    else:
                        domain.last_balance = now
                if self.logger:
                    self.logger.log(now, "burst_migration_effective", domain=domain.name, n=n)

    def sample_queues(self, cores, now):
        """Call once per sim tick so the queue-growth signal has data to work with."""
        for c in cores:
            self.detector.record_queue_sample(c, now)