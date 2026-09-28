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
"""

import csv
import glob
import math

from paired_compare import wilcoxon_signed_rank, format_p, TIE_TOLERANCE

METRICS = ["p95_wait", "avg_wait", "avg_slowdown"]


def sign_p(wins, n):
    if n == 0:
        return 1.0
    k = max(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def load_rows():
    rows = []
    for path in glob.glob("results_task6_threshold_grid_*_perseed.csv"):
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
        harmful = [(r["workload"], r["penalty"]) for r in cfg_recs if r["any_new_harm"]]
        status = "HARM" if harmful else "clean"
        print(f"{cfg:16} {status:6} harmful_on={harmful if harmful else '-'}")
        if not harmful:
            harm_free_new.append(cfg)
    print(f"\nHarm-free configs (corrected rule): {harm_free_new}")

    print("\nstacked_burst medium/high p95_wait %% change, harm-free (corrected) configs only")
    best = None
    for cfg in harm_free_new:
        vals = []
        for wl in ["stacked_medium", "stacked_high"]:
            for rec in results:
                if rec["config"] == cfg and rec["workload"] == wl:
                    vals.append(rec["per_metric"]["p95_wait"]["pct"])
        mean_pct = sum(vals) / len(vals) if vals else float("nan")
        print(f"{cfg:16} mean p95_wait %% change = {mean_pct:+.2f}%  (values: {[round(v,1) for v in vals]})")
        if best is None or mean_pct < best[1]:
            best = (cfg, mean_pct)
    print(f"\nSELECTED (corrected rule): {best[0]} ({best[1]:+.2f}%)")

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
