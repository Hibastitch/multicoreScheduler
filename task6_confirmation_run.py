"""
Confirmation run (item 4, 2026-09-27i): the FINAL configuration
(A+B+C, resets=False, runnable load_model, calibrated detector
arrival_rate_threshold=1.5/queue_growth_threshold=2/combine="and" --
all now plain defaults, see Readme.md's 2026-09-27i design-decision
entry) vs the verified baseline, on a fresh seed range (20000+), never
inspected before this run. Two report-only secondary variants included
for context, NOT part of the selection (already made and logged):
the original pre-calibration detector (q2_a0.8_or) and the runner-up
(q4_a1.5_or).

Run as: python task6_confirmation_run.py <workload_key>
(one process per workload; merged afterward by
task6_confirmation_analyze.py.)
"""

import csv
import math
import statistics
import sys

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from paired_compare import assert_same_workload, wilcoxon_signed_rank, format_p, TIE_TOLERANCE
import diagnostics

N_REPS = 30
PENALTIES = [0.0, 2.0]

# FINAL configuration is now the plain default for both classes (2026-09-27i)
FINAL_KWARGS = {}
ORIGINAL_KWARGS = dict(queue_growth_threshold=2, arrival_rate_threshold=0.8, combine="or")
RUNNER_UP_KWARGS = dict(queue_growth_threshold=4, arrival_rate_threshold=1.5, combine="or")

VARIANTS = {
    "final": FINAL_KWARGS,
    "original_q2_a0.8_or": ORIGINAL_KWARGS,
    "runner_up_q4_a1.5_or": RUNNER_UP_KWARGS,
}

WORKLOADS = {
    "stacked_low":     dict(profile="stacked_burst", intensity="low", overrides=None, n_tasks=200, seed_base=20000),
    "stacked_medium":  dict(profile="stacked_burst", intensity="medium", overrides=None, n_tasks=200, seed_base=20100),
    "stacked_high":    dict(profile="stacked_burst", intensity="high", overrides=None, n_tasks=200, seed_base=20200),
    "bursty_high_s24": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 24, "burst_duration": 24 / 3.75}, n_tasks=240, seed_base=20300),
    "bursty_high_s64": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 64, "burst_duration": 64 / 3.75}, n_tasks=640, seed_base=20400),
    "heavy_tail_high": dict(profile="heavy_tail", intensity="high", overrides=None, n_tasks=200, seed_base=20500),
}
# Arrival-rate sweep: stacked_burst/medium, rate in {0.5,0.75,1.0,1.5,3.0} x size in {4,12},
# inter_burst_interval=60, burst_duration=size/rate (exact multiple, no truncation).
_RATE_SEED_BASE = 20600
for _rate in [0.5, 0.75, 1.0, 1.5, 3.0]:
    for _size in [4, 12]:
        _key = f"rate{_rate}_s{_size}"
        WORKLOADS[_key] = dict(
            profile="stacked_burst", intensity="medium",
            overrides={"burst_size": _size, "arrival_rate_during_burst": _rate,
                       "burst_duration": _size / _rate, "inter_burst_interval": 60},
            n_tasks=_size * 10, seed_base=_RATE_SEED_BASE,
        )
        _RATE_SEED_BASE += 100

METRICS = ["p95_wait", "p99_wait", "avg_wait", "avg_slowdown", "p95_slowdown", "makespan_excess"]
COST_METRICS = ["sched_cores_scanned", "burst_balance_levels_walked", "total_migrations"]


