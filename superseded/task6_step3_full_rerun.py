"""
Task 6, Step 3: full re-run with A+B+C on for BOTH schedulers --
per_cpu_last_balance=True, load_model="runnable", imbalance_model="kernel".
transition mode, n=30 paired, same seeds as every prior run of these
cases, workload-equality assertion on (run_pair() already does this on
every call). Primary metric p95_wait; secondary mean/p95 slowdown,
avg_wait, makespan_excess (makespan reported, not used for conclusions).
Sign test + Wilcoxon on each. Plus RQ4 cost accounting (cores scanned,
levels walked by trigger, total migrations, % extra work vs % p95_wait
change) and the new machine-wide drain metric.

Cases:
  - stacked_burst: low/medium/high x penalty {0, 2}ms (Task 5's seeds)
  - bursty/high, heavy_tail/high x penalty {0, 2}ms (regression checks,
    Experiment.py's seed scheme)
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "simulator"))
import csv
import statistics

from paired_compare import (
    run_pair, summarize_metric, print_summary, seed_base,
    wilcoxon_signed_rank, format_p,
)
import diagnostics

N_REPS = 30
PENALTIES = [0.0, 2.0]
METRICS = ["p95_wait", "avg_slowdown", "p95_slowdown", "avg_wait", "makespan_excess", "makespan"]

ABC_BALANCER_KWARGS = {"newidle_mode": "transition", "per_cpu_last_balance": True,
                       "imbalance_model": "kernel"}
ABC_WORKLOAD_KWARGS = {"load_model": "runnable"}

STACKED_SEED_BASE = {"low": 5000, "medium": 5100, "high": 5200}

CASES = (
    [("stacked_burst", intensity, STACKED_SEED_BASE[intensity]) for intensity in ["low", "medium", "high"]]
    + [(p, "high", seed_base(p, "high")) for p in ["bursty", "heavy_tail"]]
)


def make_diag_procs():
    idle_state, imbalance_state, maxq_state = {}, {}, {}
    procs = [
        diagnostics.idle_while_waiting_sampler(idle_state),
        diagnostics.queue_imbalance_sampler(imbalance_state),
        diagnostics.machine_wide_queue_sampler(maxq_state),
    ]
    return procs, {"idle_state": idle_state, "imbalance_state": imbalance_state, "maxq_state": maxq_state}


def extract(row, metric):
    return row[metric] if metric in row else row["summary"][metric]


def main():
    csv_rows = []
    for profile, intensity, base in CASES:
        for penalty in PENALTIES:
            print(f"\n{'=' * 95}")
            print(f"{profile} / {intensity} / penalty={penalty}ms / A+B+C / transition / n={N_REPS} "
                  f"(seeds {base}-{base + N_REPS - 1})")
            print("=" * 95)

            balancer_kwargs = dict(ABC_BALANCER_KWARGS, migration_penalty=penalty)
            rows = [
                run_pair(profile, intensity, base + rep,
                         balancer_kwargs=balancer_kwargs, workload_kwargs=ABC_WORKLOAD_KWARGS,
                         extra_processes_factory=make_diag_procs)
                for rep in range(N_REPS)
            ]

            for metric in METRICS:
                rows_view = [
                    {"baseline": {metric: extract(r["baseline"], metric)},
                     "burst_aware": {metric: extract(r["burst_aware"], metric)}}
                    for r in rows
                ]
                s = summarize_metric(rows_view, metric, lower_is_better=True)
                print_summary(f"{profile}/{intensity}", s)

                diffs = [extract(r["burst_aware"], metric) - extract(r["baseline"], metric) for r in rows]
                wr = wilcoxon_signed_rank(diffs)
                print(f"    Wilcoxon: n={wr['n']} (zeros dropped={wr['n_zeros_dropped']})  "
                      f"z={wr['z']:+.3f}  p={format_p(wr['p'])}")

                csv_rows.append(dict(
                    profile=profile, intensity=intensity, penalty=penalty, metric=metric,
                    baseline_mean=s["baseline_mean"], baseline_stdev=s["baseline_stdev"],
                    burst_aware_mean=s["burst_aware_mean"], burst_aware_stdev=s["burst_aware_stdev"],
                    mean_diff=s["mean_diff"], ci95_lo=s["ci95"][0], ci95_hi=s["ci95"][1],
                    wins=s["wins"], n_eff=s["n_eff"], ties=s["ties"], sign_p=s["p_sign"],
                    wilcoxon_n=wr["n"], wilcoxon_z=wr["z"], wilcoxon_p=wr["p"],
                ))

            # --- RQ4 cost accounting ---
            def mean_of(side, key):
                return statistics.mean(r[side]["summary"][key] for r in rows)

            sched_scanned_base = mean_of("baseline", "sched_cores_scanned")
            sched_scanned_burst = mean_of("burst_aware", "sched_cores_scanned")
            periodic_base = mean_of("baseline", "periodic_levels_walked")
            periodic_burst = mean_of("burst_aware", "periodic_levels_walked")
            newidle_base = mean_of("baseline", "newidle_levels_walked")
            newidle_burst = mean_of("burst_aware", "newidle_levels_walked")
            burst_lvl_base = mean_of("baseline", "burst_balance_levels_walked")
            burst_lvl_burst = mean_of("burst_aware", "burst_balance_levels_walked")
            mig_base = statistics.mean(r["baseline"]["migrations"] for r in rows)
            mig_burst = statistics.mean(r["burst_aware"]["migrations"] for r in rows)

            p95_base = statistics.mean(r["baseline"]["p95_wait"] for r in rows)
            p95_burst = statistics.mean(r["burst_aware"]["p95_wait"] for r in rows)
            pct_p95_change = ((p95_burst - p95_base) / p95_base * 100) if p95_base else float("nan")
            pct_extra_scan = ((sched_scanned_burst - sched_scanned_base) / sched_scanned_base * 100) if sched_scanned_base else float("nan")
            pct_extra_mig = ((mig_burst - mig_base) / mig_base * 100) if mig_base else float("nan")

            print(f"\n  RQ4 cost: sched_cores_scanned baseline={sched_scanned_base:.0f} "
                  f"burst_aware={sched_scanned_burst:.0f} ({pct_extra_scan:+.1f}%)")
            print(f"  periodic_levels_walked: baseline={periodic_base:.0f} burst_aware={periodic_burst:.0f}")
            print(f"  newidle_levels_walked:  baseline={newidle_base:.0f} burst_aware={newidle_burst:.0f}")
            print(f"  burst_balance_levels_walked: baseline={burst_lvl_base:.0f} burst_aware={burst_lvl_burst:.0f}")
            print(f"  total_migrations: baseline={mig_base:.1f} burst_aware={mig_burst:.1f} ({pct_extra_mig:+.1f}%)")
            print(f"  p95_wait change: {pct_p95_change:+.1f}%   vs extra scanning work: {pct_extra_scan:+.1f}%   "
                  f"vs extra migrations: {pct_extra_mig:+.1f}%")

            csv_rows.append(dict(
                profile=profile, intensity=intensity, penalty=penalty, metric="RQ4_cost",
                baseline_mean=sched_scanned_base, burst_aware_mean=sched_scanned_burst,
                mean_diff=pct_extra_scan, wins=mig_base, n_eff=mig_burst, ties=pct_extra_mig,
                sign_p=pct_p95_change, baseline_stdev=periodic_base, burst_aware_stdev=periodic_burst,
                ci95_lo=newidle_base, ci95_hi=newidle_burst, wilcoxon_n=burst_lvl_base,
                wilcoxon_z=burst_lvl_burst, wilcoxon_p=None,
            ))

            # --- machine-wide drain metric ---
            drain_base_all, drain_burst_all = [], []
            for r in rows:
                gt = r["baseline"]["ground_truth_bursts"]
                db = diagnostics.machine_wide_drain_times(r["baseline"]["maxq_state"]["max_qlen_samples"], gt)
                dba = diagnostics.machine_wide_drain_times(r["burst_aware"]["maxq_state"]["max_qlen_samples"], gt)
                drain_base_all.extend(d for d in db if d is not None)
                drain_burst_all.extend(d for d in dba if d is not None)
            if drain_base_all and drain_burst_all:
                print(f"  machine-wide drain time: baseline mean={statistics.mean(drain_base_all):.2f}ms  "
                      f"burst_aware mean={statistics.mean(drain_burst_all):.2f}ms  "
                      f"(n_bursts base={len(drain_base_all)} burst_aware={len(drain_burst_all)})")

    with open("results_task6_step3_full_rerun.csv", "w", newline="") as f:
        keys = list(csv_rows[0].keys())
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(csv_rows)
    print("\nWrote results_task6_step3_full_rerun.csv")


if __name__ == "__main__":
    main()
