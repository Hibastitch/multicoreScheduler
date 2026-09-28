"""
Task 6: burst-size sweep to find where burst-aware's p95_wait effect
crosses zero. A+B+C, transition, n=30 paired, penalty in {0, 2}ms.

stacked_burst: burst_size in {2,4,8,12,16,24,32,48,64}, medium-intensity
timing otherwise unchanged (burst_duration=8, arrival_rate_during_burst=1.5,
inter_burst_interval=35 -- only burst_size overridden via
intensity_overrides).

bursty: burst_size in {16,32,48,64}, same medium-intensity base and
override style -- the fork-placement comparison (same burst sizes,
placement spreads load on arrival instead of stacking it on one core).

Seeds: stacked_burst uses its established medium-intensity base (5100);
bursty uses paired_compare.seed_base('bursty','medium') (1100) -- both
already-established conventions, just reused here.
"""

import csv
import statistics

from paired_compare import run_pair, seed_base, wilcoxon_signed_rank, format_p, TIE_TOLERANCE

N_REPS = 30
PENALTIES = [0.0, 2.0]
ABC_BASE = {"newidle_mode": "transition", "per_cpu_last_balance": True, "imbalance_model": "kernel"}

STACKED_SIZES = [2, 4, 8, 12, 16, 24, 32, 48, 64]
BURSTY_SIZES = [16, 32, 48, 64]

STACKED_SEED_BASE = 5100  # established stacked_burst/medium base
BURSTY_SEED_BASE = seed_base("bursty", "medium")  # established bursty/medium base


def run_sweep(profile, sizes, seed_base_val):
    rows = []
    for size in sizes:
        for penalty in PENALTIES:
            balancer_kwargs = dict(ABC_BASE, migration_penalty=penalty)
            workload_kwargs = {"load_model": "runnable", "intensity_overrides": {"burst_size": size}}

            pair_rows = [
                run_pair(profile, "medium", seed_base_val + rep,
                         balancer_kwargs=balancer_kwargs, workload_kwargs=workload_kwargs)
                for rep in range(N_REPS)
            ]

            base_p95 = [r["baseline"]["p95_wait"] for r in pair_rows]
            burst_p95 = [r["burst_aware"]["p95_wait"] for r in pair_rows]
            diffs = [b - a for a, b in zip(base_p95, burst_p95)]

            wins = sum(1 for d in diffs if d < -TIE_TOLERANCE)  # tie tolerance fix, see Readme.md
            ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
            n_eff = N_REPS - ties
            import math

            def sign_p(w, n):
                if n == 0:
                    return 1.0
                k = max(w, n - w)
                tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
                return min(1.0, 2 * tail)

            p_sign = sign_p(wins, n_eff)
            wr = wilcoxon_signed_rank(diffs)

            mean_base, mean_burst = statistics.mean(base_p95), statistics.mean(burst_p95)
            pct_change = ((mean_burst - mean_base) / mean_base * 100) if mean_base else float("nan")
            sd = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
            ci_half = 2.045 * sd / math.sqrt(N_REPS) if N_REPS > 1 else 0.0
            pct_ci_half = (ci_half / mean_base * 100) if mean_base else float("nan")

            scan_base = statistics.mean(r["baseline"]["summary"]["sched_cores_scanned"] for r in pair_rows)
            scan_burst = statistics.mean(r["burst_aware"]["summary"]["sched_cores_scanned"] for r in pair_rows)
            pct_extra_scan = ((scan_burst - scan_base) / scan_base * 100) if scan_base else float("nan")

            mig_base = statistics.mean(r["baseline"]["migrations"] for r in pair_rows)
            mig_burst = statistics.mean(r["burst_aware"]["migrations"] for r in pair_rows)
            pct_extra_mig = ((mig_burst - mig_base) / mig_base * 100) if mig_base else float("nan")

            fires = statistics.mean(r["burst_aware"]["balancer"].detector_fires for r in pair_rows)

            row = dict(
                profile=profile, burst_size=size, penalty=penalty,
                p95_base=mean_base, p95_burst=mean_burst, pct_change=pct_change,
                pct_ci_half=pct_ci_half, wins=wins, n_eff=n_eff, ties=ties,
                sign_p=p_sign, wilcoxon_p=wr["p"],
                pct_extra_scan=pct_extra_scan, mig_base=mig_base, mig_burst=mig_burst,
                pct_extra_mig=pct_extra_mig, detector_fires=fires,
            )
            rows.append(row)
            print(f"{profile:14} size={size:3d} penalty={penalty}ms  "
                  f"p95: {mean_base:7.2f}->{mean_burst:7.2f} ({pct_change:+6.1f}% +-{pct_ci_half:.1f})  "
                  f"sign_p={format_p(p_sign)}  wilcoxon_p={format_p(wr['p'])}  "
                  f"extra_scan={pct_extra_scan:+.1f}%  extra_mig={pct_extra_mig:+.1f}%  fires={fires:.1f}")
    return rows


def main():
    all_rows = []
    all_rows.extend(run_sweep("stacked_burst", STACKED_SIZES, STACKED_SEED_BASE))
    all_rows.extend(run_sweep("bursty", BURSTY_SIZES, BURSTY_SEED_BASE))

    with open("results_task6_burst_size_sweep.csv", "w", newline="") as f:
        keys = list(all_rows[0].keys())
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(all_rows)
    print("\nWrote results_task6_burst_size_sweep.csv")


if __name__ == "__main__":
    main()
