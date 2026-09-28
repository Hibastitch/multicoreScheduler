"""
Task 1: is heavy_tail/high's ~5.7% makespan gain (baseline=190.3, burst_aware
=179.4 at migration_penalty=2ms, from the n=10 run in results_migration_
penalty2ms.csv) real, or another small-n artifact like bursty/high turned
out to be? Same paired-seed, n=30, sign-test methodology as
stress_test_bursty.py / Readme.md's 2026-09-26b correction.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "simulator"))
import csv

from paired_compare import run_pair, summarize_metric, print_summary, seed_base

N_REPS = 30
PROFILE = "heavy_tail"
INTENSITY = "high"


def main():
    rows_out = []
    for penalty in [0.0, 2.0]:
        base = seed_base(PROFILE, INTENSITY)
        rows = [
            run_pair(PROFILE, INTENSITY, base + rep, balancer_kwargs={"migration_penalty": penalty})
            for rep in range(N_REPS)
        ]

        for metric in ["makespan", "avg_wait", "p95_wait"]:
            s = summarize_metric(rows, metric, lower_is_better=True)
            print_summary(f"heavy_tail/high penalty={penalty}ms", s)
            rows_out.append(dict(penalty=penalty, **s))

        mig_base = sum(r["baseline"]["migrations"] for r in rows) / N_REPS
        mig_burst = sum(r["burst_aware"]["migrations"] for r in rows) / N_REPS
        print(f"  migrations: baseline={mig_base:.1f} burst_aware={mig_burst:.1f} ratio={mig_burst/mig_base:.3f}")
        print()

    with open("results_task1_heavytail.csv", "w", newline="") as f:
        keys = list(rows_out[0].keys())
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows_out)
    print("Wrote results_task1_heavytail.csv")


if __name__ == "__main__":
    main()
