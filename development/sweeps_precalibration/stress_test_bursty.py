"""
Paired-significance stress test for the bursty profile, added after a
review pointed out that Readme.md's earlier migration-penalty comparison
(mean deltas over 10-20 unpaired-looking samples) wasn't actually evidence
of anything without a paired win-rate/sign test. See Readme.md's
"2026-09-26b" update for what this found: no statistically significant
makespan difference at any penalty level tested.

Reports, per (intensity, penalty) cell, over paired seeds matching
Experiment.py's own scheme (1000*profile_id + 100*intensity_id + rep):
  - mean +/- stdev makespan for each scheduler
  - paired win count for burst_aware (lower makespan) + exact binomial
    sign-test p-value (ties excluded from N)
  - avg_wait / p95_wait deltas (burst_aware - baseline)
  - migration count ratio (burst_aware / baseline)
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
import math
import statistics

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer

N_REPS = 30
# bursty = PROFILE_NAMES index 1 -> 1000; medium/high = INTENSITY_LEVELS index 1/2 -> 100/200
SEED_BASE = {"medium": 1100, "high": 1200}


def p95(vals):
    s = sorted(vals)
    idx = min(len(s) - 1, int(math.ceil(0.95 * len(s))) - 1)
    return s[idx]


def sign_test_p(wins, n):
    """Exact two-sided binomial sign test against p=0.5."""
    if n == 0:
        return 1.0
    k = max(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def paired_t(diffs):
    n = len(diffs)
    mean = statistics.mean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    if sd == 0:
        return mean, float("inf") if mean != 0 else 0.0
    return mean, mean / (sd / math.sqrt(n))


def run_one(intensity, penalty, seed):
    results = {}
    for name, cls in [("baseline", LoadBalancer), ("burst_aware", BurstAwareLoadBalancer)]:
        m, b, gt, migs, _, _ = run_simulation(
            "bursty", cls, intensity_level=intensity, seed=seed,
            balancer_kwargs={"migration_penalty": penalty},
        )
        s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
        waits = [t.start_time - t.arrival_time for c in m.cores for t in c.completed_task_list]
        results[name] = {
            "makespan": s["makespan"],
            "avg_wait": s["avg_wait"],
            "p95_wait": p95(waits) if waits else None,
            "migrations": b.migrations,
        }
    return results


def main():
    for intensity in ["medium", "high"]:
        base_seed = SEED_BASE[intensity]
        for penalty in [0.0, 2.0, 4.0, 8.0]:
            rows = [run_one(intensity, penalty, base_seed + rep) for rep in range(N_REPS)]

            makespan_diffs = [r["burst_aware"]["makespan"] - r["baseline"]["makespan"] for r in rows]
            wait_diffs = [r["burst_aware"]["avg_wait"] - r["baseline"]["avg_wait"] for r in rows]
            p95_diffs = [r["burst_aware"]["p95_wait"] - r["baseline"]["p95_wait"] for r in rows]
            mig_base = [r["baseline"]["migrations"] for r in rows]
            mig_burst = [r["burst_aware"]["migrations"] for r in rows]

            TIE_TOLERANCE = 1e-6  # tie tolerance fix (2026-09-27), see Readme.md / paired_compare.py
            wins = sum(1 for d in makespan_diffs if d < -TIE_TOLERANCE)  # burst_aware wins = lower makespan
            ties = sum(1 for d in makespan_diffs if abs(d) <= TIE_TOLERANCE)
            p_sign = sign_test_p(wins, N_REPS - ties)
            mean_d, t_stat = paired_t(makespan_diffs)

            base_makespans = [r["baseline"]["makespan"] for r in rows]
            burst_makespans = [r["burst_aware"]["makespan"] for r in rows]

            print(f"\n=== intensity={intensity} penalty={penalty}ms n={N_REPS} ===")
            print(f"makespan: baseline={statistics.mean(base_makespans):.2f}+-{statistics.stdev(base_makespans):.2f}  "
                  f"burst_aware={statistics.mean(burst_makespans):.2f}+-{statistics.stdev(burst_makespans):.2f}  "
                  f"mean_delta={mean_d:+.2f}  t={t_stat:.2f}")
            print(f"paired wins for burst_aware (lower makespan): {wins}/{N_REPS - ties} (ties={ties})  sign-test p={p_sign:.4f}")
            print(f"avg_wait delta (burst_aware-baseline): mean={statistics.mean(wait_diffs):+.4f} ms")
            print(f"p95_wait delta (burst_aware-baseline): mean={statistics.mean(p95_diffs):+.4f} ms")
            print(f"migrations: baseline mean={statistics.mean(mig_base):.1f}  burst_aware mean={statistics.mean(mig_burst):.1f}  "
                  f"ratio={statistics.mean(mig_burst)/statistics.mean(mig_base):.3f}")


if __name__ == "__main__":
    main()
