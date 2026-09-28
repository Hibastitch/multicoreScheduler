"""
Detects bursts from two OBSERVABLE signals only:

  1. Sliding-window arrival rate per pair-level domain (tasks/ms).
  2. Per-core runnable-queue growth rate (d(len(rq))/dt, sampled
     periodically, smoothed over a short window).

This module never sees the workload generator's ground-truth burst
intervals -- that ground truth exists ONLY in workload_generator.py's
`ground_truth_bursts` list, used exclusively by the evaluation harness
to compute burst response latency after the fact. If you ever find
yourself passing `ground_truth_bursts` into this class, that defeats
the point of the experiment.
"""

from collections import deque


class BurstDetector:
    def __init__(
        self,
        arrival_window=5,       # ms, sliding window for arrival-rate signal
        arrival_rate_threshold=1.5,  # tasks/ms within the window
        queue_window=5,         # ms, sliding window for queue-growth signal
        queue_growth_threshold=2.0,  # queue-length increase over the window
        combine="and",          # "and" (DEFAULT since 2026-09-27i) or "or" (original)
    ):
        # FINAL CONFIGURATION (2026-09-27i, see Readme.md): a pre-
        # registered 12-config grid (2026-09-27g) found arrival_rate_
        # threshold=1.5 + combine="and" harm-free and the largest
        # stacked_burst medium/high p95_wait reduction among harm-free
        # options. A follow-up check (2026-09-27h) found and fixed a
        # direction-blind bug in the grid's significance test, then
        # recomputed all 216 cells from the original per-seed data with
        # the corrected rule -- the bug turned out inert (0 cells
        # reclassified) and the selection stood. Kept as the pre-
        # registered choice despite a real trade-off surfaced in that
        # recheck (see Readme.md's design-decision section: better on
        # stacked_burst/medium, worse on stacked_burst/high+penalty=2,
        # forfeits the slow-arrival-rate benefit entirely vs an "or"
        # config). Pass arrival_rate_threshold=0.8, combine="or"
        # explicitly to reproduce the original (pre-calibration) detector.
        self.arrival_window = arrival_window
        self.arrival_rate_threshold = arrival_rate_threshold
        self.queue_window = queue_window
        self.queue_growth_threshold = queue_growth_threshold
        self.combine = combine

        self._arrivals = {}      # id(pair_domain) -> deque[timestamps]
        self._queue_samples = {} # core_id -> deque[(t, rq_len)]

    # ---- signal 1: arrival rate ----

    def record_arrival(self, pair_domain, now):
        key = id(pair_domain)
        dq = self._arrivals.setdefault(key, deque())
        dq.append(now)
        while dq and now - dq[0] > self.arrival_window:
            dq.popleft()

    def arrival_rate(self, pair_domain):
        key = id(pair_domain)
        dq = self._arrivals.get(key)
        if not dq or self.arrival_window <= 0:
            return 0.0
        return len(dq) / self.arrival_window

    # ---- signal 2: queue growth rate ----

    def record_queue_sample(self, core, now):
        dq = self._queue_samples.setdefault(core.core_id, deque())
        dq.append((now, core.running_count()))
        while dq and now - dq[0][0] > self.queue_window:
            dq.popleft()

    def queue_growth_rate(self, core):
        dq = self._queue_samples.get(core.core_id)
        if not dq or len(dq) < 2:
            return 0.0
        (t0, q0), (t1, q1) = dq[0], dq[-1]
        if t1 - t0 <= 0:
            return 0.0
        return (q1 - q0) / (t1 - t0) * self.queue_window  # scaled to "growth over window"

    # ---- combined verdict ----

    def is_burst(self, pair_domain, core):
        rate = self.arrival_rate(pair_domain)
        growth = self.queue_growth_rate(core)
        rate_hit = rate >= self.arrival_rate_threshold
        growth_hit = growth >= self.queue_growth_threshold
        # Threshold-calibration grid (2026-09-27, see Readme.md): "and"
        # added as an opt-in alternative combine rule; "or" (default)
        # preserves every existing result exactly.
        triggered = (rate_hit and growth_hit) if self.combine == "and" else (rate_hit or growth_hit)
        return triggered, rate, growth