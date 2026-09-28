"""
Task 3b: re-run the same paired-seed, n=30 checks as the 2026-09-26b/c
corrections, but with newidle_mode="transition" instead of the default
"legacy_ema" -- does fixing the newly-idle gate (Task 3 found it
self-suppressed to near-zero) change whether burst-aware separates from
baseline once baseline's own reactive path actually works?

heavy_tail/high uses 30 FRESH seeds (base 3200) -- NOT the 2200-2229
range already inspected in the 2026-09-26c/e updates, per the
pre-registered-hypothesis rule: confirming/disconfirming the worst-case-
makespan story on the same seeds that motivated it would be circular.
bursty/high reuses its usual seed range (1200-1229); no hypothesis was
built from inspecting individual bursty/high seeds, so no fresh-seed
requirement applies there.
"""

import csv

from paired_compare import (
    run_pair, summarize_metric, print_summary, seed_base,
    wilcoxon_signed_rank, format_p,
)

N_REPS = 30
CASES = [
    ("bursty", "high", seed_base("bursty", "high")),          # 1200 (previously inspected range, reused)
    ("heavy_tail", "high", seed_base("heavy_tail", "high") + 1000),  # 3200 -- FRESH, never inspected before
]


def main():
    csv_rows = []
    for profile, intensity, base in CASES:
        for penalty in [0.0, 2.0]:
            rows = [
                run_pair(profile, intensity, base + rep,
                         balancer_kwargs={"migration_penalty": penalty, "newidle_mode": "transition"})
                for rep in range(N_REPS)
            ]

            print(f"\n### {profile}/{intensity} penalty={penalty}ms newidle_mode=transition "
                  f"(seeds {base}-{base + N_REPS - 1}) ###")

            for metric in ["makespan", "avg_wait", "p95_wait"]:
                s = summarize_metric(rows, metric, lower_is_better=True)
                print_summary(f"{profile}/{intensity}", s)
                csv_rows.append(dict(profile=profile, intensity=intensity, penalty=penalty, **s))

            makespan_diffs = [r["burst_aware"]["makespan"] - r["baseline"]["makespan"] for r in rows]
            wr = wilcoxon_signed_rank(makespan_diffs)
            print(f"  Wilcoxon (makespan): n={wr['n']} (zeros dropped={wr['n_zeros_dropped']})  "
                  f"W+={wr['W_pos']:.1f}  W-={wr['W_neg']:.1f}  z={wr['z']:+.3f}  p={format_p(wr['p'])}")

            print("  sorted per-seed makespan diffs (burst_aware - baseline):")
            for d in sorted(makespan_diffs):
                print(f"    {d:+8.2f}")

            mig_base = sum(r["baseline"]["migrations"] for r in rows) / N_REPS
            mig_burst = sum(r["burst_aware"]["migrations"] for r in rows) / N_REPS
            print(f"  migrations: baseline={mig_base:.1f} burst_aware={mig_burst:.1f} "
                  f"ratio={mig_burst / mig_base:.3f}")

    with open("results_task3b_transition.csv", "w", newline="") as f:
        keys = list(csv_rows[0].keys())
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(csv_rows)
    print("\nWrote results_task3b_transition.csv")


if __name__ == "__main__":
    main()
