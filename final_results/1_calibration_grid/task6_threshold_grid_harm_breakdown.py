"""
Which metric(s) actually tripped the corrected harm rule, for every
harmful (config, workload, penalty) cell. No new simulations. Reuses
task6_threshold_grid_tradeoff.py's compute_results() (itself a verbatim
reproduction of task6_threshold_grid_recompute_harm.py's per-cell loop)
rather than a third copy of the same rule.
"""

import csv
import os

from task6_threshold_grid_tradeoff import compute_results
from paired_compare import format_p

# TASK 8 v2 RE-RUN (2026-09-29, Step 4): TASK8_V2=1 writes a _v2-
# suffixed output -- compute_results() already reads _v2 inputs via
# task6_threshold_grid_recompute_harm.load_rows()'s own V2_SUFFIX.
V2_SUFFIX = "_v2" if os.environ.get("TASK8_V2") else ""

# TASK 9 v3 RE-RUN (2026-09-29, Step 4): TASK9_V3=1 writes a _v3-
# suffixed output. This script deliberately does NOT filter by rule
# (e)'s disqualifying penalties -- it shows every harmful (config,
# workload, penalty, metric) cell, INCLUDING penalty=2ms's non-
# disqualifying-but-still-real harm, which is exactly what rule (e)'s
# "measured and reported in full" means.
V3_SUFFIX = "_v3" if os.environ.get("TASK9_V3") else ""

# TASK 10 v4 RE-RUN (2026-09-30, Step 3): TASK10_V4=1 writes a _v4-
# suffixed output -- same "show everything, including non-disqualifying
# penalty=2ms harm" behavior as v3, unchanged by design.
V4 = bool(os.environ.get("TASK10_V4"))
V4_SUFFIX = "_v4" if V4 else ""
SUFFIX = V4_SUFFIX or V3_SUFFIX or V2_SUFFIX

# REORGANIZATION (2026-10-01, docs/NOTEBOOK.md): old-version (non-v4)
# outputs now live in history/ -- v4 keeps writing here unchanged.
DATA_DIR = "" if V4 else "history/"


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
    out_name = f"{DATA_DIR}harm_breakdown{SUFFIX}.csv"
    with open(out_name, "w", newline="") as f:
        cols = ["config", "workload", "penalty", "metric", "pct", "wins", "harms", "ties", "n_eff", "sign_p"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {out_name} ({len(rows)} rows)")

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
