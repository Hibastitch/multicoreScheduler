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
                 imbalance_model="kernel", burst_resets_timer=False, **detector_kwargs):
        super().__init__(machine_domain, cores_by_id, migration_penalty=migration_penalty,
                          newidle_mode=newidle_mode, seed=seed,
                          per_cpu_last_balance=per_cpu_last_balance,
                          imbalance_model=imbalance_model)
        self.detector = BurstDetector(**detector_kwargs)
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

        for domain in domain_chain(core):
            STATS.burst_balance_levels_walked += 1
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