"""
STEP 1 (measure BEFORE, no code change): read-only instrumentation
around LoadBalancer.is_designated_checker() to count, per (level,
domain name), how many periodic-interval-elapsed checks find a
STRUCTURALLY INVALID elected checker -- one that does not itself climb
that exact domain object (Domain.home_children means a domain's span
can include neighbor cores that are never that domain's true ancestor
path; see Topology.py's build_topology() docstring, 2026-09-29 CAVEAT).

When the elected checker is invalid, NO core will ever see
is_designated_checker()==True for that domain at that moment -- the
periodic pass is structurally skipped, not just "not this core's turn."

Monkey-patches LoadBalancer.is_designated_checker for the duration of
each run (never mutates the class permanently, restored in a finally
block) -- same pattern as diagnostics.py's post-hoc analysis functions.
No simulator logic is changed by this script.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))

import csv
from collections import defaultdict

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from Topology import domain_chain, Domain

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

SCHEDULERS = {"baseline": LoadBalancer, "burst_aware": BurstAwareLoadBalancer}


def audit_one_run(profile, cls, seed, overrides, n_tasks, intensity):
    """Returns per-domain event counters for this one run."""
    per_domain = defaultdict(lambda: dict(level=None, n_events=0, n_checker_invalid=0, n_checker_valid_self=0))

    orig_is_checker = LoadBalancer.is_designated_checker

    def patched_is_checker(self, domain, core):
        checker = self._find_checker(domain)
        # is the elected checker actually a core that climbs THIS domain object?
        valid = domain in domain_chain(checker)
        key = domain.name
        rec = per_domain[key]
        rec["level"] = domain.level
        rec["n_events"] += 1
        if not valid:
            rec["n_checker_invalid"] += 1
        result = checker.core_id == core.core_id
        if valid and result:
            rec["n_checker_valid_self"] += 1
        return result

    LoadBalancer.is_designated_checker = patched_is_checker
    try:
        m, b, gt, migs, logger, plan = run_simulation(
            profile, cls, intensity_level=intensity, seed=seed,
            balancer_kwargs=dict(migration_penalty=PENALTY),
            intensity_overrides=overrides, n_tasks=n_tasks,
        )
    finally:
        LoadBalancer.is_designated_checker = orig_is_checker

    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    return per_domain, s


def main():
    per_seed_rows = []
    # aggregate[(scheduler, domain_name)] = dict(level, n_events, n_checker_invalid, n_checker_valid_self)
    aggregate = defaultdict(lambda: dict(level=None, n_events=0, n_checker_invalid=0, n_checker_valid_self=0))

    for sched_name, cls in SCHEDULERS.items():
        for wl_name, wl in WORKLOADS.items():
            for rep in range(N_SEEDS):
                seed = SEED_BASE + rep
                per_domain, s = audit_one_run(wl["profile"], cls, seed, wl["overrides"], wl["n_tasks"], wl["intensity"])
                for dom_name, rec in per_domain.items():
                    row = dict(scheduler=sched_name, workload=wl_name, seed=seed, domain=dom_name,
                               level=rec["level"], n_events=rec["n_events"],
                               n_checker_invalid=rec["n_checker_invalid"],
                               n_checker_valid_self=rec["n_checker_valid_self"])
                    per_seed_rows.append(row)

                    agg = aggregate[(sched_name, dom_name)]
                    agg["level"] = rec["level"]
                    agg["n_events"] += rec["n_events"]
                    agg["n_checker_invalid"] += rec["n_checker_invalid"]
                    agg["n_checker_valid_self"] += rec["n_checker_valid_self"]
                print(f"{sched_name:12} {wl_name:16} seed={seed} done, p95_wait={s['p95_wait']:.2f}")

    with open("checker_audit_perseed.csv", "w", newline="") as f:
        cols = ["scheduler", "workload", "seed", "domain", "level", "n_events",
                "n_checker_invalid", "n_checker_valid_self"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(per_seed_rows)
    print("\nWrote checker_audit_perseed.csv")

    summary_rows = []
    for (sched_name, dom_name), agg in aggregate.items():
        pct_invalid = 100 * agg["n_checker_invalid"] / agg["n_events"] if agg["n_events"] else 0.0
        summary_rows.append(dict(
            scheduler=sched_name, domain=dom_name, level=agg["level"],
            n_events=agg["n_events"], n_checker_invalid=agg["n_checker_invalid"],
            pct_invalid=round(pct_invalid, 2),
            n_checker_valid_self=agg["n_checker_valid_self"],
            fully_starved=(agg["n_checker_valid_self"] == 0 and agg["n_events"] > 0),
        ))
    summary_rows.sort(key=lambda r: (r["scheduler"], r["level"], r["domain"]))

    with open("checker_audit_summary.csv", "w", newline="") as f:
        cols = ["scheduler", "domain", "level", "n_events", "n_checker_invalid", "pct_invalid",
                "n_checker_valid_self", "fully_starved"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(summary_rows)
    print("Wrote checker_audit_summary.csv")

    print(f"\n{'scheduler':12} {'domain':10} {'level':8} {'events':>8} {'invalid':>8} {'%invalid':>9} {'valid+self':>11} {'starved':>8}")
    for r in summary_rows:
        print(f"{r['scheduler']:12} {r['domain']:10} {r['level']:8} {r['n_events']:8} "
              f"{r['n_checker_invalid']:8} {r['pct_invalid']:8.1f}% {r['n_checker_valid_self']:11} "
              f"{'YES' if r['fully_starved'] else '':>8}")

    print("\n=== by level, both schedulers combined ===")
    by_level = defaultdict(lambda: [0, 0])
    for r in summary_rows:
        by_level[r["level"]][0] += r["n_events"]
        by_level[r["level"]][1] += r["n_checker_invalid"]
    for level, (events, invalid) in sorted(by_level.items()):
        pct = 100 * invalid / events if events else 0.0
        print(f"{level:10} events={events:8}  invalid={invalid:8}  pct_invalid={pct:5.1f}%")


if __name__ == "__main__":
    main()
