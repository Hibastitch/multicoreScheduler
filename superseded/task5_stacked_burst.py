"""
Task 5: stacked_burst is the key experiment -- see Readme.md's
2026-09-26o pre-registration. PRIMARY metric: p95 wait. SECONDARY: mean
slowdown, p95 slowdown, makespan_excess, avg wait. Makespan reported for
continuity, not used for conclusions (2026-09-26m: it's a weak metric,
dominated by the workload's own longest task).

n=30 paired seeds, transition mode, penalty in {0.0, 2.0}, intensity in
{low, medium, high}. Every comparison gets a paired sign test AND a
Wilcoxon signed-rank test (the sign test alone missed the tail-clipping
shape back in Task 1 -- see 2026-09-26c's follow-up). Writes
results_task5_stacked_burst.csv.

Seeds: stacked_burst is deliberately NOT in WorkloadGenerator.
PROFILE_NAMES (opt-in, doesn't touch Experiment.py's default grid), so
paired_compare.seed_base() (which indexes PROFILE_NAMES) can't be used
for it -- a separate, clearly-out-of-range base is used instead so it
can never collide with any real profile/intensity seed range (those top
out at 4200 for deadline_driven/high).
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "simulator"))
import csv
import statistics

from paired_compare import (
    run_pair, summarize_metric, print_summary,
    wilcoxon_signed_rank, format_p,
)
import diagnostics

N_REPS = 30
PROFILE = "stacked_burst"
INTENSITIES = ["low", "medium", "high"]
PENALTIES = [0.0, 2.0]
SEED_BASE = {"low": 5000, "medium": 5100, "high": 5200}

METRICS = ["p95_wait", "avg_slowdown", "p95_slowdown", "makespan_excess", "avg_wait", "makespan"]


def make_diag_procs():
    idle_state = {}
    imbalance_state = {}
    procs = [
        diagnostics.idle_while_waiting_sampler(idle_state),
        diagnostics.queue_imbalance_sampler(imbalance_state),
    ]
    return procs, {"idle_state": idle_state, "imbalance_state": imbalance_state}


def extract(row, metric):
    """Metrics not already top-level in run_pair()'s row dict live in ['summary']."""
    if metric in row:
        return row[metric]
    return row["summary"][metric]


def main():
    csv_rows = []
    for intensity in INTENSITIES:
        base = SEED_BASE[intensity]
        for penalty in PENALTIES:
            print(f"\n{'=' * 90}")
            print(f"stacked_burst / {intensity} / penalty={penalty}ms / transition / n={N_REPS} "
                  f"(seeds {base}-{base + N_REPS - 1})")
            print("=" * 90)

            rows = [
                run_pair(PROFILE, intensity, base + rep,
                         balancer_kwargs={"migration_penalty": penalty, "newidle_mode": "transition"},
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
                print_summary("stacked_burst", s)

                diffs = [extract(r["burst_aware"], metric) - extract(r["baseline"], metric) for r in rows]
                wr = wilcoxon_signed_rank(diffs)
                print(f"    Wilcoxon: n={wr['n']} (zeros dropped={wr['n_zeros_dropped']})  "
                      f"z={wr['z']:+.3f}  p={format_p(wr['p'])}")

                csv_rows.append(dict(
                    intensity=intensity, penalty=penalty, metric=metric,
                    baseline_mean=s["baseline_mean"], baseline_stdev=s["baseline_stdev"],
                    burst_aware_mean=s["burst_aware_mean"], burst_aware_stdev=s["burst_aware_stdev"],
                    mean_diff=s["mean_diff"], ci95_lo=s["ci95"][0], ci95_hi=s["ci95"][1],
                    wins=s["wins"], n_eff=s["n_eff"], ties=s["ties"], sign_p=s["p_sign"],
                    wilcoxon_n=wr["n"], wilcoxon_zeros_dropped=wr["n_zeros_dropped"],
                    wilcoxon_z=wr["z"], wilcoxon_p=wr["p"],
                ))

            # --- diagnostics-only summaries (not part of the primary/secondary metric set) ---
            idle_base = [r["baseline"]["idle_state"]["idle_while_waiting_time"] for r in rows]
            idle_burst = [r["burst_aware"]["idle_state"]["idle_while_waiting_time"] for r in rows]
            print(f"\n  idle_while_waiting_time: baseline={statistics.mean(idle_base):.1f}  "
                  f"burst_aware={statistics.mean(idle_burst):.1f}")

            qi_base = [diagnostics.summarize_queue_imbalance(
                r["baseline"]["imbalance_state"]["queue_imbalance_samples"], r["baseline"]["ground_truth_bursts"])
                for r in rows]
            qi_burst = [diagnostics.summarize_queue_imbalance(
                r["burst_aware"]["imbalance_state"]["queue_imbalance_samples"], r["burst_aware"]["ground_truth_bursts"])
                for r in rows]
            db_mean_base = statistics.mean(q["during_burst_mean"] for q in qi_base if q["during_burst_mean"] is not None)
            db_mean_burst = statistics.mean(q["during_burst_mean"] for q in qi_burst if q["during_burst_mean"] is not None)
            print(f"  queue_imbalance during-burst mean: baseline={db_mean_base:.3f}  burst_aware={db_mean_burst:.3f}")

            fires = [r["burst_aware"]["balancer"].detector_fires for r in rows]
            attempts = [r["burst_aware"]["balancer"].burst_balance_attempts for r in rows]
            btmigs = [r["burst_aware"]["balancer"].burst_triggered_migrations for r in rows]
            print(f"  funnel: detector_fires={statistics.mean(fires):.1f}  "
                  f"burst_balance_attempts={statistics.mean(attempts):.1f}  "
                  f"burst_triggered_migrations={statistics.mean(btmigs):.1f}")

            dq_list = [diagnostics.detector_quality(r["burst_aware"]["balancer"].detector_fire_times,
                                                     r["burst_aware"]["ground_truth_bursts"]) for r in rows]
            recalls = [d["recall"] for d in dq_list if d["recall"] is not None]
            precisions = [d["precision"] for d in dq_list if d["precision"] is not None]
            latencies = [d["avg_detection_latency"] for d in dq_list if d["avg_detection_latency"] is not None]
            print(f"  detector_quality: recall={statistics.mean(recalls):.3f}  "
                  f"precision={statistics.mean(precisions):.3f}  "
                  f"avg_detection_latency={statistics.mean(latencies):.3f}"
                  if recalls and precisions and latencies else "  detector_quality: insufficient data")

            lt_tagged_all, lt_any_all = [], []
            for r in rows:
                lt = diagnostics.lead_time_per_burst(
                    r["baseline"]["migration_events"], r["burst_aware"]["migration_events"],
                    r["baseline"]["ground_truth_bursts"],
                )
                lt_tagged_all.extend(lt["vs_burst_tagged"])
                lt_any_all.extend(lt["vs_any_migration"])
            print(f"  lead_time vs_burst_tagged: n={len(lt_tagged_all)}  "
                  f"mean={statistics.mean(lt_tagged_all):.3f}" if lt_tagged_all else "  lead_time vs_burst_tagged: no matches")
            print(f"  lead_time vs_any_migration: n={len(lt_any_all)}  "
                  f"mean={statistics.mean(lt_any_all):.3f}" if lt_any_all else "  lead_time vs_any_migration: no matches")

    with open("results_task5_stacked_burst.csv", "w", newline="") as f:
        keys = list(csv_rows[0].keys())
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(csv_rows)
    print("\nWrote results_task5_stacked_burst.csv")


if __name__ == "__main__":
    main()
