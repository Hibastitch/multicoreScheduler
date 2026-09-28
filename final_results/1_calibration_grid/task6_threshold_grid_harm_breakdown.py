"""
Which metric(s) actually tripped the corrected harm rule, for every
harmful (config, workload, penalty) cell. No new simulations. Reuses
task6_threshold_grid_tradeoff.py's compute_results() (itself a verbatim
reproduction of task6_threshold_grid_recompute_harm.py's per-cell loop)
rather than a third copy of the same rule.
"""

import csv

from task6_threshold_grid_tradeoff import compute_results
from paired_compare import format_p


def main():
    results = compute_results()
    rows = []
    for rec in results:
        for metric, pm in rec["per_metric"].items():
            if pm["new_harm"]:
                rows.append(dict(
                    config=rec["config"], workload=rec["workload"], penalty=rec["penalty"],
                    metric=metric, pct=round(pm["pct"], 3),
                    wins=pm["wins"], harms=pm["harms"], ties=pm["ties"], n_eff=pm["n_eff"],
                    sign_p=pm["sign_p"],
                ))

    rows.sort(key=lambda r: (r["workload"], r["penalty"], r["config"], r["metric"]))
    with open("harm_breakdown.csv", "w", newline="") as f:
        cols = ["config", "workload", "penalty", "metric", "pct", "wins", "harms", "ties", "n_eff", "sign_p"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote harm_breakdown.csv ({len(rows)} rows)")

    print("\n=== bursty_high_s64 penalty=2.0 ===")
    for r in rows:
        if r["workload"] == "bursty_high_s64" and r["penalty"] == "2.0":
            print(f"{r['config']:14} {r['metric']:14} pct={r['pct']:+7.3f}%  "
                  f"wins={r['wins']:2} harms={r['harms']:2} ties={r['ties']:2}  "
                  f"sign_p={format_p(r['sign_p'])}")

    print("\n=== full summary, grouped by (workload, penalty) ===")
    seen = set()
    for r in rows:
        key = (r["workload"], r["penalty"])
        if key in seen:
            continue
        seen.add(key)
        cell_rows = [x for x in rows if (x["workload"], x["penalty"]) == key]
        configs_here = sorted(set(x["config"] for x in cell_rows))
        metrics_here = sorted(set(x["metric"] for x in cell_rows))
        print(f"{r['workload']:16} pen={r['penalty']}  configs={configs_here}  metrics={metrics_here}")


if __name__ == "__main__":
    main()
