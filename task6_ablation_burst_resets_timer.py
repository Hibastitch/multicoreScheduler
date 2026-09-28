"""
Item 2: pre-registered ablation, on FRESH seeds (not 5000-5029/5100-5129/
5200-5229, already inspected in Task 5 / Task 6 follow-ups). Three-way
comparison per seed: baseline vs burst-aware(burst_resets_timer=True,
current/default) vs burst-aware(burst_resets_timer=False). A+B+C,
transition, n=30 paired, penalty in {0,2}ms, stacked_burst
low/medium/high. Primary metric p95_wait, sign test + Wilcoxon for both
(baseline vs resets=True) and (baseline vs resets=False), plus a direct
(resets=True vs resets=False) comparison.

If resets=False removes stacked_burst/low's harm (brings it back toward
baseline / positive-vs-baseline sign flips to non-significant or
favorable), the remaining low-intensity effect is batch-splitting alone,
independent of the timer. Task 6 follow-up 3's direct attempt-schedule
measurement already found no phase-shift either way -- this is an
independent check of that finding, run regardless of its outcome, per
the pre-registration.
"""

import csv

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from paired_compare import (
    summarize_metric, print_summary, assert_same_workload,
    wilcoxon_signed_rank, format_p,
)

N_REPS = 30
PENALTIES = [0.0, 2.0]
INTENSITIES = ["low", "medium", "high"]
FRESH_SEED_BASE = {"low": 7000, "medium": 7100, "high": 7200}  # fresh -- distinct from every prior range

ABC_BASE = {"newidle_mode": "transition", "per_cpu_last_balance": True, "imbalance_model": "kernel"}


def run_three(intensity, seed, penalty):
    out = {}
    for name, cls, extra in [
        ("baseline", LoadBalancer, {}),
        ("resets_true", BurstAwareLoadBalancer, {"burst_resets_timer": True}),
        ("resets_false", BurstAwareLoadBalancer, {"burst_resets_timer": False}),
    ]:
        kwargs = dict(ABC_BASE, migration_penalty=penalty, **extra)
        m, b, gt, migs, logger, plan = run_simulation(
            "stacked_burst", cls, intensity_level=intensity, seed=seed,
            balancer_kwargs=kwargs, load_model="runnable",
        )
        s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
        out[name] = {"summary": s, "p95_wait": s["p95_wait"], "plan": plan}
    assert_same_workload(out["baseline"]["plan"], out["resets_true"]["plan"], "baseline", "resets_true")
    assert_same_workload(out["baseline"]["plan"], out["resets_false"]["plan"], "baseline", "resets_false")
    return out


def compare(rows, key_a, key_b, label):
    rows_view = [{"baseline": {"p95_wait": r[key_a]["p95_wait"]},
                  "burst_aware": {"p95_wait": r[key_b]["p95_wait"]}} for r in rows]
    s = summarize_metric(rows_view, "p95_wait", lower_is_better=True)
    print_summary(label, s)
    diffs = [r[key_b]["p95_wait"] - r[key_a]["p95_wait"] for r in rows]
    wr = wilcoxon_signed_rank(diffs)
    print(f"    Wilcoxon: n={wr['n']} (zeros dropped={wr['n_zeros_dropped']})  z={wr['z']:+.3f}  p={format_p(wr['p'])}")
    return s, wr


def main():
    csv_rows = []
    for intensity in INTENSITIES:
        base_seed = FRESH_SEED_BASE[intensity]
        for penalty in PENALTIES:
            print(f"\n{'=' * 90}")
            print(f"stacked_burst / {intensity} / penalty={penalty}ms / A+B+C / FRESH seeds "
                  f"{base_seed}-{base_seed + N_REPS - 1}")
            print("=" * 90)

            rows = [run_three(intensity, base_seed + rep, penalty) for rep in range(N_REPS)]

            s1, w1 = compare(rows, "baseline", "resets_true", f"{intensity} baseline vs resets_true (current)")
            s2, w2 = compare(rows, "baseline", "resets_false", f"{intensity} baseline vs resets_false (ablation)")
            s3, w3 = compare(rows, "resets_true", "resets_false", f"{intensity} resets_true vs resets_false (direct)")

            csv_rows.append(dict(intensity=intensity, penalty=penalty, comparison="baseline_vs_resets_true",
                                  mean_diff=s1["mean_diff"], sign_p=s1["p_sign"], wilcoxon_p=w1["p"]))
            csv_rows.append(dict(intensity=intensity, penalty=penalty, comparison="baseline_vs_resets_false",
                                  mean_diff=s2["mean_diff"], sign_p=s2["p_sign"], wilcoxon_p=w2["p"]))
            csv_rows.append(dict(intensity=intensity, penalty=penalty, comparison="resets_true_vs_resets_false",
                                  mean_diff=s3["mean_diff"], sign_p=s3["p_sign"], wilcoxon_p=w3["p"]))

    with open("results_task6_ablation_burst_resets_timer.csv", "w", newline="") as f:
        keys = list(csv_rows[0].keys())
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(csv_rows)
    print("\nWrote results_task6_ablation_burst_resets_timer.csv")


if __name__ == "__main__":
    main()
