"""
Control 3c: same 30 FRESH heavy_tail/high seeds (3200-3229) as Task 3b's
"transition" run (which came back 30/30 ties), but with newidle_mode=
"legacy_ema" instead. Purpose: attribute Task 3b's tie result to the
newidle-gate fix specifically, not to something else about these
particular seeds. If legacy_ema shows real (non-tied) wins for
burst-aware on these same seeds while transition showed 30/30 ties, the
gate is confirmed as the mechanism. If legacy_ema ALSO ties out on these
seeds, the attribution stays open -- report that outcome honestly too.
"""

import csv

from paired_compare import (
    run_pair, summarize_metric, print_summary, seed_base,
    wilcoxon_signed_rank, format_p,
)

N_REPS = 30
PROFILE = "heavy_tail"
INTENSITY = "high"
BASE = seed_base(PROFILE, INTENSITY) + 1000  # 3200 -- same fresh range Task 3b used


def main():
    csv_rows = []
    for penalty in [0.0, 2.0]:
        rows = [
            run_pair(PROFILE, INTENSITY, BASE + rep,
                     balancer_kwargs={"migration_penalty": penalty, "newidle_mode": "legacy_ema"})
            for rep in range(N_REPS)
        ]

        print(f"\n### {PROFILE}/{INTENSITY} penalty={penalty}ms newidle_mode=legacy_ema "
              f"(seeds {BASE}-{BASE + N_REPS - 1}, same as Task 3b's transition run) ###")

        for metric in ["makespan", "avg_wait", "p95_wait"]:
            s = summarize_metric(rows, metric, lower_is_better=True)
            print_summary(f"{PROFILE}/{INTENSITY}", s)
            csv_rows.append(dict(profile=PROFILE, intensity=INTENSITY, penalty=penalty, **s))

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

    with open("results_control3c_legacy.csv", "w", newline="") as f:
        keys = list(csv_rows[0].keys())
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(csv_rows)
    print("\nWrote results_control3c_legacy.csv")


if __name__ == "__main__":
    main()
