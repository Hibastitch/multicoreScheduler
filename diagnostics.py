"""
Task 4 diagnostics: live samplers (registered via Main.run_simulation's
`extra_processes` hook) plus post-hoc analysis functions, all read-only
instrumentation -- nothing here changes scheduling/detector behavior.

Live samplers run once per sim tick (their own env.timeout(1) loop,
same pattern as Main.py's periodic_ticker) and write into a plain dict
passed in by the caller, so a script can hold onto that dict after
run_simulation() returns.

  - idle_while_waiting_sampler: accumulates `idle_while_waiting_time`,
    the total simulated time where >=1 core is idle AND >=1 task is
    queued (not running) elsewhere -- the waste ANY balancer could
    recover, a hard ceiling this run's specific scheduler choices are
    not achieving. (An idle core, by definition, has an empty rq --
    core.is_idle() requires both current_task is None AND not rq -- so
    "queued elsewhere" is automatic once any core is idle and the
    machine-wide queued count is >=1; no separate "elsewhere" check
    needed.)
  - queue_imbalance_sampler: accumulates a full (t, max(rq)-min(rq))
    time series across all cores. Post-hoc analysis (below) splits this
    into "during a ground-truth burst window" vs overall, since burst
    windows are only known for certain once WorkloadGenerator.
    generate_plan() has run (before or after this sampler ran doesn't
    matter -- the raw series is timestamped and filtered afterward).

Post-hoc analysis (no simpy involved, operates on what run_simulation()
already returns):
  - detector_quality: recall/precision/avg detection latency of a
    burst-aware balancer's raw detector fires against ground truth.
  - lead_time_per_burst: for each ground-truth burst, baseline's first
    migration at/after burst start MINUS burst-aware's first
    burst-tagged migration at/after burst start. Positive = burst-aware
    acted earlier.
"""

import statistics


def idle_while_waiting_sampler(state):
    state.setdefault("idle_while_waiting_time", 0.0)

    def _proc(env, cores, balancer):
        while True:
            any_idle = any(c.is_idle() for c in cores)
            total_queued = sum(len(c.rq) for c in cores)
            if any_idle and total_queued > 0:
                state["idle_while_waiting_time"] += 1.0
            yield env.timeout(1)
    return _proc


def queue_imbalance_sampler(state):
    state.setdefault("queue_imbalance_samples", [])  # [(t, max_rq - min_rq)]

    def _proc(env, cores, balancer):
        while True:
            lens = [len(c.rq) for c in cores]
            state["queue_imbalance_samples"].append((env.now, max(lens) - min(lens)))
            yield env.timeout(1)
    return _proc


def machine_wide_queue_sampler(state):
    """Records (t, max(len(rq)) across ALL cores) every tick -- feeds
    machine_wide_drain_times() below. Added 2026-09-27 (Task 6 Fix C
    follow-up): a per-core drain trace (Task 6 prep) only tells you when
    ONE specific core empties, which understates recovery time once
    Fix C's migrate_util can relocate a whole pile onto a DIFFERENT core
    in one pass -- this tracks when the MACHINE as a whole has caught up,
    not just the originally-stacked core."""
    state.setdefault("max_qlen_samples", [])  # [(t, max_rq_len)]

    def _proc(env, cores, balancer):
        while True:
            state["max_qlen_samples"].append((env.now, max(len(c.rq) for c in cores)))
            yield env.timeout(1)
    return _proc


def machine_wide_drain_times(samples, ground_truth_bursts, threshold=1):
    """For each ground-truth burst, time from its start until full
    recovery: first find the burst's own SPIKE (first sample at/after
    start with max-qlen > threshold -- since arrivals land gradually
    over the burst's duration, `t == start` itself can still read
    max-qlen <= threshold at essentially the same tick, a same-instant
    sampler/arrival race, not real recovery), then find the first
    sample at/after the spike where max-qlen <= threshold again.
    Returns a list, one entry per burst (0.0 if the burst never actually
    pushed any core's queue past threshold; None if it spiked but never
    recovered within the observed samples)."""
    samples_sorted = sorted(samples)
    times = []
    for start, end, _n in ground_truth_bursts:
        spike_at = next((t for t, qlen in samples_sorted if t >= start and qlen > threshold), None)
        if spike_at is None:
            times.append(0.0)
            continue
        drained_at = next((t for t, qlen in samples_sorted if t >= spike_at and qlen <= threshold), None)
        times.append((drained_at - start) if drained_at is not None else None)
    return times


def summarize_queue_imbalance(samples, ground_truth_bursts):
    """Returns dict with overall mean/max and during-burst mean/max
    (during-burst = sample's timestamp falls within any [start, end])."""
    if not samples:
        return dict(overall_mean=None, overall_max=None, during_burst_mean=None, during_burst_max=None)

    overall_vals = [v for _, v in samples]
    during = [v for t, v in samples if any(start <= t <= end for start, end, _ in ground_truth_bursts)]

    return dict(
        overall_mean=statistics.mean(overall_vals),
        overall_max=max(overall_vals),
        during_burst_mean=statistics.mean(during) if during else None,
        during_burst_max=max(during) if during else None,
        n_samples=len(samples),
        n_during_burst_samples=len(during),
    )


