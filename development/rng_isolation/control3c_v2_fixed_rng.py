"""
Fix 3d verification: re-run Control 3c (heavy_tail/high, seeds 3200-3229,
newidle_mode="legacy_ema", penalty=0.0) with the RNG-isolation fix applied.
Before the fix, this exact case showed makespan swings from -166 to +94ms
across the 30 seeds (mean_diff=+7.07, p=0.29 -- noisy, not significant) and
was later shown to be partly comparing DIFFERENT workloads between the two
scheduler runs. Question: with workloads now provably identical
(run_pair() asserts this), do the huge swings disappear? Does legacy_ema
now match transition's clean 30/30-tie result on these same seeds, or does
a real (if smaller) difference remain?
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
from paired_compare import (
    run_pair, summarize_metric, print_summary, seed_base,
    wilcoxon_signed_rank, format_p,
)

N_REPS = 30
PROFILE = "heavy_tail"
INTENSITY = "high"
BASE = seed_base(PROFILE, INTENSITY) + 1000  # 3200 -- same seeds as Control 3c and Task 3b


def main():
    rows = [
        run_pair(PROFILE, INTENSITY, BASE + rep,
                 balancer_kwargs={"migration_penalty": 0.0, "newidle_mode": "legacy_ema"})
        for rep in range(N_REPS)
    ]
    print(f"### {PROFILE}/{INTENSITY} penalty=0.0ms newidle_mode=legacy_ema, RNG-ISOLATED "
          f"(seeds {BASE}-{BASE + N_REPS - 1}) ###")
    print("(every run_pair() call above already asserted both runs saw an identical workload)")

    for metric in ["makespan", "avg_wait", "p95_wait"]:
        s = summarize_metric(rows, metric, lower_is_better=True)
        print_summary(f"{PROFILE}/{INTENSITY}", s)

    makespan_diffs = [r["burst_aware"]["makespan"] - r["baseline"]["makespan"] for r in rows]
    wr = wilcoxon_signed_rank(makespan_diffs)
    print(f"  Wilcoxon (makespan): n={wr['n']} (zeros dropped={wr['n_zeros_dropped']})  "
          f"W+={wr['W_pos']:.1f}  W-={wr['W_neg']:.1f}  z={wr['z']:+.3f}  p={format_p(wr['p'])}")

    print("  sorted per-seed makespan diffs (burst_aware - baseline):")
    for d in sorted(makespan_diffs):
        print(f"    {d:+8.2f}")

    mig_base = sum(r["baseline"]["migrations"] for r in rows) / N_REPS
    mig_burst = sum(r["burst_aware"]["migrations"] for r in rows) / N_REPS
    print(f"  migrations: baseline={mig_base:.1f} burst_aware={mig_burst:.1f} ratio={mig_burst / mig_base:.3f}")


if __name__ == "__main__":
    main()
