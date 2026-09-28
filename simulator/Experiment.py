"""
Phase 2 harness: 2 schedulers x 5 profiles x 3 intensity levels,
N repetitions each, averaged. Writes results to CSV.

This is the tool that actually answers "is burst-aware better," not
single-seed spot checks -- individual runs (as we found with `mixed`
at medium intensity) can look identical, worse, or better purely from
random-number-stream divergence between the two schedulers. Only
averages across many seeds separate real effect from noise.

FINAL CONFIGURATION (2026-09-27i, see Readme.md): run_all() defaults
now match LoadBalancer's new defaults (transition newidle, per_cpu_
last_balance, kernel imbalance model) plus runnable load_model, and
BurstAwareLoadBalancer separately defaults its BurstDetector to
arrival_rate_threshold=1.5 + combine="and". `results.csv` in this repo
predates 2026-09-27i and was generated under the OLD defaults -- it is
not comparable to a fresh run_all() call without reproducing them:
run_all(balancer_kwargs=dict(newidle_mode="legacy_ema",
per_cpu_last_balance=False, imbalance_model="legacy"),
load_model="legacy") reproduces the shared LoadBalancer/load_model
flags for BOTH schedulers (balancer_kwargs is passed to both classes
here, and LoadBalancer's __init__ does not accept detector kwargs).
Reproducing the pre-calibration detector (arrival_rate_threshold=0.8,
combine="or") additionally needs a burst-aware-only kwargs dict, the
way task6_threshold_grid.py's run_baseline()/run_variant() split does
-- run_all()'s single shared balancer_kwargs can't express "different
kwargs per scheduler" as written.
"""

import csv
import statistics

from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from WorkloadGenerator import PROFILE_NAMES
from Main import run_simulation

SCHEDULERS = {
    "baseline": LoadBalancer,
    "burst_aware": BurstAwareLoadBalancer,
}

INTENSITY_LEVELS = ["low", "medium", "high"]
N_REPETITIONS = 10
METRIC_KEYS = [
    "avg_wait", "avg_turnaround", "makespan",
    "avg_migrations_per_task", "migration_rate_per_ms",
    "burst_response_latency", "cpu_util_mean",
    "deadline_miss_rate", "avg_lateness",
    "tree_avg_nodes_per_pick", "tree_rotations",
    "sched_cores_scanned", "periodic_levels_walked", "newidle_levels_walked",
    "placement_levels_walked_total", "placement_levels_per_call", "burst_balance_levels_walked",
]


def run_all(n_repetitions=N_REPETITIONS, out_path="results.csv", balancer_kwargs=None, load_model="runnable"):
    rows = []

    # Fixed integer IDs (not Python's hash(), which is randomized per
    # process since PYTHONHASHSEED defaults to random in Python 3.3+) so
    # seeds -- and therefore "the same experiment" -- are reproducible
    # across runs and across machines.
    PROFILE_IDS = {name: i for i, name in enumerate(PROFILE_NAMES)}
    INTENSITY_IDS = {name: i for i, name in enumerate(INTENSITY_LEVELS)}

    for profile in PROFILE_NAMES:
        for intensity in INTENSITY_LEVELS:
            for sched_name, sched_cls in SCHEDULERS.items():
                per_rep = {k: [] for k in METRIC_KEYS}

                for rep in range(n_repetitions):
                    seed = 1000 * PROFILE_IDS[profile] + 100 * INTENSITY_IDS[intensity] + rep

                    m, b, gt, migs, _, _ = run_simulation(
                        profile, sched_cls, intensity_level=intensity, seed=seed,
                        balancer_kwargs=balancer_kwargs, load_model=load_model,
                    )
                    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
                    if s["completed"] == 0:
                        continue
                    for k in METRIC_KEYS:
                        v = s.get(k)
                        if v is not None:
                            per_rep[k].append(v)

                row = {"profile": profile, "intensity": intensity, "scheduler": sched_name,
                       "n_reps": n_repetitions}
                for k in METRIC_KEYS:
                    vals = per_rep[k]
                    row[f"{k}_mean"] = statistics.mean(vals) if vals else None
                    row[f"{k}_stdev"] = statistics.stdev(vals) if len(vals) > 1 else 0.0
                rows.append(row)
                print(f"{profile:16s} {intensity:6s} {sched_name:12s} "
                      f"makespan={row['makespan_mean']:.1f}±{row['makespan_stdev']:.1f}  "
                      f"wait={row['avg_wait_mean']:.2f}±{row['avg_wait_stdev']:.2f}")

    if rows:
        keys = list(rows[0].keys())
        with open(out_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print(f"\nWrote {len(rows)} rows to {out_path}")

    return rows


if __name__ == "__main__":
    run_all()