"""
Pre-registered detector threshold calibration. Grid: queue_growth_
threshold in {2,4,8}, arrival_rate_threshold in {0.8,1.5}, combine in
{or,and} -- 12 configs. A+B+C, resets=False, penalty in {0,2}, n=30
paired, fresh seeds, tie-tolerant tests, per-seed CSV.

Baseline doesn't depend on detector thresholds at all, so it's run ONCE
per (workload, penalty, seed) and reused across all 12 threshold
configs -- 13x fewer baseline runs than a naive full factorial.

Run as: python task6_threshold_grid.py <workload_key>
(one process per workload, so each fits comfortably in a bounded
background-task timeout; results merged afterward.)
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
import csv
import itertools
import math
import os
import statistics
import sys

# TASK 8 v2 RE-RUN (2026-09-29, docs/NOTEBOOK.md 2026-09-29c pre-
# registration, Step 4 "prepare, don't run"): TASK8_V2=1 in the
# environment writes to _v2-suffixed output files instead of
# overwriting the original grid CSVs -- SAME seeds as the original grid
# (only the code's defaults changed, in Step 3, not this script), so
# the v2 run automatically picks up checker_model="kernel"/
# busy_factor=16/cache_hot=True/numa_fix=True (LoadBalancer/
# BurstAwareLoadBalancer's own new defaults) and time_slice=2.8/
# placement_root="own" (run_simulation()'s new defaults) without any
# kwarg changes here.
V2_SUFFIX = "_v2" if os.environ.get("TASK8_V2") else ""

# TASK 9 v3 RE-RUN (2026-09-29, docs/NOTEBOOK.md 2026-09-29h pre-
# registration, Step 4 "prepare, don't run"): TASK9_V3=1 in the
# environment (a) writes to _v3-suffixed output files, (b) adds
# penalty=0.5ms (the kernel's own sysctl_sched_migration_cost estimate)
# to the penalty sweep, (c) sets penalty_model="ran_only" for BOTH
# baseline and burst-aware (Task 9a: charge migration_penalty only to a
# task that has already run) and burst_gap_gate=True for burst-aware
# only (Task 9b: skip a burst-triggered domain unless the least-loaded
# other core is >=2 nr_running behind), and (d) shifts every workload's
# seed_base by +60000 -- FRESH 70000+ seeds, 100 apart per workload,
# never used by any prior grid/confirmation/audit range in this repo.
V3 = bool(os.environ.get("TASK9_V3"))
V3_SUFFIX = "_v3" if V3 else ""
V3_SEED_OFFSET = 60000 if V3 else 0
V3_BASELINE_KWARGS = dict(penalty_model="ran_only") if V3 else {}
V3_VARIANT_KWARGS = dict(penalty_model="ran_only", burst_gap_gate=True) if V3 else {}

# TASK 10 v4 RE-RUN (2026-09-30, docs/NOTEBOOK.md 2026-09-30d pre-
# registration, Step 3 "prepare, don't run"): TASK10_V4=1 in the
# environment (a) writes to _v4-suffixed output files, (b) keeps the
# same 3-penalty sweep (0/0.5/2ms) as v3, (c) sets penalty_model=
# "ran_only" for both baseline and burst-aware (Task 9a, unchanged)
# and burst_idle_check=True for burst-aware only (Task 10: skip the
# WHOLE burst-triggered domain_chain walk unless at least one core is
# idle machine-wide -- burst_gap_gate stays False, not combined), and
# (d) shifts every workload's seed_base by +100000 from the v1/v2 base
# -- FRESH 110000+ seeds, 100 apart per workload, never used by any
# prior grid/confirmation/audit/diagnostic range in this repo.
V4 = bool(os.environ.get("TASK10_V4"))
V4_SUFFIX = "_v4" if V4 else ""
SUFFIX = V4_SUFFIX or V3_SUFFIX or V2_SUFFIX

# PRE-PUBLICATION AUDIT FIX (2026-10-01, docs/NOTEBOOK.md): running this
# script with NONE of TASK8_V2/TASK9_V3/TASK10_V4 set used to silently
# fall back to v1 behavior AND overwrite the un-suffixed v1 result files
# already on disk -- no warning, no version check. Now a hard error
# unless TASK6_V1_LEGACY=1 is set explicitly (the only way to get that
# old v1 behavior back on purpose). Checked BEFORE any file is opened.
if not SUFFIX and not os.environ.get("TASK6_V1_LEGACY"):
    raise SystemExit(
        "ERROR: no pipeline version selected -- refusing to run (would "
        "silently reproduce v1 and overwrite the un-suffixed v1 result "
        "files already on disk). Set TASK10_V4=1 to reproduce the "
        "published v4 results (the current pipeline); TASK9_V3=1 or "
        "TASK8_V2=1 for an earlier version; or TASK6_V1_LEGACY=1 to "
        "deliberately run the original v1 config/seeds on purpose."
    )

V4_SEED_OFFSET = 100000 if V4 else 0
V4_BASELINE_KWARGS = dict(penalty_model="ran_only") if V4 else {}
V4_VARIANT_KWARGS = dict(penalty_model="ran_only", burst_idle_check=True) if V4 else {}

# REORGANIZATION (2026-10-01, docs/NOTEBOOK.md): old-version (non-v4)
# outputs now live in history/ -- the main folder holds only the final
# v4 data. v4 keeps writing here unchanged (DATA_DIR="").
DATA_DIR = "" if V4 else "history/"

SEED_OFFSET = V4_SEED_OFFSET or V3_SEED_OFFSET
BASELINE_KWARGS = V4_BASELINE_KWARGS or V3_BASELINE_KWARGS
VARIANT_KWARGS = V4_VARIANT_KWARGS or V3_VARIANT_KWARGS

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from paired_compare import assert_same_workload, wilcoxon_signed_rank, format_p, TIE_TOLERANCE
import diagnostics

N_REPS = 30
PENALTIES = [0.0, 0.5, 2.0] if (V3 or V4) else [0.0, 2.0]
ABC_BASE = {"newidle_mode": "transition", "per_cpu_last_balance": True, "imbalance_model": "kernel",
            "burst_resets_timer": False}

QUEUE_GROWTH_THRESHOLDS = [2, 4, 8]
ARRIVAL_RATE_THRESHOLDS = [0.8, 1.5]
COMBINES = ["or", "and"]
THRESHOLD_CONFIGS = [
    dict(queue_growth_threshold=q, arrival_rate_threshold=a, combine=c)
    for q, a, c in itertools.product(QUEUE_GROWTH_THRESHOLDS, ARRIVAL_RATE_THRESHOLDS, COMBINES)
]

WORKLOADS = {
    "stacked_low":     dict(profile="stacked_burst", intensity="low", overrides=None, n_tasks=200, seed_base=10000),
    "stacked_medium":  dict(profile="stacked_burst", intensity="medium", overrides=None, n_tasks=200, seed_base=10100),
    "stacked_high":    dict(profile="stacked_burst", intensity="high", overrides=None, n_tasks=200, seed_base=10200),
    "bursty_high_s24": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 24, "burst_duration": 24 / 3.75}, n_tasks=240, seed_base=10300),
    "bursty_high_s64": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 64, "burst_duration": 64 / 3.75}, n_tasks=640, seed_base=10400),
    "rate0.5_s4":      dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 4, "arrival_rate_during_burst": 0.5,
                                        "burst_duration": 4 / 0.5, "inter_burst_interval": 60},
                             n_tasks=40, seed_base=10500),
    "rate0.5_s12":     dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 12, "arrival_rate_during_burst": 0.5,
                                        "burst_duration": 12 / 0.5, "inter_burst_interval": 60},
                             n_tasks=120, seed_base=10600),
    "rate3.0_s4":      dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 4, "arrival_rate_during_burst": 3.0,
                                        "burst_duration": 4 / 3.0, "inter_burst_interval": 60},
                             n_tasks=40, seed_base=10700),
    "rate3.0_s12":     dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 12, "arrival_rate_during_burst": 3.0,
                                        "burst_duration": 12 / 3.0, "inter_burst_interval": 60},
                             n_tasks=120, seed_base=10800),
}

if SEED_OFFSET:
    for _wl in WORKLOADS.values():
        _wl["seed_base"] += SEED_OFFSET

METRICS = ["p95_wait", "avg_wait", "avg_slowdown"]


def sign_p(wins, n):
    if n == 0:
        return 1.0
    k = max(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def run_baseline(profile, intensity, seed, penalty, overrides, n_tasks):
    kwargs = dict(ABC_BASE, migration_penalty=penalty, **BASELINE_KWARGS)
    del kwargs["burst_resets_timer"]  # baseline (LoadBalancer) doesn't take this
    m, b, gt, migs, logger, plan = run_simulation(
        profile, LoadBalancer, intensity_level=intensity, seed=seed,
        balancer_kwargs=kwargs, load_model="runnable", intensity_overrides=overrides, n_tasks=n_tasks,
    )
    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    return dict(plan=plan, gt=gt, **{metric: s[metric] for metric in METRICS})


def run_variant(profile, intensity, seed, penalty, overrides, n_tasks, threshold_cfg):
    kwargs = dict(ABC_BASE, migration_penalty=penalty, **VARIANT_KWARGS, **threshold_cfg)
    m, b, gt, migs, logger, plan = run_simulation(
        profile, BurstAwareLoadBalancer, intensity_level=intensity, seed=seed,
        balancer_kwargs=kwargs, load_model="runnable", intensity_overrides=overrides, n_tasks=n_tasks,
    )
    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    dq = diagnostics.detector_quality(b.detector_fire_times, gt)
    return dict(plan=plan, sched_cores_scanned=s["sched_cores_scanned"],
                detector_fires=b.detector_fires, recall=dq["recall"], precision=dq["precision"],
                **{metric: s[metric] for metric in METRICS})


def main():
    workload_key = sys.argv[1]
    # Optional chunking (2026-09-27): a single workload x both penalties x
    # all 12 configs can take 10-16+ minutes (n_tasks up to 640, low
    # intensity's long makespan), too long for one bounded background
    # call. argv[2] = "0"/"1" selects a penalty; argv[3] = "0"/"1" selects
    # the first/second half of THRESHOLD_CONFIGS. Omit both to run
    # everything in one process (fine for quick manual/smoke use). Output
    # filenames include the chunk suffix so parallel chunks never collide;
    # a separate merge step combines them afterward.
    penalty_idx = sys.argv[2] if len(sys.argv) > 2 else None
    config_half = sys.argv[3] if len(sys.argv) > 3 else None
    chunk_suffix = ""

    penalties = PENALTIES
    if penalty_idx is not None:
        penalties = [PENALTIES[int(penalty_idx)]]
        chunk_suffix += f"_p{penalty_idx}"

    configs = THRESHOLD_CONFIGS
    if config_half is not None:
        half = len(THRESHOLD_CONFIGS) // 2
        configs = THRESHOLD_CONFIGS[:half] if config_half == "0" else THRESHOLD_CONFIGS[half:]
        chunk_suffix += f"_c{config_half}"

    wl = WORKLOADS[workload_key]

    per_seed_rows = []
    summary_rows = []

    for penalty in penalties:
        base_by_seed = {}
        for rep in range(N_REPS):
            seed = wl["seed_base"] + rep
            base_by_seed[seed] = run_baseline(wl["profile"], wl["intensity"], seed, penalty,
                                               wl["overrides"], wl["n_tasks"])

        for cfg in configs:
            cfg_label = f"q{cfg['queue_growth_threshold']}_a{cfg['arrival_rate_threshold']}_{cfg['combine']}"
            variant_results = []
            for rep in range(N_REPS):
                seed = wl["seed_base"] + rep
                r_var = run_variant(wl["profile"], wl["intensity"], seed, penalty,
                                     wl["overrides"], wl["n_tasks"], cfg)
                assert_same_workload(base_by_seed[seed]["plan"], r_var["plan"], "baseline", "burst_aware")
                variant_results.append(r_var)

                row = dict(workload=workload_key, penalty=penalty, config=cfg_label, seed=seed)
                for metric in METRICS:
                    row[f"baseline_{metric}"] = base_by_seed[seed][metric]
                    row[f"burst_aware_{metric}"] = r_var[metric]
                row["detector_fires"] = r_var["detector_fires"]
                row["recall"] = r_var["recall"]
                row["precision"] = r_var["precision"]
                row["sched_cores_scanned"] = r_var["sched_cores_scanned"]
                per_seed_rows.append(row)

            summary = dict(workload=workload_key, penalty=penalty, config=cfg_label,
                            queue_growth_threshold=cfg["queue_growth_threshold"],
                            arrival_rate_threshold=cfg["arrival_rate_threshold"], combine=cfg["combine"])
            any_significant_harm = False
            for metric in METRICS:
                base_vals = [base_by_seed[wl["seed_base"] + rep][metric] for rep in range(N_REPS)]
                var_vals = [r[metric] for r in variant_results]
                diffs = [v - b for b, v in zip(base_vals, var_vals)]
                wins = sum(1 for d in diffs if d < -TIE_TOLERANCE)
                harms = sum(1 for d in diffs if d > TIE_TOLERANCE)
                ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
                n_eff = N_REPS - ties
                p = sign_p(wins, n_eff)
                wr = wilcoxon_signed_rank(diffs)
                mean_base, mean_var = statistics.mean(base_vals), statistics.mean(var_vals)
                pct = ((mean_var - mean_base) / mean_base * 100) if mean_base else float("nan")
                summary[f"{metric}_base"] = mean_base
                summary[f"{metric}_var"] = mean_var
                summary[f"{metric}_pct"] = pct
                summary[f"{metric}_wins"] = wins
                summary[f"{metric}_harms"] = harms
                summary[f"{metric}_ties"] = ties
                summary[f"{metric}_sign_p"] = p
                summary[f"{metric}_wilcoxon_p"] = wr["p"]
                # "significant harm" = variant significantly WORSE (mean higher, sign test on
                # the harms side significant) -- checked via harms count against n_eff.
                p_harm = sign_p(harms, n_eff)
                if p_harm < 0.05 and mean_var > mean_base + TIE_TOLERANCE:
                    any_significant_harm = True
            summary["any_significant_harm"] = any_significant_harm
            summary["detector_fires"] = statistics.mean(r["detector_fires"] for r in variant_results)
            recalls = [r["recall"] for r in variant_results if r["recall"] is not None]
            precisions = [r["precision"] for r in variant_results if r["precision"] is not None]
            summary["recall"] = statistics.mean(recalls) if recalls else None
            summary["precision"] = statistics.mean(precisions) if precisions else None
            summary["sched_cores_scanned"] = statistics.mean(r["sched_cores_scanned"] for r in variant_results)
            summary_rows.append(summary)

            print(f"{workload_key:16} penalty={penalty}ms {cfg_label:20} "
                  f"p95_wait: {summary['p95_wait_base']:.3f}->{summary['p95_wait_var']:.3f} "
                  f"({summary['p95_wait_pct']:+.1f}%) sign_p={format_p(summary['p95_wait_sign_p'])} "
                  f"harm={any_significant_harm}  fires={summary['detector_fires']:.1f} "
                  f"recall={summary['recall']}  precision={summary['precision']}")

    with open(f"{DATA_DIR}results_task6_threshold_grid{SUFFIX}_{workload_key}{chunk_suffix}_perseed.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(per_seed_rows[0].keys()))
        w.writeheader()
        w.writerows(per_seed_rows)

    with open(f"{DATA_DIR}results_task6_threshold_grid{SUFFIX}_{workload_key}{chunk_suffix}_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)

    print(f"\nWrote {DATA_DIR}results_task6_threshold_grid{SUFFIX}_{workload_key}{chunk_suffix}_perseed.csv and _summary.csv")


if __name__ == "__main__":
    main()