def sign_p(wins, n):
    if n == 0:
        return 1.0
    k = max(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def run_baseline(profile, intensity, seed, penalty, overrides, n_tasks):
    m, b, gt, migs, logger, plan = run_simulation(
        profile, LoadBalancer, intensity_level=intensity, seed=seed,
        balancer_kwargs={"migration_penalty": penalty}, intensity_overrides=overrides, n_tasks=n_tasks,
    )
    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    row = dict(plan=plan, gt=gt)
    for metric in METRICS + COST_METRICS:
        row[metric] = s[metric]
    return row


def run_variant(profile, intensity, seed, penalty, overrides, n_tasks, variant_kwargs):
    kwargs = dict(variant_kwargs, migration_penalty=penalty)
    m, b, gt, migs, logger, plan = run_simulation(
        profile, BurstAwareLoadBalancer, intensity_level=intensity, seed=seed,
        balancer_kwargs=kwargs, intensity_overrides=overrides, n_tasks=n_tasks,
    )
    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    dq = diagnostics.detector_quality(b.detector_fire_times, gt)
    row = dict(plan=plan, detector_fires=b.detector_fires, recall=dq["recall"], precision=dq["precision"])
    for metric in METRICS + COST_METRICS:
        row[metric] = s[metric]
    return row


def main():
    workload_key = sys.argv[1]
    wl = WORKLOADS[workload_key]

    per_seed_rows = []
    summary_rows = []

    for penalty in PENALTIES:
        base_by_seed = {}
        for rep in range(N_REPS):
            seed = wl["seed_base"] + rep
            base_by_seed[seed] = run_baseline(wl["profile"], wl["intensity"], seed, penalty,
                                               wl["overrides"], wl["n_tasks"])

        for variant_name, variant_kwargs in VARIANTS.items():
            variant_results = []
            for rep in range(N_REPS):
                seed = wl["seed_base"] + rep
                r_var = run_variant(wl["profile"], wl["intensity"], seed, penalty,
                                     wl["overrides"], wl["n_tasks"], variant_kwargs)
                assert_same_workload(base_by_seed[seed]["plan"], r_var["plan"], "baseline", variant_name)
                variant_results.append(r_var)

                row = dict(workload=workload_key, penalty=penalty, variant=variant_name, seed=seed)
                for metric in METRICS + COST_METRICS:
                    row[f"baseline_{metric}"] = base_by_seed[seed][metric]
                    row[f"variant_{metric}"] = r_var[metric]
                row["detector_fires"] = r_var["detector_fires"]
                row["recall"] = r_var["recall"]
                row["precision"] = r_var["precision"]
                per_seed_rows.append(row)

            summary = dict(workload=workload_key, penalty=penalty, variant=variant_name)
            any_harm = False
            for metric in METRICS:
                base_vals = [base_by_seed[wl["seed_base"] + rep][metric] for rep in range(N_REPS)]
                var_vals = [r[metric] for r in variant_results]
                diffs = [v - b for b, v in zip(base_vals, var_vals)]
                wins = sum(1 for d in diffs if d < -TIE_TOLERANCE)
                harms = sum(1 for d in diffs if d > TIE_TOLERANCE)
                ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
                n_eff = N_REPS - ties
                mean_base, mean_var = statistics.mean(base_vals), statistics.mean(var_vals)
                stdev_base = statistics.stdev(base_vals) if len(set(base_vals)) > 1 else 0.0
                pct = ((mean_var - mean_base) / mean_base * 100) if mean_base else float("nan")
                p_sign = sign_p(harms, n_eff) if harms > wins else sign_p(wins, n_eff)
                wr = wilcoxon_signed_rank(diffs)
                # corrected harm rule (2026-09-27h): direction must actually favor harm by count
                is_harm = (harms > wins) and (sign_p(harms, n_eff) < 0.05) and (mean_var > mean_base + TIE_TOLERANCE)
                summary[f"{metric}_base"] = mean_base
                summary[f"{metric}_var"] = mean_var
                summary[f"{metric}_pct"] = pct
                summary[f"{metric}_wins"] = wins
                summary[f"{metric}_harms"] = harms
                summary[f"{metric}_ties"] = ties
                summary[f"{metric}_sign_p"] = p_sign
                summary[f"{metric}_wilcoxon_p"] = wr["p"]
                summary[f"{metric}_harm"] = is_harm
                summary[f"{metric}_floored"] = (stdev_base == 0.0 and statistics.stdev(var_vals) == 0.0)
                any_harm = any_harm or is_harm
            for cm in COST_METRICS:
                base_vals = [base_by_seed[wl["seed_base"] + rep][cm] for rep in range(N_REPS)]
                var_vals = [r[cm] for r in variant_results]
                mean_base, mean_var = statistics.mean(base_vals), statistics.mean(var_vals)
                pct = ((mean_var - mean_base) / mean_base * 100) if mean_base else float("nan")
                summary[f"{cm}_base"] = mean_base
                summary[f"{cm}_var"] = mean_var
                summary[f"{cm}_pct"] = pct
            summary["any_harm"] = any_harm
            summary["detector_fires"] = statistics.mean(r["detector_fires"] for r in variant_results)
            recalls = [r["recall"] for r in variant_results if r["recall"] is not None]
            precisions = [r["precision"] for r in variant_results if r["precision"] is not None]
            summary["recall"] = statistics.mean(recalls) if recalls else None
            summary["precision"] = statistics.mean(precisions) if precisions else None
            summary_rows.append(summary)

            print(f"{workload_key:16} penalty={penalty}ms {variant_name:22} "
                  f"p95_wait: {summary['p95_wait_base']:.3f}->{summary['p95_wait_var']:.3f} "
                  f"({summary['p95_wait_pct']:+.1f}%) sign_p={format_p(summary['p95_wait_sign_p'])} "
                  f"harm={any_harm} fires={summary['detector_fires']:.1f} "
                  f"cores_scanned_pct={summary['sched_cores_scanned_pct']:+.1f}%")

    with open(f"results_task6_confirmation_{workload_key}_perseed.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(per_seed_rows[0].keys()))
        w.writeheader()
        w.writerows(per_seed_rows)

    with open(f"results_task6_confirmation_{workload_key}_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)

    print(f"\nWrote results_task6_confirmation_{workload_key}_perseed.csv and _summary.csv")


if __name__ == "__main__":
    main()
