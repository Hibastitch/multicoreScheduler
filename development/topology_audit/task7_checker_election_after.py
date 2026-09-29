"""
STEP 3: re-measure with checker_model="kernel" (same audit as STEP 1,
same seeds/workloads/penalty=0, baseline scheduler only) -- confirms
the invalid-checker rate drops to ~0, plus a paired legacy-vs-kernel
comparison on migrations and p95_wait. Does NOT change LoadBalancer's
default (checker_model stays "legacy" unless passed explicitly here).
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))

import csv
import math
from collections import defaultdict

from Main import run_simulation
from LoadBalancer import LoadBalancer
from Topology import domain_chain, Domain
from paired_compare import wilcoxon_signed_rank, format_p, TIE_TOLERANCE

N_SEEDS = 10
SEED_BASE = 30000
PENALTY = 0.0

WORKLOADS = {
    "stacked_medium": dict(profile="stacked_burst", intensity="medium", overrides=None, n_tasks=200),
    "stacked_high":   dict(profile="stacked_burst", intensity="high", overrides=None, n_tasks=200),
    "rate3.0_s12":    dict(profile="stacked_burst", intensity="medium",
                            overrides={"burst_size": 12, "arrival_rate_during_burst": 3.0,
                                       "burst_duration": 12 / 3.0, "inter_burst_interval": 60},
                            n_tasks=120),
    "bursty_high_s64": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 64, "burst_duration": 64 / 3.75}, n_tasks=640),
}


def sign_p(wins, n):
    if n == 0:
        return 1.0
    k = max(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def audit_run(profile, seed, overrides, n_tasks, intensity, checker_model):
    per_domain = defaultdict(lambda: dict(level=None, n_events=0, n_checker_invalid=0))

    orig = LoadBalancer.is_designated_checker

    def patched(self, domain, core):
        checker = self._find_checker(domain, core)
        valid = domain in domain_chain(checker)
        rec = per_domain[domain.name]
        rec["level"] = domain.level
        rec["n_events"] += 1
        if not valid:
            rec["n_checker_invalid"] += 1
        return checker.core_id == core.core_id

    LoadBalancer.is_designated_checker = patched
    try:
        m, b, gt, migs, logger, plan = run_simulation(
            profile, LoadBalancer, intensity_level=intensity, seed=seed,
            balancer_kwargs=dict(migration_penalty=PENALTY, checker_model=checker_model),
            intensity_overrides=overrides, n_tasks=n_tasks,
        )
    finally:
        LoadBalancer.is_designated_checker = orig

    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    return per_domain, s


def main():
    audit_rows = []
    compare_rows = []
    by_level = defaultdict(lambda: [0, 0])  # level -> [events, invalid]

    for wl_name, wl in WORKLOADS.items():
        for rep in range(N_SEEDS):
            seed = SEED_BASE + rep

            per_domain_kernel, s_kernel = audit_run(wl["profile"], seed, wl["overrides"], wl["n_tasks"],
                                                      wl["intensity"], "kernel")
            for dom_name, rec in per_domain_kernel.items():
                audit_rows.append(dict(workload=wl_name, seed=seed, domain=dom_name, level=rec["level"],
                                        n_events=rec["n_events"], n_checker_invalid=rec["n_checker_invalid"]))
                by_level[rec["level"]][0] += rec["n_events"]
                by_level[rec["level"]][1] += rec["n_checker_invalid"]

            # legacy run (plain, no instrumentation needed -- just for the comparison)
            m_l, b_l, gt_l, migs_l, logger_l, plan_l = run_simulation(
                wl["profile"], LoadBalancer, intensity_level=wl["intensity"], seed=seed,
                balancer_kwargs=dict(migration_penalty=PENALTY, checker_model="legacy"),
                intensity_overrides=wl["overrides"], n_tasks=wl["n_tasks"],
            )
            s_legacy = m_l.summary(balancer=b_l, ground_truth_bursts=gt_l, migration_events=migs_l)

            compare_rows.append(dict(
                workload=wl_name, seed=seed,
                p95_wait_legacy=s_legacy["p95_wait"], p95_wait_kernel=s_kernel["p95_wait"],
                migrations_legacy=s_legacy["total_migrations"], migrations_kernel=s_kernel["total_migrations"],
                cores_scanned_legacy=s_legacy["sched_cores_scanned"], cores_scanned_kernel=s_kernel["sched_cores_scanned"],
            ))
            print(f"{wl_name:16} seed={seed} legacy p95={s_legacy['p95_wait']:.2f} migs={s_legacy['total_migrations']}"
                  f"  |  kernel p95={s_kernel['p95_wait']:.2f} migs={s_kernel['total_migrations']}")

    with open("checker_audit_after_perseed.csv", "w", newline="") as f:
        cols = ["workload", "seed", "domain", "level", "n_events", "n_checker_invalid"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(audit_rows)
    print("\nWrote checker_audit_after_perseed.csv")

    print("\n=== AFTER (checker_model=kernel): invalid-checker rate by level ===")
    for level, (events, invalid) in sorted(by_level.items()):
        pct = 100 * invalid / events if events else 0.0
        print(f"{level:10} events={events:8}  invalid={invalid:8}  pct_invalid={pct:5.2f}%")

    with open("checker_model_compare_perseed.csv", "w", newline="") as f:
        cols = list(compare_rows[0].keys())
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(compare_rows)
    print("\nWrote checker_model_compare_perseed.csv")

    print("\n=== legacy vs kernel, baseline only, penalty=0, paired by seed ===")
    for metric in ["p95_wait", "migrations", "cores_scanned"]:
        for wl_name in WORKLOADS:
            wl_rows = [r for r in compare_rows if r["workload"] == wl_name]
            legacy_vals = [r[f"{metric}_legacy"] for r in wl_rows]
            kernel_vals = [r[f"{metric}_kernel"] for r in wl_rows]
            diffs = [k - l for l, k in zip(legacy_vals, kernel_vals)]
            wins = sum(1 for d in diffs if d < -TIE_TOLERANCE)
            harms = sum(1 for d in diffs if d > TIE_TOLERANCE)
            ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
            n_eff = len(diffs) - ties
            p = sign_p(harms, n_eff) if harms >= wins else sign_p(wins, n_eff)
            wr = wilcoxon_signed_rank(diffs)
            mean_l, mean_k = sum(legacy_vals) / len(legacy_vals), sum(kernel_vals) / len(kernel_vals)
            pct = (mean_k - mean_l) / mean_l * 100 if mean_l else float("nan")
            print(f"{metric:16} {wl_name:16} legacy={mean_l:9.2f} kernel={mean_k:9.2f} ({pct:+6.2f}%)  "
                  f"wins={wins:2} harms={harms:2} ties={ties:2}  sign_p={format_p(p):>8}  wilcoxon_p={format_p(wr['p']):>8}")


if __name__ == "__main__":
    main()
