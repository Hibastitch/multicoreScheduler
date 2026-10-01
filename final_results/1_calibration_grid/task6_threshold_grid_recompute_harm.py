"""
Recompute the threshold-calibration grid's harm flags from the existing
per-seed CSVs (2026-09-27h correction). No new simulations.

BUG (see Readme.md): sign_p(w, n) uses k = max(w, n-w), which is
SYMMETRIC -- sign_p(harms, n_eff) == sign_p(wins, n_eff) whenever
harms + wins == n_eff. So the original "any_significant_harm" rule
(p_harm < 0.05 and mean_var > mean_base) is NOT actually testing "harms
are significantly more common than wins" -- it tests only "the wins/
harms split is far from 50/50 in EITHER direction," then uses the
outlier-sensitive mean comparison to decide which way to label it. That
can flag a metric where wins >> harms (most seeds improved) as "harm"
if a few outlier seeds pull the mean the other way.

CORRECTED per-metric harm rule: harms > wins (direction must actually
favor harm by count, not just by mean) AND sign_p(harms, n_eff) < 0.05
(equivalently sign_p(wins, n_eff) < 0.05, they're identical -- this
just requires the split itself be significant) AND mean_var > mean_base
+ TIE_TOLERANCE (mean also moved in the harmful direction).

TASK 9 v3 (docs/NOTEBOOK.md 2026-09-29h, when TASK9_V3=1): applies rule
(e) -- only penalties 0 and 0.5 disqualify a config; 2ms is measured/
reported but does not. This script's corrected `harms>wins` rule is the
authoritative one in this project (standing practice since the
2026-09-27h correction), so in v3 mode this is also the script that
writes selected_config_v3.json (selected config's thresholds + runner-
up, or nulls if none is harm-free) for task6_confirmation_run.py's v3
mode to read.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
import csv
import glob
import json
import math
import os
import re

from paired_compare import wilcoxon_signed_rank, format_p, TIE_TOLERANCE

METRICS = ["p95_wait", "avg_wait", "avg_slowdown"]

# TASK 8 v2 RE-RUN (2026-09-29, Step 4): TASK8_V2=1 reads the _v2-
# suffixed grid CSVs. tradeoff.py and harm_breakdown.py both import
# load_rows() from here, so this one change propagates to both.
V2_SUFFIX = "_v2" if os.environ.get("TASK8_V2") else ""

# TASK 9 v3 RE-RUN (2026-09-29, Step 4): TASK9_V3=1 reads the _v3-
# suffixed grid CSVs and applies rule (e)'s penalty-based disqualification.
V3 = bool(os.environ.get("TASK9_V3"))
V3_SUFFIX = "_v3" if V3 else ""

# TASK 10 v4 RE-RUN (2026-09-30, Step 3): TASK10_V4=1 reads the _v4-
# suffixed grid CSVs, applies the SAME rule (e) penalty-based
# disqualification as v3 (only 0/0.5 disqualify), and -- since this
# script's corrected `harms>wins` rule is the authoritative one -- also
# writes selected_config_v4.json for task6_confirmation_run.py's v4
# mode to read.
V4 = bool(os.environ.get("TASK10_V4"))
V4_SUFFIX = "_v4" if V4 else ""
SUFFIX = V4_SUFFIX or V3_SUFFIX or V2_SUFFIX

# PRE-PUBLICATION AUDIT FIX (2026-10-01, docs/NOTEBOOK.md): running this
# script with NONE of TASK8_V2/TASK9_V3/TASK10_V4 set used to silently
# fall back to v1 behavior AND overwrite the un-suffixed v1 result files
# already on disk -- no warning, no version check. Now a hard error
# unless TASK6_V1_LEGACY=1 is set explicitly (the only way to get that
# old v1 behavior back on purpose). Checked BEFORE any file is opened --
# also protects task6_threshold_grid_tradeoff.py and
# task6_threshold_grid_harm_breakdown.py, which both import from this
# module before computing anything of their own.
if not SUFFIX and not os.environ.get("TASK6_V1_LEGACY"):
    raise SystemExit(
        "ERROR: no pipeline version selected -- refusing to run (would "
        "silently reproduce v1 and overwrite the un-suffixed v1 result "
        "files already on disk). Set TASK10_V4=1 to reproduce the "
        "published v4 results (the current pipeline); TASK9_V3=1 or "
        "TASK8_V2=1 for an earlier version; or TASK6_V1_LEGACY=1 to "
        "deliberately run the original v1 config/seeds on purpose."
    )

RULE_PENALTIES = {"0.0", "0.5"} if (V3 or V4) else {"0.0", "2.0"}


def sign_p(wins, n):
    if n == 0:
        return 1.0
    k = max(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def load_rows():
    rows = []
    for path in glob.glob(f"results_task6_threshold_grid{SUFFIX}_*_perseed.csv"):
        if SUFFIX == "" and ("_v2_" in path or "_v3_" in path or "_v4_" in path):
            continue  # plain mode must not also pick up v2/v3 files
        with open(path, newline="") as f:
            rows.extend(csv.DictReader(f))
    return rows


def group_key(r):
    return (r["workload"], r["penalty"], r["config"])


def main():
    rows = load_rows()
    groups = {}
    for r in rows:
        groups.setdefault(group_key(r), []).append(r)

    results = []  # one dict per (workload, penalty, config)
    for (workload, penalty, config), grp in groups.items():
        n = len(grp)
        rec = dict(workload=workload, penalty=penalty, config=config, n=n)
        any_old_harm = False
        any_new_harm = False
        per_metric = {}
        for metric in METRICS:
            base_vals = [float(r[f"baseline_{metric}"]) for r in grp]
            var_vals = [float(r[f"burst_aware_{metric}"]) for r in grp]
            diffs = [v - b for b, v in zip(base_vals, var_vals)]
            wins = sum(1 for d in diffs if d < -TIE_TOLERANCE)
            harms = sum(1 for d in diffs if d > TIE_TOLERANCE)
            ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
            n_eff = n - ties
            mean_base = sum(base_vals) / n
            mean_var = sum(var_vals) / n
            pct = ((mean_var - mean_base) / mean_base * 100) if mean_base else float("nan")
            p_harm = sign_p(harms, n_eff)  # == sign_p(wins, n_eff) always (the bug)
            wr = wilcoxon_signed_rank(diffs)

            old_harm = (p_harm < 0.05) and (mean_var > mean_base + TIE_TOLERANCE)
            new_harm = (harms > wins) and (p_harm < 0.05) and (mean_var > mean_base + TIE_TOLERANCE)

            per_metric[metric] = dict(
                wins=wins, harms=harms, ties=ties, n_eff=n_eff,
                pct=pct, sign_p=p_harm, wilcoxon_p=wr["p"],
                old_harm=old_harm, new_harm=new_harm,
            )
            any_old_harm = any_old_harm or old_harm
            any_new_harm = any_new_harm or new_harm

        rec["per_metric"] = per_metric
        rec["any_old_harm"] = any_old_harm
        rec["any_new_harm"] = any_new_harm
        results.append(rec)

    # --- 1. print every cell flagged under EITHER rule ---
    print("=" * 100)
    print("CELLS FLAGGED UNDER OLD RULE AND/OR NEW RULE")
    print("=" * 100)
    flip_count = 0
    for rec in sorted(results, key=lambda r: (r["workload"], r["config"], r["penalty"])):
        if not (rec["any_old_harm"] or rec["any_new_harm"]):
            continue
        flagged_metrics = [m for m in METRICS if rec["per_metric"][m]["old_harm"] or rec["per_metric"][m]["new_harm"]]
        if rec["any_old_harm"] != rec["any_new_harm"]:
            flip_count += 1
            flip_note = "  <-- FLIPS (old != new)"
        else:
            flip_note = ""
        print(f"\n{rec['workload']:16} penalty={rec['penalty']} {rec['config']:14}{flip_note}")
        for m in flagged_metrics:
            pm = rec["per_metric"][m]
            print(f"    {m:14} wins={pm['wins']:3} harms={pm['harms']:3} ties={pm['ties']:3}  "
                  f"pct={pm['pct']:+7.2f}%  sign_p={format_p(pm['sign_p'])}  wilcoxon_p={format_p(pm['wilcoxon_p'])}  "
                  f"old_harm={pm['old_harm']}  new_harm={pm['new_harm']}")

    print(f"\nTotal cells flagged by old rule: {sum(1 for r in results if r['any_old_harm'])}")
    print(f"Total cells flagged by new rule: {sum(1 for r in results if r['any_new_harm'])}")
    print(f"Cells where old/new disagree: {flip_count}")

    # --- 2. re-apply selection rule with corrected flags ---
    print("\n" + "=" * 100)
    print("SELECTION RULE RE-APPLIED WITH CORRECTED HARM FLAG")
    print("=" * 100)
    configs = sorted(set(r["config"] for r in results))
    harm_free_new = []
    for cfg in configs:
        cfg_recs = [r for r in results if r["config"] == cfg]
        # TASK 9 rule (e): only DISQUALIFYING penalties count here -- a
        # no-op filter in v1/v2 (RULE_PENALTIES covers every penalty
        # those grids have).
        harmful = [(r["workload"], r["penalty"]) for r in cfg_recs
                   if r["any_new_harm"] and r["penalty"] in RULE_PENALTIES]
        reported_only = [(r["workload"], r["penalty"]) for r in cfg_recs
                          if r["any_new_harm"] and r["penalty"] not in RULE_PENALTIES]
        status = "HARM" if harmful else "clean"
        print(f"{cfg:16} {status:6} harmful_on={harmful if harmful else '-'}"
              + (f"  (also harmful, NON-disqualifying: {reported_only})" if (V3 and reported_only) else ""))
        if not harmful:
            harm_free_new.append(cfg)
    print(f"\nHarm-free configs (corrected rule): {harm_free_new}")

    print("\nstacked_burst medium/high p95_wait %% change, harm-free (corrected) configs only")
    ranked = []
    for cfg in harm_free_new:
        vals = []
        for wl in ["stacked_medium", "stacked_high"]:
            for rec in results:
                if rec["config"] == cfg and rec["workload"] == wl and rec["penalty"] in RULE_PENALTIES:
                    vals.append(rec["per_metric"]["p95_wait"]["pct"])
        mean_pct = sum(vals) / len(vals) if vals else float("nan")
        print(f"{cfg:16} mean p95_wait %% change = {mean_pct:+.2f}%  (values: {[round(v,1) for v in vals]})")
        ranked.append((cfg, mean_pct))
    ranked.sort(key=lambda t: t[1])
    best = ranked[0] if ranked else None
    runner_up = ranked[1] if len(ranked) > 1 else None
    # TASK 9 STEP 0 (2026-09-29): an empty harm-free-under-the-corrected-
    # rule set is a real, reportable outcome -- see docs/NOTEBOOK.md's
    # "Task 8 result" entry (the v2 grid found all 12 configs harmful) --
    # not a bug to crash on.
    if best is None:
        print("\nNO CONFIG SELECTED (corrected rule): every config was flagged "
              "harmful on at least one (workload, penalty, metric) -- see the "
              "harm-free list above (empty).")
    else:
        print(f"\nSELECTED (corrected rule): {best[0]} ({best[1]:+.2f}%)")
        if (V3 or V4) and runner_up:
            print(f"RUNNER-UP (corrected rule): {runner_up[0]} ({runner_up[1]:+.2f}%)")

    # TASK 9 v3 / TASK 10 v4: this IS the authoritative (corrected-rule)
    # selection -- write it for task6_confirmation_run.py's matching
    # mode to read.
    if V3 or V4:
        def _thresholds_for(cfg_name):
            m = re.match(r"^q(\d+)_a([\d.]+)_(or|and)$", cfg_name)
            if not m:
                return None
            return dict(config=cfg_name, queue_growth_threshold=int(m.group(1)),
                        arrival_rate_threshold=float(m.group(2)), combine=m.group(3))

        out = dict(selected=None, selected_mean_p95_pct=None,
                    runner_up=None, runner_up_mean_p95_pct=None,
                    harm_free_configs=harm_free_new, rule_penalties=sorted(RULE_PENALTIES))
        if best is not None:
            out["selected"] = _thresholds_for(best[0])
            out["selected_mean_p95_pct"] = best[1]
        if runner_up is not None:
            out["runner_up"] = _thresholds_for(runner_up[0])
            out["runner_up_mean_p95_pct"] = runner_up[1]
        out_name = f"selected_config{SUFFIX}.json"
        with open(out_name, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote {out_name} "
              f"(selected={out['selected']['config'] if out['selected'] else None}, "
              f"runner_up={out['runner_up']['config'] if out['runner_up'] else None})")

    # --- 3. old selection for comparison ---
    print("\n" + "=" * 100)
    print("OLD RULE (for comparison)")
    print("=" * 100)
    harm_free_old = []
    for cfg in configs:
        cfg_recs = [r for r in results if r["config"] == cfg]
        harmful = [(r["workload"], r["penalty"]) for r in cfg_recs if r["any_old_harm"]]
        if not harmful:
            harm_free_old.append(cfg)
    print(f"Harm-free configs (old rule): {harm_free_old}")


if __name__ == "__main__":
    main()
