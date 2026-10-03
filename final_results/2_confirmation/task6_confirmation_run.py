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

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
import csv
import json
import math
import os
import statistics
import sys

# TASK 8 v2 RE-RUN (2026-09-29, docs/NOTEBOOK.md 2026-09-29c pre-
# registration, Step 4 "prepare, don't run"): TASK8_V2=1 in the
# environment (a) shifts every workload's seed_base by +20000 -- FRESH
# 40000+ seeds, 100 apart per workload, matching the existing spacing
# pattern, never used by the original 20000+ confirmation run, Task 7's
# 30000+ audit, or Task 8's own 50000+/60000+ audit scripts -- and (b)
# writes to _v2-suffixed output files instead of overwriting the
# original confirmation CSVs. The code's defaults already carry the six
# fidelity fixes (Step 3) with no kwarg changes needed here.
V2_SUFFIX = "_v2" if os.environ.get("TASK8_V2") else ""
V2_SEED_OFFSET = 20000 if os.environ.get("TASK8_V2") else 0

# TASK 9 v3 RE-RUN (2026-09-29, docs/NOTEBOOK.md 2026-09-29h pre-
# registration (f)/(g), Step 4 "prepare, don't run"): TASK9_V3=1 (a)
# shifts every workload's seed_base by +60000 from its BASE (20000+)
# value -- FRESH 80000+ seeds, 100 apart per workload; (b) writes to
# _v3-suffixed outputs; (c) adds penalty=0.5ms; (d) sets penalty_model=
# "ran_only" for baseline and every variant (Task 9a, applies
# universally -- not one of the things compared); (e) replaces the
# 3-variant VARIANTS dict with rule (g)'s 4: the selected config GATED,
# the same thresholds UNGATED, the original q2_a0.8_or UNGATED, and the
# runner-up UNGATED -- read from selected_config_v3.json, written by
# ../1_calibration_grid/task6_threshold_grid_recompute_harm.py's v3
# mode (run that first). If no config was selected there (rule (h)),
# VARIANTS is empty and main() reports that plainly instead of running
# anything.
V3 = bool(os.environ.get("TASK9_V3"))
V3_SUFFIX = "_v3" if V3 else ""
V3_SEED_OFFSET = 60000 if V3 else 0

# TASK 10 v4 RE-RUN (2026-09-30, docs/NOTEBOOK.md 2026-09-30d pre-
# registration, Step 3 "prepare, don't run"): TASK10_V4=1 (a) shifts
# every workload's seed_base by +100000 from its BASE (20000+) value --
# FRESH 120000+ seeds, 100 apart per workload; (b) writes to
# _v4-suffixed outputs; (c) keeps the 3-penalty sweep; (d) sets
# penalty_model="ran_only" universally (Task 9a, unchanged); (e)
# replaces VARIANTS with rule (d)'s 4: selected WITH the idle check,
# selected WITHOUT it, the original q2_a0.8_or WITHOUT it, and the
# runner-up WITH it -- read from selected_config_v4.json, written by
# ../1_calibration_grid/task6_threshold_grid_recompute_harm.py's v4
# mode (run that first). Empty VARIANTS is reported, not a crash, same
# as v3.
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

# REORGANIZATION (2026-10-01, docs/NOTEBOOK.md): old-version (non-v4)
# outputs now live in history/ -- v4 keeps writing here unchanged.
DATA_DIR = "" if V4 else "history/"

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from paired_compare import assert_same_workload, wilcoxon_signed_rank, format_p, TIE_TOLERANCE
import diagnostics

N_REPS = 30
PENALTIES = [0.0, 0.5, 2.0] if (V3 or V4) else [0.0, 2.0]

# FINAL configuration is now the plain default for both classes (2026-09-27i)
FINAL_KWARGS = {}
ORIGINAL_KWARGS = dict(queue_growth_threshold=2, arrival_rate_threshold=0.8, combine="or")
RUNNER_UP_KWARGS = dict(queue_growth_threshold=4, arrival_rate_threshold=1.5, combine="or")


def _load_v3_variants():
    """rule (g): 4 variants built from selected_config_v3.json (written
    by ../1_calibration_grid/task6_threshold_grid_recompute_harm.py's v3
    mode). Returns {} if that file doesn't exist yet or no config was
    selected there (rule (h): nothing to confirm)."""
    # REORGANIZATION (2026-10-01, docs/NOTEBOOK.md): v3 is an old version
    # now, so its selected_config lives in the grid folder's history/.
    path = pathlib.Path(__file__).resolve().parents[1] / "1_calibration_grid" / "history" / "selected_config_v3.json"
    try:
        with open(path) as f:
            sel = json.load(f)
    except FileNotFoundError:
        print(f"selected_config_v3.json not found at {path} -- run the v3 grid and "
              f"task6_threshold_grid_recompute_harm.py first.")
        return {}
    if not sel.get("selected"):
        print(f"selected_config_v3.json says NO CONFIG SELECTED (harm_free_configs="
              f"{sel.get('harm_free_configs')}) -- nothing to confirm, per rule (h).")
        return {}

    def _thresholds(entry):
        return dict(queue_growth_threshold=entry["queue_growth_threshold"],
                    arrival_rate_threshold=entry["arrival_rate_threshold"], combine=entry["combine"])

    selected = _thresholds(sel["selected"])
    variants = {
        "selected_gated": dict(selected, burst_gap_gate=True),
        "selected_ungated": dict(selected, burst_gap_gate=False),
        "original_q2_a0.8_or_ungated": dict(ORIGINAL_KWARGS, burst_gap_gate=False),
    }
    if sel.get("runner_up"):
        variants["runner_up_ungated"] = dict(_thresholds(sel["runner_up"]), burst_gap_gate=False)
    else:
        print("selected_config_v3.json has no runner_up (fewer than 2 harm-free configs) "
              "-- confirmation will run without a runner-up variant.")
    return variants


