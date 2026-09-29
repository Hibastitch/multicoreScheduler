"""
Merge the 9 per-workload threshold-grid summary CSVs and apply the
PRE-REGISTERED selection rule (stated before seeing results): choose
the config with NO significant harm on ANY metric (p95_wait, avg_wait,
avg_slowdown), on ANY workload, at EITHER penalty, and, among those,
the largest mean p95_wait reduction on stacked_burst medium/high.
"""

import csv
import glob
import os

# TASK 8 v2 RE-RUN (2026-09-29, Step 4): TASK8_V2=1 reads the _v2-
# suffixed grid CSVs instead of the original ones. No new simulations
# either way -- this script only merges existing CSVs.
V2_SUFFIX = "_v2" if os.environ.get("TASK8_V2") else ""

rows = []
for path in glob.glob(f"results_task6_threshold_grid{V2_SUFFIX}_*_summary.csv"):
    if V2_SUFFIX == "" and "_v2_" in path:
        continue  # non-v2 mode must not also pick up v2 files
    rows.extend(csv.DictReader(open(path)))

configs = sorted(set(r["config"] for r in rows))
workloads = sorted(set(r["workload"] for r in rows))
print(f"Loaded {len(rows)} rows across {len(workloads)} workloads x {len(configs)} configs x 2 penalties")
print(f"Workloads: {workloads}")
print(f"Configs: {configs}\n")

# --- full grid: per config, any_significant_harm anywhere? ---
print("=" * 100)
print("HARM CHECK per config (any workload, any penalty, any metric)")
print("=" * 100)
harm_free_configs = []
for cfg in configs:
    cfg_rows = [r for r in rows if r["config"] == cfg]
    harmful = [(r["workload"], r["penalty"]) for r in cfg_rows if r["any_significant_harm"] == "True"]
    status = "HARM" if harmful else "clean"
    print(f"{cfg:20} {status:6} harmful_on={harmful if harmful else '-'}")
    if not harmful:
        harm_free_configs.append(cfg)

print(f"\nHarm-free configs: {harm_free_configs}\n")

# --- among harm-free configs, largest mean p95_wait reduction on stacked_burst medium/high ---
print("=" * 100)
print("stacked_burst medium/high p95_wait %% change, harm-free configs only")
print("=" * 100)
best = None
for cfg in harm_free_configs:
    med_rows = [r for r in rows if r["config"] == cfg and r["workload"] == "stacked_medium"]
    high_rows = [r for r in rows if r["config"] == cfg and r["workload"] == "stacked_high"]
    pct_vals = [float(r["p95_wait_pct"]) for r in med_rows + high_rows]
    mean_pct = sum(pct_vals) / len(pct_vals) if pct_vals else float("nan")
    print(f"{cfg:20} mean p95_wait %% change (medium+high, both penalties) = {mean_pct:+.2f}%  "
          f"(values: {[round(v,1) for v in pct_vals]})")
    if best is None or mean_pct < best[1]:
        best = (cfg, mean_pct)

print(f"\nSELECTED (largest reduction among harm-free configs): {best[0]} ({best[1]:+.2f}%)")

# --- full grid table for the report ---
print("\n" + "=" * 100)
print("FULL GRID (p95_wait %% change, sign_p, fires, recall, precision) -- condensed")
print("=" * 100)
for cfg in configs:
    print(f"\n--- {cfg} ---")
    for wl in workloads:
        for penalty in ["0.0", "2.0"]:
            r = next((r for r in rows if r["config"] == cfg and r["workload"] == wl and r["penalty"] == penalty), None)
            if r is None:
                continue
            print(f"  {wl:16} p={penalty}  p95_wait {r['p95_wait_pct']:>7}%  sign_p={r['p95_wait_sign_p']:>8}  "
                  f"harm={r['any_significant_harm']:5}  fires={float(r['detector_fires']):.1f}  "
                  f"recall={r['recall']}  precision={r['precision']}")
