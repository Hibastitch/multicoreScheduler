"""
Merge the 9 per-workload threshold-grid summary CSVs and apply the
PRE-REGISTERED selection rule (stated before seeing results).

v1/v2 rule: choose the config with NO significant harm on ANY metric
(p95_wait, avg_wait, avg_slowdown), on ANY workload, at EITHER penalty,
and, among those, the largest mean p95_wait reduction on stacked_burst
medium/high (averaged over both penalties).

TASK 9 v3 rule (e) (docs/NOTEBOOK.md 2026-09-29h, when TASK9_V3=1):
harm-free at penalties 0 and 0.5 ONLY disqualifies -- penalty=2ms is
measured and reported in full but does NOT disqualify a config, and is
NOT included in the benefit average either. This script's own harm flag
is the OLD (pre-2026-09-27h) rule -- report-only here, as it always was;
task6_threshold_grid_recompute_harm.py's corrected `harms>wins` rule is
the authoritative one and is what writes selected_config_v3.json for
task6_confirmation_run.py's v3 mode to read.
"""

import csv
import glob
import os

# TASK 8 v2 RE-RUN (2026-09-29, Step 4): TASK8_V2=1 reads the _v2-
# suffixed grid CSVs instead of the original ones. No new simulations
# either way -- this script only merges existing CSVs.
V2_SUFFIX = "_v2" if os.environ.get("TASK8_V2") else ""

# TASK 9 v3 RE-RUN (2026-09-29, Step 4): TASK9_V3=1 reads the _v3-
# suffixed grid CSVs and applies rule (e) above instead of the original
# either-penalty-disqualifies rule.
V3 = bool(os.environ.get("TASK9_V3"))
V3_SUFFIX = "_v3" if V3 else ""

# TASK 10 v4 RE-RUN (2026-09-30, Step 3): TASK10_V4=1 reads the _v4-
# suffixed grid CSVs and applies the SAME rule (e) as v3 (only 0/0.5
# disqualify). This script's own harm rule is still the OLD, report-
# only one under v4 too -- task6_threshold_grid_recompute_harm.py stays
# the authoritative script that writes selected_config_v4.json.
V4 = bool(os.environ.get("TASK10_V4"))
V4_SUFFIX = "_v4" if V4 else ""
SUFFIX = V4_SUFFIX or V3_SUFFIX or V2_SUFFIX

# PRE-PUBLICATION AUDIT FIX (2026-10-01, docs/NOTEBOOK.md): running this
# script with NONE of TASK8_V2/TASK9_V3/TASK10_V4 set used to silently
# fall back to v1 behavior (reading the un-suffixed v1 grid CSVs) with
# no warning. Now a hard error unless TASK6_V1_LEGACY=1 is set
# explicitly. Checked BEFORE any file is read.
if not SUFFIX and not os.environ.get("TASK6_V1_LEGACY"):
    raise SystemExit(
        "ERROR: no pipeline version selected -- refusing to run. Set "
        "TASK10_V4=1 to reproduce the published v4 results (the current "
        "pipeline); TASK9_V3=1 or TASK8_V2=1 for an earlier version; or "
        "TASK6_V1_LEGACY=1 to deliberately run the original v1 config on "
        "purpose."
    )

# Penalties that DISQUALIFY a config (harm check) and that the benefit
# average is computed over. v1/v2: both penalties disqualify (the grid
# only ever had 2: 0 and 2). v3/v4: only 0 and 0.5 disqualify/count
# toward the benefit average -- 2ms is measured and reported but neither.
RULE_PENALTIES = {"0.0", "0.5"} if (V3 or V4) else {"0.0", "2.0"}
ALL_PENALTIES = ["0.0", "0.5", "2.0"] if (V3 or V4) else ["0.0", "2.0"]

rows = []
for path in glob.glob(f"results_task6_threshold_grid{SUFFIX}_*_summary.csv"):
    if SUFFIX == "" and ("_v2_" in path or "_v3_" in path or "_v4_" in path):
        continue  # plain mode must not also pick up v2/v3/v4 files
    rows.extend(csv.DictReader(open(path)))

configs = sorted(set(r["config"] for r in rows))
workloads = sorted(set(r["workload"] for r in rows))
print(f"Loaded {len(rows)} rows across {len(workloads)} workloads x {len(configs)} configs "
      f"x {len(ALL_PENALTIES)} penalties")
print(f"Workloads: {workloads}")
print(f"Configs: {configs}\n")
if V3:
    print(f"TASK9_V3 mode: disqualifying penalties = {sorted(RULE_PENALTIES)}; "
          f"penalty=2.0 measured/reported but does NOT disqualify (rule e).\n")
elif V4:
    print(f"TASK10_V4 mode: disqualifying penalties = {sorted(RULE_PENALTIES)}; "
          f"penalty=2.0 measured/reported but does NOT disqualify (rule e).\n")

# --- full grid: per config, any_significant_harm anywhere? ---
print("=" * 100)
print("HARM CHECK per config (any workload, any DISQUALIFYING penalty, any metric)" if (V3 or V4)
      else "HARM CHECK per config (any workload, any penalty, any metric)")