def _load_v4_variants():
    """Task 10 pre-registration (d): 4 variants built from
    selected_config_v4.json (written by ../1_calibration_grid/
    task6_threshold_grid_recompute_harm.py's v4 mode). Unlike v3's
    runner_up_ungated, the runner-up is tested WITH the mechanism here
    (burst_idle_check=True) -- the mechanism itself is what's under
    test, not just the threshold choice. Returns {} if the file doesn't
    exist yet or no config was selected there."""
    path = pathlib.Path(__file__).resolve().parents[1] / "1_calibration_grid" / "selected_config_v4.json"
    try:
        with open(path) as f:
            sel = json.load(f)
    except FileNotFoundError:
        print(f"selected_config_v4.json not found at {path} -- run the v4 grid and "
              f"task6_threshold_grid_recompute_harm.py first.")
        return {}
    if not sel.get("selected"):
        print(f"selected_config_v4.json says NO CONFIG SELECTED (harm_free_configs="
              f"{sel.get('harm_free_configs')}) -- nothing to confirm.")
        return {}

    def _thresholds(entry):
        return dict(queue_growth_threshold=entry["queue_growth_threshold"],
                    arrival_rate_threshold=entry["arrival_rate_threshold"], combine=entry["combine"])

    selected = _thresholds(sel["selected"])
    variants = {
        "selected_checked": dict(selected, burst_idle_check=True),
        "selected_unchecked": dict(selected, burst_idle_check=False),
        "original_q2_a0.8_or_unchecked": dict(ORIGINAL_KWARGS, burst_idle_check=False),
    }
    if sel.get("runner_up"):
        variants["runner_up_checked"] = dict(_thresholds(sel["runner_up"]), burst_idle_check=True)
    else:
        print("selected_config_v4.json has no runner_up (fewer than 2 harm-free configs) "
              "-- confirmation will run without a runner-up variant.")
    return variants


if V4:
    VARIANTS = _load_v4_variants()
elif V3:
    VARIANTS = _load_v3_variants()
else:
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

_SEED_OFFSET = V4_SEED_OFFSET or V3_SEED_OFFSET or V2_SEED_OFFSET
if _SEED_OFFSET:
    for _wl in WORKLOADS.values():
        _wl["seed_base"] += _SEED_OFFSET

METRICS = ["p95_wait", "p99_wait", "avg_wait", "avg_slowdown", "p95_slowdown", "makespan_excess"]
COST_METRICS = ["sched_cores_scanned", "burst_balance_levels_walked", "total_migrations"]


def sign_p(wins, n):
    if n == 0:
        return 1.0
    k = max(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def run_baseline(profile, intensity, seed, penalty, overrides, n_tasks):
    # TASK 9a: penalty_model="ran_only" applies to baseline too, per the
    # pre-registration -- it's a cost-model correction, not one of the
    # 4 things rule (g) compares.
    kwargs = {"migration_penalty": penalty}
    if V3 or V4:
        kwargs["penalty_model"] = "ran_only"
    m, b, gt, migs, logger, plan = run_simulation(
        profile, LoadBalancer, intensity_level=intensity, seed=seed,
        balancer_kwargs=kwargs, intensity_overrides=overrides, n_tasks=n_tasks,
    )
    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    row = dict(plan=plan, gt=gt)
    for metric in METRICS + COST_METRICS:
        row[metric] = s[metric]
    return row


def run_variant(profile, intensity, seed, penalty, overrides, n_tasks, variant_kwargs):
    kwargs = dict(variant_kwargs, migration_penalty=penalty)
    if V3 or V4:
        # burst_gap_gate/burst_idle_check is already IN variant_kwargs for v3/v4
        kwargs["penalty_model"] = "ran_only"
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

    # TASK 9 rule (h) / TASK 10: if no config was harm-free in the grid,
    # _load_v3_variants()/_load_v4_variants() already printed why and
    # returned {} -- nothing to confirm. Report and stop, don't crash on
    # an empty per_seed_rows[0] below.
    if (V3 or V4) and not VARIANTS:
        print(f"\nNO CONFIG SELECTED (see message above) -- {workload_key}: nothing to confirm. "
              f"No output written.")
        return

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

    with open(f"{DATA_DIR}results_task6_confirmation{SUFFIX}_{workload_key}_perseed.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(per_seed_rows[0].keys()))
        w.writeheader()
        w.writerows(per_seed_rows)

    with open(f"{DATA_DIR}results_task6_confirmation{SUFFIX}_{workload_key}_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)

    print(f"\nWrote {DATA_DIR}results_task6_confirmation{SUFFIX}_{workload_key}_perseed.csv and _summary.csv")


if __name__ == "__main__":
    main()
