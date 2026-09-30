"""
Measurement only (2026-10-01, docs/NOTEBOOK.md): how many real
Core.is_idle() reads burst_idle_check's scan performs per trigger, and
how that compares to the existing sched_cores_scanned counters -- NOT
included in sched_cores_scanned itself (see simulator/BurstScheduler.py's
idle_check_runs/idle_check_cores_read comments, added purely to be read
here). No rule, selection, or default changed; this script adds no new
behavior of its own either, it only reads counters that already exist
on the balancer after a run.

Re-runs the EXACT same (workload, penalty, seed) triples as the
published v4 confirmation (seeds 120000-120029, the committed
selected_config_v4.json thresholds), so the runs measured here are
the SAME runs behind results_task6_confirmation_v4_*_MAIN_TABLE.csv --
not a fresh sample.

SAFETY CHECK, before writing anything: every run's p95_wait/avg_wait/
total_migrations (baseline AND selected_checked) is compared against
the committed per-seed CSV and must match exactly (within 1e-9, to
absorb only float<->str round-tripping, not real differences). If
anything differs, this script stops and reports without writing
results -- the new counters would be meaningless if the underlying
run diverged from the published one.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
import csv
import json
import statistics

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer

CONF_DIR = pathlib.Path(__file__).resolve().parents[2] / "final_results" / "2_confirmation"
GRID_DIR = pathlib.Path(__file__).resolve().parents[2] / "final_results" / "1_calibration_grid"

with open(GRID_DIR / "selected_config_v4.json") as f:
    _sel = json.load(f)["selected"]
assert (_sel["queue_growth_threshold"], _sel["arrival_rate_threshold"], _sel["combine"]) == (8, 1.5, "or"), \
    f"selected_config_v4.json changed since this script was written: {_sel}"
SELECTED_KWARGS = dict(queue_growth_threshold=8, arrival_rate_threshold=1.5, combine="or")

N_REPS = 30
PENALTIES = [0.0, 0.5, 2.0]

# Exact same profile/intensity/overrides/n_tasks/seed_base as
# final_results/2_confirmation/task6_confirmation_run.py's WORKLOADS
# dict under TASK10_V4=1 -- verified directly against that module
# before writing this (seed_base = the v1 base + the +100000 v4 offset).
WORKLOADS = {
    "stacked_medium":  dict(profile="stacked_burst", intensity="medium", overrides=None,
                             n_tasks=200, seed_base=120100),
    "stacked_high":    dict(profile="stacked_burst", intensity="high", overrides=None,
                             n_tasks=200, seed_base=120200),
    "rate1.5_s12":     dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 12, "arrival_rate_during_burst": 1.5,
                                        "burst_duration": 8.0, "inter_burst_interval": 60},
                             n_tasks=120, seed_base=121300),
    "rate3.0_s12":     dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 12, "arrival_rate_during_burst": 3.0,
                                        "burst_duration": 4.0, "inter_burst_interval": 60},
                             n_tasks=120, seed_base=121500),
    "bursty_high_s64": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 64, "burst_duration": 64 / 3.75},
                             n_tasks=640, seed_base=120400),
    # Added 2026-10-01c to fill the 2 n/a cost-table cells: bursty_high_s24
    # fires rarely (0.067/0.033 per run at penalty=0.5/2.0) but not zero, so
    # it wasn't in the original 5-workload set -- see docs/NOTEBOOK.md.
    "bursty_high_s24": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 24, "burst_duration": 24 / 3.75},
                             n_tasks=240, seed_base=120300),
}


def load_committed(workload):
    path = CONF_DIR / f"results_task6_confirmation_v4_{workload}_perseed.csv"
    rows = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["variant"] != "selected_checked":
                continue
            rows[(r["penalty"], int(r["seed"]))] = r
    return rows


def run_baseline(profile, intensity, seed, penalty, overrides, n_tasks):
    kwargs = {"migration_penalty": penalty, "penalty_model": "ran_only"}
    m, b, gt, migs, logger, plan = run_simulation(
        profile, LoadBalancer, intensity_level=intensity, seed=seed,
        balancer_kwargs=kwargs, intensity_overrides=overrides, n_tasks=n_tasks,
    )
    return m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)


def run_variant(profile, intensity, seed, penalty, overrides, n_tasks):
    kwargs = dict(SELECTED_KWARGS, burst_idle_check=True, migration_penalty=penalty,
                  penalty_model="ran_only")
    m, b, gt, migs, logger, plan = run_simulation(
        profile, BurstAwareLoadBalancer, intensity_level=intensity, seed=seed,
        balancer_kwargs=kwargs, intensity_overrides=overrides, n_tasks=n_tasks,
    )
    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    return s, b


def close(a, b, tol=1e-9):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


def main():
    mismatches = []
    per_cell_rows = []
    n_checked = 0

    for wl_name, wl in WORKLOADS.items():
        committed = load_committed(wl_name)
        for penalty in PENALTIES:
            pen_key = str(penalty)
            base_scanned, var_scanned = [], []
            idle_runs_list, idle_reads_list = [], []

            for rep in range(N_REPS):
                seed = wl["seed_base"] + rep
                committed_row = committed.get((pen_key, seed))
                if committed_row is None:
                    mismatches.append(f"{wl_name} pen={pen_key} seed={seed}: no committed "
                                       f"selected_checked row found -- cannot safety-check")
                    continue

                base_s = run_baseline(wl["profile"], wl["intensity"], seed, penalty,
                                       wl["overrides"], wl["n_tasks"])
                var_s, b = run_variant(wl["profile"], wl["intensity"], seed, penalty,
                                        wl["overrides"], wl["n_tasks"])

                checks = [
                    ("baseline_p95_wait", base_s["p95_wait"]),
                    ("baseline_avg_wait", base_s["avg_wait"]),
                    ("baseline_total_migrations", base_s["total_migrations"]),
                    ("variant_p95_wait", var_s["p95_wait"]),
                    ("variant_avg_wait", var_s["avg_wait"]),
                    ("variant_total_migrations", var_s["total_migrations"]),
                ]
                for col, val in checks:
                    n_checked += 1
                    if not close(val, committed_row[col]):
                        mismatches.append(f"{wl_name} pen={pen_key} seed={seed} {col}: "
                                           f"recomputed={val} committed={committed_row[col]}")

                base_scanned.append(base_s["sched_cores_scanned"])
                var_scanned.append(var_s["sched_cores_scanned"])
                idle_runs_list.append(b.idle_check_runs)
                idle_reads_list.append(b.idle_check_cores_read)

            if not idle_runs_list:
                continue
            mean_runs = statistics.mean(idle_runs_list)
            mean_reads = statistics.mean(idle_reads_list)
            mean_base_scanned = statistics.mean(base_scanned)
            mean_var_scanned = statistics.mean(var_scanned)
            per_cell_rows.append(dict(
                workload=wl_name, penalty=pen_key, n_seeds=len(idle_runs_list),
                idle_check_runs_mean=mean_runs,
                idle_check_cores_read_mean=mean_reads,
                mean_cores_read_per_check=(sum(idle_reads_list) / sum(idle_runs_list)
                                            if sum(idle_runs_list) else 0.0),
                baseline_sched_cores_scanned_mean=mean_base_scanned,
                variant_sched_cores_scanned_mean=mean_var_scanned,
                idle_check_pct_of_baseline_scanned=(
                    100 * mean_reads / mean_base_scanned if mean_base_scanned else None),
            ))
            r = per_cell_rows[-1]
            print(f"{wl_name:16} pen={penalty:4} idle_check_runs={mean_runs:7.1f} "
                  f"idle_check_cores_read={mean_reads:8.1f} "
                  f"mean_cores/check={r['mean_cores_read_per_check']:5.2f} "
                  f"baseline_scanned={mean_base_scanned:8.1f} variant_scanned={mean_var_scanned:8.1f} "
                  f"idle_check_pct={r['idle_check_pct_of_baseline_scanned']:.2f}%")

    if mismatches:
        print(f"\nSAFETY CHECK FAILED ({len(mismatches)} mismatch(es) out of {n_checked} values "
              f"checked) -- results NOT written:")
        for m in mismatches:
            print("  " + m)
        return 1

    print(f"\nSAFETY CHECK PASSED: all {n_checked} recomputed values (baseline+selected_checked x "
          f"p95_wait/avg_wait/total_migrations, every seed) matched the committed per-seed CSVs "
          f"exactly (tolerance 1e-9, float<->str round-tripping only).")

    out_dir = pathlib.Path(__file__).resolve().parent
    out_csv = out_dir / "results_idle_check_cost.csv"
    cols = ["workload", "penalty", "n_seeds", "idle_check_runs_mean", "idle_check_cores_read_mean",
            "mean_cores_read_per_check", "baseline_sched_cores_scanned_mean",
            "variant_sched_cores_scanned_mean", "idle_check_pct_of_baseline_scanned"]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(per_cell_rows)
    print(f"Wrote {out_csv}")

    out_md = out_dir / "results_idle_check_cost.md"
    lines = [
        "Measurement only -- see this script's module docstring. Safety check PASSED: every "
        f"recomputed value ({n_checked} total) matched the committed v4 confirmation per-seed "
        "CSVs exactly; these are the same runs behind the published results, not a fresh sample.",
        "",
        "| workload | penalty | idle_check_runs | idle_check_cores_read | mean cores/check | "
        "baseline sched_cores_scanned | variant sched_cores_scanned | idle-check as % of baseline |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in per_cell_rows:
        # .3f/.4f, not .1f/.2f, for idle_check_runs_mean and the pct column:
        # bursty_high_s24's rare-fire cells (1/30 seeds) round to 0.0/0.00% at
        # coarser precision, indistinguishable from the workloads that never
        # run the check at all -- keep the nonzero visible (2026-10-01c).
        lines.append(f"| {r['workload']} | {r['penalty']} | {r['idle_check_runs_mean']:.3f} | "
                      f"{r['idle_check_cores_read_mean']:.1f} | {r['mean_cores_read_per_check']:.2f} | "
                      f"{r['baseline_sched_cores_scanned_mean']:.1f} | "
                      f"{r['variant_sched_cores_scanned_mean']:.1f} | "
                      f"{r['idle_check_pct_of_baseline_scanned']:.4f}% |")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
