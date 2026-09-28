"""
Item 3: fixed sweeps, both burst_resets_timer variants.

(a) burst-size sweep, stacked_burst AND bursty: burst_duration =
    burst_size / arrival_rate_during_burst (medium's 1.5), so the
    requested size is ACTUALLY emitted -- asserted for every size,
    every burst, not just the first.
(b) inter_burst_interval sweep (10,20,35,60,90ms) at fixed burst_size
    4 and 12, stacked_burst only.

A+B+C, transition, n=30 paired, penalty in {0,2}ms. One baseline run
per seed (shared across both burst_resets_timer variants, since
burst_resets_timer only affects BurstAwareLoadBalancer).
"""

import csv
import math
import statistics

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from paired_compare import assert_same_workload, wilcoxon_signed_rank, format_p, TIE_TOLERANCE

N_REPS = 30
PENALTIES = [0.0, 2.0]
ABC_BASE = {"newidle_mode": "transition", "per_cpu_last_balance": True, "imbalance_model": "kernel"}
ARRIVAL_RATE = 1.5  # medium's arrival_rate_during_burst, held fixed across both sweeps

SWEEP_A_SEED_BASE = {"stacked_burst": 8000, "bursty": 8100}
SWEEP_B_SEED_BASE = 8200


def sign_p(wins, n):
    if n == 0:
        return 1.0
    k = max(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def run_one(profile, cls, seed, penalty, resets, intensity_overrides, n_tasks):
    kwargs = dict(ABC_BASE, migration_penalty=penalty)
    if resets is not None:
        kwargs["burst_resets_timer"] = resets
    m, b, gt, migs, logger, plan = run_simulation(
        profile, cls, intensity_level="medium", seed=seed,
        balancer_kwargs=kwargs, load_model="runnable", intensity_overrides=intensity_overrides,
        n_tasks=n_tasks,
    )
    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    return dict(summary=s, p95_wait=s["p95_wait"], plan=plan, gt=gt,
                scanned=s["sched_cores_scanned"], migrations=b.migrations,
                fires=getattr(b, "detector_fires", None))


def assert_size_emitted(gt, expected_size, label):
    bad = [n for (_, _, n) in gt if n != expected_size]
    if bad:
        raise AssertionError(f"{label}: expected every burst to emit {expected_size} tasks, "
                              f"found sizes {sorted(set(bad))} instead")


def run_case(profile, intensity_overrides, seed_base, x_label, x_value, csv_rows, assert_size=None,
             n_tasks=200):
    for penalty in PENALTIES:
        base_rows, true_rows, false_rows = [], [], []
        for rep in range(N_REPS):
            seed = seed_base + rep
            r_base = run_one(profile, LoadBalancer, seed, penalty, None, intensity_overrides, n_tasks)
            r_true = run_one(profile, BurstAwareLoadBalancer, seed, penalty, True, intensity_overrides, n_tasks)
            r_false = run_one(profile, BurstAwareLoadBalancer, seed, penalty, False, intensity_overrides, n_tasks)
            assert_same_workload(r_base["plan"], r_true["plan"], "baseline", "resets_true")
            assert_same_workload(r_base["plan"], r_false["plan"], "baseline", "resets_false")
            if assert_size is not None:
                assert_size_emitted(r_base["gt"], assert_size, f"{profile}/{x_label}={x_value}/seed={seed}")
            base_rows.append(r_base)
            true_rows.append(r_true)
            false_rows.append(r_false)

        for variant_name, variant_rows in [("resets_true", true_rows), ("resets_false", false_rows)]:
            base_p95 = [r["p95_wait"] for r in base_rows]
            var_p95 = [r["p95_wait"] for r in variant_rows]
            diffs = [v - b for b, v in zip(base_p95, var_p95)]
            wins = sum(1 for d in diffs if d < -TIE_TOLERANCE)  # tie tolerance fix, see Readme.md
            ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
            n_eff = N_REPS - ties
            p_sign = sign_p(wins, n_eff)
            wr = wilcoxon_signed_rank(diffs)

            mean_base, mean_var = statistics.mean(base_p95), statistics.mean(var_p95)
            pct_change = ((mean_var - mean_base) / mean_base * 100) if mean_base else float("nan")
            sd = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
            ci_half = 2.045 * sd / math.sqrt(N_REPS) if N_REPS > 1 else 0.0
            pct_ci_half = (ci_half / mean_base * 100) if mean_base else float("nan")

            scan_base = statistics.mean(r["scanned"] for r in base_rows)
            scan_var = statistics.mean(r["scanned"] for r in variant_rows)
            pct_scan = ((scan_var - scan_base) / scan_base * 100) if scan_base else float("nan")
            mig_base = statistics.mean(r["migrations"] for r in base_rows)
            mig_var = statistics.mean(r["migrations"] for r in variant_rows)
            pct_mig = ((mig_var - mig_base) / mig_base * 100) if mig_base else float("nan")
            fires = statistics.mean(r["fires"] for r in variant_rows if r["fires"] is not None)

            row = dict(profile=profile, x_label=x_label, x_value=x_value, penalty=penalty,
                       variant=variant_name, p95_base=mean_base, p95_variant=mean_var,
                       pct_change=pct_change, pct_ci_half=pct_ci_half, wins=wins, n_eff=n_eff,
                       ties=ties, sign_p=p_sign, wilcoxon_p=wr["p"], pct_extra_scan=pct_scan,
                       pct_extra_mig=pct_mig, detector_fires=fires)
            csv_rows.append(row)
            print(f"{profile:14} {x_label}={x_value:5} penalty={penalty}ms {variant_name:13} "
                  f"p95: {mean_base:7.2f}->{mean_var:7.2f} ({pct_change:+6.1f}%+-{pct_ci_half:.1f})  "
                  f"sign_p={format_p(p_sign)}  wilcoxon_p={format_p(wr['p'])}  "
                  f"scan={pct_scan:+.1f}%  mig={pct_mig:+.1f}%  fires={fires:.1f}")


def main():
    csv_rows_a = []
    for profile in ["stacked_burst", "bursty"]:
        for size in [2, 4, 8, 12, 16, 24, 32, 48, 64]:
            duration = size / ARRIVAL_RATE
            overrides = {"burst_size": size, "burst_duration": duration, "arrival_rate_during_burst": ARRIVAL_RATE}
            # n_tasks an EXACT multiple of size (10 full bursts) -- avoids
            # a truncated final burst from hitting the task budget mid-way,
            # which assert_size_emitted() would otherwise (correctly) catch.
            run_case(profile, overrides, SWEEP_A_SEED_BASE[profile], "burst_size", size, csv_rows_a,
                     assert_size=size, n_tasks=size * 10)

    with open("results_task6_sweepA_burstsize_v2.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows_a[0].keys()))
        w.writeheader()
        w.writerows(csv_rows_a)
    print("\nWrote results_task6_sweepA_burstsize_v2.csv")

    csv_rows_b = []
    for size in [4, 12]:
        duration = size / ARRIVAL_RATE
        for interval in [10, 20, 35, 60, 90]:
            overrides = {"burst_size": size, "burst_duration": duration,
                         "arrival_rate_during_burst": ARRIVAL_RATE, "inter_burst_interval": interval}
            run_case("stacked_burst", overrides, SWEEP_B_SEED_BASE, f"interval@size{size}", interval,
                     csv_rows_b, assert_size=size, n_tasks=size * 10)

    with open("results_task6_sweepB_interval_v2.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows_b[0].keys()))
        w.writeheader()
        w.writerows(csv_rows_b)
    print("Wrote results_task6_sweepB_interval_v2.csv")


if __name__ == "__main__":
    main()
