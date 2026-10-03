"""
Diagnostic only (2026-09-30) -- NOT part of Task 9's confirmation or
selection process, and it changes NO rule: no simulator file is
edited, no default is touched. It answers a mechanistic question the
v3 confirmation compact table raised but can't itself show: what is
the burst_gap_gate (Task 9b) actually doing, per run, on the one
workload where selected_gated showed disqualifying harm
(bursty_high_s64, avg_wait@p0) versus a workload where it clearly
helped (stacked_high)?

The confirmation CSVs only ever saved summary/per-seed METRICS, never
raw event logs (see final_results/2_confirmation/*_perseed.csv's
header) -- so there is nothing on disk to re-derive this from, and
this script runs 5 FRESH seeds per (workload, config, penalty), never
used by any prior grid/confirmation/audit range in this repo:
bursty_high_s64 at 95000-95004, stacked_high at 95100-95104 (kept 100
apart, this repo's convention for per-workload seed blocks).

Per (workload, config, penalty), mean over the 5 seeds:
  - detector_fires: raw BurstDetector.is_burst()==True count (BEFORE
    the cooldown gate) -- "the detector triggered."
  - burst_triggers: post-cooldown domain-chain walks actually attempted
    (<=  detector_fires; see BurstScheduler.py's Task-4-funnel-counter
    comment). Each one visits every domain in domain_chain(core) once.
  - domains_allowed / domains_blocked: of those domain visits, how many
    the gap gate let through to _balance_domain() vs skipped for
    gap<2 (Topology.STATS.burst_balance_levels_walked minus
    balancer.gate_skipped_domains, and gate_skipped_domains itself).
    selected_ungated never runs the gate at all, so blocked=0 and
    allowed=every domain visited, by construction -- reported anyway,
    for the side-by-side contrast the user asked for.
  - migrations tagged periodic / newidle / burst (baseline only ever
    produces the first two -- it never calls BurstAwareLoadBalancer's
    on_task_placed at all, so "burst" is always 0 there structurally,
    not empirically).
  - of the burst-tagged migrations specifically, how many moved a task
    that had ALREADY run at least once (Task.last_ran_until is not
    None) -- Task 9a's own charging condition (LoadBalancer.py
    _do_migrate: penalty_model="ran_only" charges the migration
    penalty exactly when this is True), observed here rather than
    asserted.

How the tag/already-ran breakdown is captured WITHOUT touching
LoadBalancer.py: _do_migrate is monkeypatched at the class level for
the duration of each run only (records tag + already-ran off the
exact arguments the real call received, then immediately calls the
untouched original and returns its result), and the original is
restored right after -- every run's actual scheduling outcome is
bit-for-bit whatever the unmodified simulator would have produced.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
import csv
import json
import statistics

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from Topology import STATS

N_SEEDS = 5
PENALTIES = [0.0, 0.5, 2.0]

WORKLOADS = {
    "bursty_high_s64": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 64, "burst_duration": 64 / 3.75},
                             n_tasks=640, seed_base=95000),
    "stacked_high":    dict(profile="stacked_burst", intensity="high", overrides=None,
                             n_tasks=200, seed_base=95100),
}

_SELECTED_CONFIG_PATH = (pathlib.Path(__file__).resolve().parents[2] / "final_results"
                          / "1_calibration_grid" / "history" / "selected_config_v3.json")
with open(_SELECTED_CONFIG_PATH) as f:
    _sel = json.load(f)["selected"]
SELECTED_KWARGS = dict(queue_growth_threshold=_sel["queue_growth_threshold"],
                        arrival_rate_threshold=_sel["arrival_rate_threshold"], combine=_sel["combine"])
print(f"Diagnosing selected_gated/selected_ungated = {_sel['config']} "
      f"(queue_growth_threshold={_sel['queue_growth_threshold']}, "
      f"arrival_rate_threshold={_sel['arrival_rate_threshold']}, combine={_sel['combine']})")

VARIANTS = {
    "selected_gated":   dict(SELECTED_KWARGS, burst_gap_gate=True),
    "selected_ungated": dict(SELECTED_KWARGS, burst_gap_gate=False),
}


def _install_migration_trace(trace):
    """Returns (original, wrapper); caller installs/restores wrapper
    on LoadBalancer._do_migrate around exactly one run_simulation()
    call. wrapper reads task.last_ran_until (never writes it) before
    delegating to the untouched original -- zero effect on scheduling."""
    original = LoadBalancer._do_migrate

    def wrapper(self, task, src_core, dst_core, now, tag="periodic"):
        already_ran = task.last_ran_until is not None
        bucket = trace.setdefault(tag, {"count": 0, "already_ran": 0})
        bucket["count"] += 1
        if already_ran:
            bucket["already_ran"] += 1
        return original(self, task, src_core, dst_core, now, tag)

    return original, wrapper


def run_traced(profile, intensity, overrides, n_tasks, seed, balancer_cls, balancer_kwargs):
    trace = {}
    original, wrapper = _install_migration_trace(trace)
    LoadBalancer._do_migrate = wrapper
    try:
        m, b, gt, migs, logger, plan = run_simulation(
            profile, balancer_cls, intensity_level=intensity, seed=seed,
            balancer_kwargs=balancer_kwargs, intensity_overrides=overrides, n_tasks=n_tasks,
        )
        stats_snapshot = dict(burst_balance_levels_walked=STATS.burst_balance_levels_walked,
                               burst_check_calls=STATS.burst_check_calls)
    finally:
        LoadBalancer._do_migrate = original
    return b, trace, stats_snapshot


def _tag_count(trace, tag):
    return trace.get(tag, {}).get("count", 0)


def _tag_already_ran(trace, tag):
    return trace.get(tag, {}).get("already_ran", 0)


def _mean(vals):
    return statistics.mean(vals) if vals else 0.0


def collect_baseline(wl_cfg, penalty, seeds):
    perseed = []
    for seed in seeds:
        b, trace, snap = run_traced(wl_cfg["profile"], wl_cfg["intensity"], wl_cfg["overrides"],
                                     wl_cfg["n_tasks"], seed, LoadBalancer,
                                     dict(migration_penalty=penalty, penalty_model="ran_only"))
        perseed.append(dict(
            seed=seed, detector_fires=0, burst_triggers=0, domains_allowed=0, domains_blocked=0,
            mig_periodic=_tag_count(trace, "periodic"), mig_newidle=_tag_count(trace, "newidle"),
            mig_burst=_tag_count(trace, "burst"),
            mig_burst_already_ran=_tag_already_ran(trace, "burst"),
            mig_periodic_already_ran=_tag_already_ran(trace, "periodic"),
            mig_newidle_already_ran=_tag_already_ran(trace, "newidle"),
        ))
    return perseed


def collect_variant(wl_cfg, variant_kwargs, penalty, seeds):
    perseed = []
    for seed in seeds:
        b, trace, snap = run_traced(wl_cfg["profile"], wl_cfg["intensity"], wl_cfg["overrides"],
                                     wl_cfg["n_tasks"], seed, BurstAwareLoadBalancer,
                                     dict(variant_kwargs, migration_penalty=penalty, penalty_model="ran_only"))
        allowed = snap["burst_balance_levels_walked"] - b.gate_skipped_domains
        perseed.append(dict(
            seed=seed, detector_fires=b.detector_fires, burst_triggers=b.burst_triggers,
            domains_allowed=allowed, domains_blocked=b.gate_skipped_domains,
            mig_periodic=_tag_count(trace, "periodic"), mig_newidle=_tag_count(trace, "newidle"),
            mig_burst=_tag_count(trace, "burst"),
            mig_burst_already_ran=_tag_already_ran(trace, "burst"),
            mig_periodic_already_ran=_tag_already_ran(trace, "periodic"),
            mig_newidle_already_ran=_tag_already_ran(trace, "newidle"),
        ))
    return perseed


FIELDS = ["detector_fires", "burst_triggers", "domains_allowed", "domains_blocked",
          "mig_periodic", "mig_newidle", "mig_burst", "mig_burst_already_ran",
          "mig_periodic_already_ran", "mig_newidle_already_ran"]


def summarize(workload, config, penalty, perseed):
    row = dict(workload=workload, config=config, penalty=penalty, n_seeds=len(perseed))
    for field in FIELDS:
        row[f"{field}_mean"] = round(_mean([r[field] for r in perseed]), 2)
    mig_burst_total = sum(r["mig_burst"] for r in perseed)
    mig_burst_already_ran_total = sum(r["mig_burst_already_ran"] for r in perseed)
    row["mig_burst_already_ran_pct"] = (round(100 * mig_burst_already_ran_total / mig_burst_total, 1)
                                         if mig_burst_total else None)
    return row


def main():
    summary_rows = []
    perseed_rows = []
    for wl, wl_cfg in WORKLOADS.items():
        seeds = [wl_cfg["seed_base"] + i for i in range(N_SEEDS)]
        print(f"\n=== {wl}  (seeds {seeds}) ===")
        for penalty in PENALTIES:
            base_perseed = collect_baseline(wl_cfg, penalty, seeds)
            for r in base_perseed:
                perseed_rows.append(dict(workload=wl, config="baseline", penalty=penalty, **r))
            summary_rows.append(summarize(wl, "baseline", penalty, base_perseed))

            for variant_name, variant_kwargs in VARIANTS.items():
                var_perseed = collect_variant(wl_cfg, variant_kwargs, penalty, seeds)
                for r in var_perseed:
                    perseed_rows.append(dict(workload=wl, config=variant_name, penalty=penalty, **r))
                summary_rows.append(summarize(wl, variant_name, penalty, var_perseed))

        print(f"{'config':18} {'pen':4} {'fires':>7} {'trig':>6} {'allow':>6} {'block':>6} "
              f"{'p_mig':>6} {'n_mig':>6} {'b_mig':>6} {'b_ran':>6} {'b_ran%':>7}")
        for row in summary_rows:
            if row["workload"] != wl:
                continue
            print(f"{row['config']:18} {row['penalty']:4} "
                  f"{row['detector_fires_mean']:7.1f} {row['burst_triggers_mean']:6.1f} "
                  f"{row['domains_allowed_mean']:6.1f} {row['domains_blocked_mean']:6.1f} "
                  f"{row['mig_periodic_mean']:6.1f} {row['mig_newidle_mean']:6.1f} "
                  f"{row['mig_burst_mean']:6.1f} {row['mig_burst_already_ran_mean']:6.1f} "
                  f"{('%.1f' % row['mig_burst_already_ran_pct']) if row['mig_burst_already_ran_pct'] is not None else 'n/a':>7}")

    with open("results_task9_gap_gate_diagnostic_summary.csv", "w", newline="") as f:
        cols = ["workload", "config", "penalty", "n_seeds"] + [f"{f}_mean" for f in FIELDS] + \
               ["mig_burst_already_ran_pct"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(summary_rows)
    print("\nWrote results_task9_gap_gate_diagnostic_summary.csv")

    with open("results_task9_gap_gate_diagnostic_perseed.csv", "w", newline="") as f:
        cols = ["workload", "config", "penalty", "seed"] + FIELDS
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(perseed_rows)
    print("Wrote results_task9_gap_gate_diagnostic_perseed.csv")


if __name__ == "__main__":
    main()
