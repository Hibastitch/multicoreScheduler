"""
Task 8, Step 2: baseline-only ablation of the 6 fidelity fixes
implemented in Step 1 (see docs/NOTEBOOK.md 2026-09-29c/d,
docs/FIDELITY_AUDIT.md). LoadBalancer ONLY -- burst-aware is not run
anywhere in this task (no peeking at the baseline-vs-burst-aware
comparison before the actual v2 re-run).

Configurations:
  (a) all_legacy       -- every fix at its legacy value (the exact
                           pre-Task-8 default behavior)
  (b) each fix ALONE on top of (a) -- 6 configs, one fix flipped at a
      time, isolating each fix's individual effect
  (c) all_six          -- every fix at its corrected value

penalty=0, 10 seeds (60000-60009), 5 workloads: stacked_medium,
stacked_high, rate3.0_s12, bursty_high_s24, bursty_high_s64 (canonical
definitions from final_results/2_confirmation/task6_confirmation_run.py).

Metrics: p95_wait, avg_wait, avg_slowdown, total_migrations. % change
vs (a) and the corrected paired sign test (harms > wins required) +
Wilcoxon, per fix per workload.
"""

import sys, pathlib, csv, math
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))

from Main import run_simulation
from LoadBalancer import LoadBalancer
from paired_compare import wilcoxon_signed_rank, format_p, TIE_TOLERANCE

N_SEEDS = 10
SEED_BASE = 60000
PENALTY = 0.0

WORKLOADS = {
    "stacked_medium":  dict(profile="stacked_burst", intensity="medium", overrides=None, n_tasks=200),
    "stacked_high":    dict(profile="stacked_burst", intensity="high", overrides=None, n_tasks=200),
    "rate3.0_s12":     dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 12, "arrival_rate_during_burst": 3.0,
                                        "burst_duration": 12 / 3.0, "inter_burst_interval": 60},
                             n_tasks=120),
    "bursty_high_s24": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 24, "burst_duration": 24 / 3.75}, n_tasks=240),
    "bursty_high_s64": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 64, "burst_duration": 64 / 3.75}, n_tasks=640),
}

LEGACY = dict(checker_model="legacy", busy_factor=1, cache_hot=False, numa_fix=False)
LEGACY_TS = 4
LEGACY_ROOT = "fixed"

# Each entry: (balancer_kwargs overrides, time_slice, placement_root)
CONFIGS = {
    "a_all_legacy":  (dict(), LEGACY_TS, LEGACY_ROOT),
    "b_checker_only": (dict(checker_model="kernel"), LEGACY_TS, LEGACY_ROOT),
    "b_busy_factor_only": (dict(busy_factor=16), LEGACY_TS, LEGACY_ROOT),
    "b_placement_root_only": (dict(), LEGACY_TS, "own"),
    "b_cache_hot_only": (dict(cache_hot=True), LEGACY_TS, LEGACY_ROOT),
    "b_numa_fix_only": (dict(numa_fix=True), LEGACY_TS, LEGACY_ROOT),
    "b_time_slice_only": (dict(), 2.8, LEGACY_ROOT),
    "c_all_six": (dict(checker_model="kernel", busy_factor=16, cache_hot=True, numa_fix=True), 2.8, "own"),
}

METRICS = ["p95_wait", "avg_wait", "avg_slowdown", "total_migrations"]


def sign_p(wins, harms, n):
    if n == 0:
        return 1.0
    k = max(wins, harms)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def run_config(wl, seed, cfg_name):
    overrides, time_slice, placement_root = CONFIGS[cfg_name]
    kwargs = dict(LEGACY)
    kwargs.update(overrides)
    m, b, gt, migs, logger, plan = run_simulation(
        wl["profile"], LoadBalancer, intensity_level=wl["intensity"], seed=seed,
        balancer_kwargs=dict(migration_penalty=PENALTY, **kwargs),
        intensity_overrides=wl["overrides"], n_tasks=wl["n_tasks"],
        time_slice=time_slice, placement_root=placement_root,
    )
    return m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)


def main():
    perseed_rows = []
    summary_rows = []

    raw = {}  # (workload, config) -> {metric: [values across seeds]}
    for wl_name, wl in WORKLOADS.items():
        for cfg_name in CONFIGS:
            vals = {m: [] for m in METRICS}
            for rep in range(N_SEEDS):
                seed = SEED_BASE + rep
                s = run_config(wl, seed, cfg_name)
                for metric in METRICS:
                    vals[metric].append(s[metric])
                perseed_rows.append(dict(workload=wl_name, config=cfg_name, seed=seed,
                                          **{m: s[m] for m in METRICS}))
            raw[(wl_name, cfg_name)] = vals
            print(f"{wl_name:16} {cfg_name:24} "
                  f"p95={sum(vals['p95_wait'])/N_SEEDS:8.2f}  "
                  f"migs={sum(vals['total_migrations'])/N_SEEDS:8.1f}")

    with open(pathlib.Path(__file__).resolve().parent / "results_task8_step2_ablation_perseed.csv",
              "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(perseed_rows[0].keys()))
        w.writeheader()
        w.writerows(perseed_rows)
    print("\nWrote results_task8_step2_ablation_perseed.csv")

    print("\n=== % change vs (a) all_legacy, paired sign test + Wilcoxon ===")
    for wl_name in WORKLOADS:
        base_vals = raw[(wl_name, "a_all_legacy")]
        for cfg_name in CONFIGS:
            if cfg_name == "a_all_legacy":
                continue
            for metric in METRICS:
                a = base_vals[metric]
                b_ = raw[(wl_name, cfg_name)][metric]
                diffs = [x - y for y, x in zip(a, b_)]
                wins = sum(1 for d in diffs if d < -TIE_TOLERANCE)
                harms = sum(1 for d in diffs if d > TIE_TOLERANCE)
                ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
                n_eff = len(diffs) - ties
                p = sign_p(wins, harms, n_eff)
                wr = wilcoxon_signed_rank(diffs)
                mean_a, mean_b = sum(a) / len(a), sum(b_) / len(b_)
                pct = (mean_b - mean_a) / mean_a * 100 if mean_a else float("nan")
                summary_rows.append(dict(
                    workload=wl_name, config=cfg_name, metric=metric,
                    mean_legacy=round(mean_a, 3), mean_config=round(mean_b, 3),
                    pct_change=round(pct, 2), wins=wins, harms=harms, ties=ties,
                    sign_p=p, wilcoxon_p=wr["p"],
                ))
                print(f"{wl_name:16} {cfg_name:24} {metric:16} "
                      f"legacy={mean_a:9.2f} cfg={mean_b:9.2f} ({pct:+7.2f}%)  "
                      f"wins={wins:2} harms={harms:2} ties={ties:2}  "
                      f"sign_p={format_p(p):>8}  wilcoxon_p={format_p(wr['p']):>8}")

    with open(pathlib.Path(__file__).resolve().parent / "results_task8_step2_ablation_summary.csv",
              "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)
    print("\nWrote results_task8_step2_ablation_summary.csv")


if __name__ == "__main__":
    main()