print("=" * 100)
harm_free_configs = []
for cfg in configs:
    cfg_rows = [r for r in rows if r["config"] == cfg]
    harmful = [(r["workload"], r["penalty"]) for r in cfg_rows
               if r["any_significant_harm"] == "True" and r["penalty"] in RULE_PENALTIES]
    reported_only = [(r["workload"], r["penalty"]) for r in cfg_rows
                      if r["any_significant_harm"] == "True" and r["penalty"] not in RULE_PENALTIES]
    status = "HARM" if harmful else "clean"
    print(f"{cfg:20} {status:6} harmful_on={harmful if harmful else '-'}"
          + (f"  (also harmful, NON-disqualifying: {reported_only})" if reported_only else ""))
    if not harmful:
        harm_free_configs.append(cfg)

if V3 or V4:
    print(f"\nHarm-free configs (at disqualifying penalties {sorted(RULE_PENALTIES)}): {harm_free_configs}\n")
else:
    print(f"\nHarm-free configs: {harm_free_configs}\n")

# --- among harm-free configs, largest mean p95_wait reduction on stacked_burst medium/high ---
print("=" * 100)
if V3 or V4:
    print(f"stacked_burst medium/high p95_wait %% change (penalties {sorted(RULE_PENALTIES)} only), "
          f"harm-free configs only")
else:
    print("stacked_burst medium/high p95_wait %% change, harm-free configs only")
print("=" * 100)
ranked = []  # (cfg, mean_pct), sorted best (most negative) first
for cfg in harm_free_configs:
    med_rows = [r for r in rows if r["config"] == cfg and r["workload"] == "stacked_medium"
                and r["penalty"] in RULE_PENALTIES]
    high_rows = [r for r in rows if r["config"] == cfg and r["workload"] == "stacked_high"
                 and r["penalty"] in RULE_PENALTIES]
    pct_vals = [float(r["p95_wait_pct"]) for r in med_rows + high_rows]
    mean_pct = sum(pct_vals) / len(pct_vals) if pct_vals else float("nan")
    if V3 or V4:
        print(f"{cfg:20} mean p95_wait %% change (medium+high, penalties {sorted(RULE_PENALTIES)}) = "
              f"{mean_pct:+.2f}%  (values: {[round(v,1) for v in pct_vals]})")
    else:
        print(f"{cfg:20} mean p95_wait %% change (medium+high, both penalties) = {mean_pct:+.2f}%  "
              f"(values: {[round(v,1) for v in pct_vals]})")
    ranked.append((cfg, mean_pct))
ranked.sort(key=lambda t: t[1])
best = ranked[0] if ranked else None
runner_up = ranked[1] if len(ranked) > 1 else None

# TASK 9 STEP 0 (2026-09-29): an empty harm-free set is a real,
# reportable outcome (see docs/NOTEBOOK.md's "Task 8 result" entry --
# the v2 grid found all 12 configs harmful), not a bug -- print it
# plainly instead of crashing on `best[0]` when `best` is still None.
if best is None:
    print("\nNO CONFIG SELECTED: every config was flagged harmful on at least "
          "one (workload, disqualifying-penalty, metric) -- see the HARM CHECK above.")
else:
    print(f"\nSELECTED (largest reduction among harm-free configs): {best[0]} ({best[1]:+.2f}%)")
    if (V3 or V4) and runner_up:
        print(f"RUNNER-UP: {runner_up[0]} ({runner_up[1]:+.2f}%)")

# --- full grid table for the report ---
print("\n" + "=" * 100)
if V3 or V4:
    print("FULL GRID (p95_wait %% change, sign_p, fires, recall, precision) -- condensed, ALL penalties")
else:
    print("FULL GRID (p95_wait %% change, sign_p, fires, recall, precision) -- condensed")
print("=" * 100)
for cfg in configs:
    print(f"\n--- {cfg} ---")
    for wl in workloads:
        for penalty in ALL_PENALTIES:
            r = next((r for r in rows if r["config"] == cfg and r["workload"] == wl and r["penalty"] == penalty), None)
            if r is None:
                continue
            disq_tag = "" if penalty in RULE_PENALTIES else "  [reported, non-disqualifying]"
            print(f"  {wl:16} p={penalty}  p95_wait {r['p95_wait_pct']:>7}%  sign_p={r['p95_wait_sign_p']:>8}  "
                  f"harm={r['any_significant_harm']:5}  fires={float(r['detector_fires']):.1f}  "
                  f"recall={r['recall']}  precision={r['precision']}{disq_tag}")

# TASK 9 v3: this script's own harm rule (any_significant_harm, above)
# is the OLD, pre-2026-09-27h rule (sign_p(harms,n) symmetry bug) --
# this project's standing practice since that correction is that
# task6_threshold_grid_recompute_harm.py's `harms>wins` rule is the
# authoritative one for any NEW selection. That script (not this one)
# writes selected_config_v3.json for task6_confirmation_run.py to read.
# This script's own SELECTED/RUNNER-UP above stays a report-only,
# old-rule cross-check (as it always was for v1/v2).