def detector_quality(fire_times, ground_truth_bursts):
    """recall: fraction of ground-truth bursts with >=1 raw detector fire
    within [start, end]. precision: fraction of raw fires that fall
    within ANY ground-truth burst's [start, end]. avg_detection_latency:
    mean of (first matching fire time - burst start) over detected
    bursts. All three are None if there's nothing to compute them from
    (no bursts, or no fires)."""
    fire_times = sorted(fire_times)
    n_bursts = len(ground_truth_bursts)

    latencies = []
    n_detected = 0
    for start, end, _n in ground_truth_bursts:
        first_fire = next((t for t in fire_times if start <= t <= end), None)
        if first_fire is not None:
            n_detected += 1
            latencies.append(first_fire - start)

    def _in_any_window(t):
        return any(start <= t <= end for start, end, _n in ground_truth_bursts)

    n_fires_in_window = sum(1 for t in fire_times if _in_any_window(t))

    return dict(
        n_bursts=n_bursts,
        n_fires=len(fire_times),
        n_detected=n_detected,
        recall=(n_detected / n_bursts) if n_bursts else None,
        precision=(n_fires_in_window / len(fire_times)) if fire_times else None,
        avg_detection_latency=statistics.mean(latencies) if latencies else None,
    )


def lead_time_per_burst(baseline_migration_events, burst_aware_migration_events, ground_truth_bursts):
    """For each ground-truth burst with both a baseline migration and a
    burst-TAGGED burst-aware migration at/after its start: baseline's
    first migration time minus burst-aware's first burst-triggered
    migration time. Positive = burst-aware acted earlier.

    FIXED (2026-09-26, found by inspecting task4_verify_one_seed.py's
    output): the original version searched with no upper bound
    (`t >= start`, no `end`), so a single later migration could get
    "claimed" as the nearest match by every earlier burst that had no
    closer migration of its own -- the same timestamp showed up as the
    lead time for two different bursts even though there were only 2
    burst-tagged migrations that whole run. Fixed by (1) restricting
    matches to [start, end + BURST_MATCH_SLACK_MS] -- a migration long
    after a burst ended isn't a response to it -- and (2) exclusive
    matching: each migration can be claimed by at most one burst,
    first-come-first-served in burst-start order.

    Returns a dict with TWO lead-time definitions, both against the same
    "baseline's first migration of any kind in the window" reference:
      - vs_burst_tagged: baseline's first-any-migration time minus
        burst_aware's first BURST-TAGGED migration time (the original
        question: does the dedicated mechanism beat baseline).
      - vs_any_migration: baseline's first-any-migration time minus
        burst_aware's first migration of ANY kind (does burst-aware as a
        WHOLE scheduler, periodic/newidle included, beat baseline --
        a looser, more forgiving comparison).
    Plus per-definition counts of how many bursts had no matching
    migration on either side (unmatched bursts are excluded from the
    lead-time list itself, not counted as 0)."""
    BURST_MATCH_SLACK_MS = 10.0

    def _match_first_per_burst(times):
        times_sorted = sorted(times)
        used = [False] * len(times_sorted)
        matches = []
        for start, end, _n in ground_truth_bursts:
            found = None
            for i, t in enumerate(times_sorted):
                if used[i] or t < start:
                    continue
                if t > end + BURST_MATCH_SLACK_MS:
                    break
                found = t
                used[i] = True
                break
            matches.append(found)
        return matches

    baseline_any_times = [e["t"] for e in baseline_migration_events]
    burst_aware_tagged_times = [e["t"] for e in burst_aware_migration_events if e.get("trigger") == "burst"]
    burst_aware_any_times = [e["t"] for e in burst_aware_migration_events]

    baseline_matches = _match_first_per_burst(baseline_any_times)
    tagged_matches = _match_first_per_burst(burst_aware_tagged_times)
    any_matches = _match_first_per_burst(burst_aware_any_times)

    def _pair_up(a_matches, b_matches):
        return [a - b for a, b in zip(a_matches, b_matches) if a is not None and b is not None]

    n_bursts = len(ground_truth_bursts)
    return dict(
        n_bursts=n_bursts,
        vs_burst_tagged=_pair_up(baseline_matches, tagged_matches),
        vs_any_migration=_pair_up(baseline_matches, any_matches),
        n_bursts_no_baseline_migration=sum(1 for m in baseline_matches if m is None),
        n_bursts_no_burst_tagged_migration=sum(1 for m in tagged_matches if m is None),
        n_bursts_no_any_burst_aware_migration=sum(1 for m in any_matches if m is None),
    )
