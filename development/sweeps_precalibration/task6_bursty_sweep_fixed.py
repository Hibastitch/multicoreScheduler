"""
Item 1 (fix verification): re-run the bursty half of sweep A with the
tie-tolerance fix, reporting p95_wait, p99_wait, avg_wait, avg_slowdown.
Same sizes/seeds/settings as the original (buggy) run, for direct
comparison. Saves PER-SEED raw values to CSV (not only aggregates), per
the new standing practice -- so this analysis (or a corrected one) can
be redone without re-running any simulation.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
import csv
import statistics

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from paired_compare import assert_same_workload, wilcoxon_signed_rank, format_p, TIE_TOLERANCE

N_REPS = 30
PENALTIES = [0.0, 2.0]
SIZES = [2, 4, 8, 12, 16, 24, 32, 48, 64]
SEED_BASE = 8100  # same as the original (buggy) bursty sweep, for direct comparability
ARRIVAL_RATE = 1.5
ABC_BASE = {"newidle_mode": "transition", "per_cpu_last_balance": True, "imbalance_model": "kernel"}
METRICS = ["p95_wait", "p99_wait", "avg_wait", "avg_slowdown"]


def run_one(cls, seed, penalty, resets, overrides, n_tasks):
    kwargs = dict(ABC_BASE, migration_penalty=penalty)
    if resets is not None:
        kwargs["burst_resets_timer"] = resets
    m, b, gt, migs, logger, plan = run_simulation(
        "bursty", cls, intensity_level="medium", seed=seed,
        balancer_kwargs=kwargs, load_model="runnable", intensity_overrides=overrides,
        n_tasks=n_tasks,
    )
    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    return dict(plan=plan, **{metric: s[metric] for metric in METRICS})


def sign_wilcoxon(base_vals, var_vals):
    diffs = [v - b for b, v in zip(base_vals, var_vals)]
    wins = sum(1 for d in diffs if d < -TIE_TOLERANCE)
    ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
    n_eff = len(diffs) - ties
    import math
    def sign_p(w, n):
        if n == 0:
            return 1.0
        k = max(w, n - w)
        tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
        return min(1.0, 2 * tail)
    p_sign = sign_p(wins, n_eff)
    wr = wilcoxon_signed_rank(diffs)
    mean_base, mean_var = statistics.mean(base_vals), statistics.mean(var_vals)
    pct_change = ((mean_var - mean_base) / mean_base * 100) if mean_base else float("nan")
    return dict(mean_base=mean_base, mean_var=mean_var, pct_change=pct_change,
                wins=wins, ties=ties, n_eff=n_eff, sign_p=p_sign, wilcoxon_p=wr["p"])


def main():
    per_seed_rows = []
    summary_rows = []

    for size in SIZES:
        duration = size / ARRIVAL_RATE
        overrides = {"burst_size": size, "burst_duration": duration, "arrival_rate_during_burst": ARRIVAL_RATE}
        n_tasks = size * 10
        for penalty in PENALTIES:
            base_results, true_results, false_results = [], [], []
            for rep in range(N_REPS):
                seed = SEED_BASE + rep
                r_base = run_one(LoadBalancer, seed, penalty, None, overrides, n_tasks)
                r_true = run_one(BurstAwareLoadBalancer, seed, penalty, True, overrides, n_tasks)
                r_false = run_one(BurstAwareLoadBalancer, seed, penalty, False, overrides, n_tasks)
                assert_same_workload(r_base["plan"], r_true["plan"], "baseline", "resets_true")
                assert_same_workload(r_base["plan"], r_false["plan"], "baseline", "resets_false")
                base_results.append(r_base)
                true_results.append(r_true)
                false_results.append(r_false)

                row = dict(size=size, penalty=penalty, seed=seed)
                for metric in METRICS:
                    row[f"baseline_{metric}"] = r_base[metric]
                    row[f"resets_true_{metric}"] = r_true[metric]
                    row[f"resets_false_{metric}"] = r_false[metric]
                per_seed_rows.append(row)

            print(f"\n=== bursty size={size} penalty={penalty}ms ===")
            for metric in METRICS:
                base_vals = [r[metric] for r in base_results]
                for variant_name, variant_results in [("resets_true", true_results), ("resets_false", false_results)]:
                    var_vals = [r[metric] for r in variant_results]
                    res = sign_wilcoxon(base_vals, var_vals)
                    print(f"  [{metric}] {variant_name}: {res['mean_base']:.4f}->{res['mean_var']:.4f} "
                          f"({res['pct_change']:+.2f}%)  wins={res['wins']}/{res['n_eff']} (ties={res['ties']})  "
                          f"sign_p={format_p(res['sign_p'])}  wilcoxon_p={format_p(res['wilcoxon_p'])}")
                    summary_rows.append(dict(size=size, penalty=penalty, metric=metric, variant=variant_name, **res))

    with open("results_task6_bursty_sweep_fixed_perseed.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(per_seed_rows[0].keys()))
        w.writeheader()
        w.writerows(per_seed_rows)
    print("\nWrote results_task6_bursty_sweep_fixed_perseed.csv")

    with open("results_task6_bursty_sweep_fixed_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)
    print("Wrote results_task6_bursty_sweep_fixed_summary.csv")


if __name__ == "__main__":
    main()
