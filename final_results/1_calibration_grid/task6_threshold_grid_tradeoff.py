"""
Trade-off scatter: HARM (# of 9 workloads flagged harmful, corrected
rule) vs BENEFIT (mean p95_wait %% reduction on stacked_medium+high,
both penalties) for all 12 threshold-calibration configs. No new
simulations -- reads only the existing per-seed CSVs in this folder.

The corrected harm rule is NOT redefined here -- imported verbatim from
task6_threshold_grid_recompute_harm.py (sign_p, METRICS, load_rows) and
the same per-metric computation from its main() is copied verbatim
below (that module doesn't expose it as a reusable function, so the
loop body is reproduced exactly, not altered).
"""

import csv
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from task6_threshold_grid_recompute_harm import sign_p, load_rows, METRICS
from paired_compare import TIE_TOLERANCE

# TASK 8 v2 RE-RUN (2026-09-29, Step 4): TASK8_V2=1 writes _v2-suffixed
# outputs and SKIPS the EXPECTED_HARM/EXPECTED_BENEFIT mismatch gate
# below -- those are v1-run reference values; a v2 run is expected to
# differ (that's the whole point) and must not abort on a "mismatch"
# that just means the fidelity fixes changed something.
V2_SUFFIX = "_v2" if os.environ.get("TASK8_V2") else ""

# TASK 9 v3 RE-RUN (2026-09-29, Step 4): TASK9_V3=1 writes _v3-suffixed
# outputs, applies rule (e) (only penalties 0/0.5 count toward harm_count
# and benefit_score; 2ms is still recorded in the CSV for visibility but
# excluded from both), and reads SELECTED/RUNNER_UP from
# selected_config_v3.json (written by task6_threshold_grid_recompute_
# harm.py's v3 mode) instead of the hardcoded v1 constants -- also skips
# the EXPECTED_HARM/EXPECTED_BENEFIT gate, same reasoning as v2.
V3 = bool(os.environ.get("TASK9_V3"))
V3_SUFFIX = "_v3" if V3 else ""
SUFFIX = V3_SUFFIX or V2_SUFFIX
RULE_PENALTIES = {"0.0", "0.5"} if V3 else {"0.0", "2.0"}
ALL_PENALTIES = ["0.0", "0.5", "2.0"] if V3 else ["0.0", "2.0"]


def _pen_key(pen):
    # v1/v2 keeps the ORIGINAL "p0"/"p2" scheme (pen[0]) -- unambiguous
    # there since penalties are only ever "0.0"/"2.0". v3 has 3
    # penalties, where pen[0] would collide "0.0" and "0.5" both to
    # "p0" -- uses "p0_0"/"p0_5"/"p2_0" instead.
    return f"p{pen.replace('.', '_')}" if V3 else f"p{pen[0]}"


SELECTED_DEFAULT = "q2_a1.5_and"  # v1's own pre-registered selection -- unused when V3
RUNNER_UP_DEFAULT = "q4_a1.5_or"

EXPECTED_HARM = {
    "q2_a1.5_and": 0, "q4_a1.5_or": 0, "q8_a0.8_and": 0, "q8_a1.5_and": 0,
    "q2_a0.8_and": 1, "q2_a1.5_or": 1, "q4_a0.8_and": 1, "q4_a1.5_and": 1, "q8_a1.5_or": 1,
    "q2_a0.8_or": 3, "q4_a0.8_or": 3, "q8_a0.8_or": 3,
}
EXPECTED_BENEFIT = {
    "q2_a1.5_and": 20.17, "q4_a1.5_or": 19.00, "q8_a0.8_and": 9.98, "q8_a1.5_and": 9.97,
}


def compute_results():
    """Verbatim copy of task6_threshold_grid_recompute_harm.main()'s
    per-(workload,penalty,config) computation loop (lines 53-94 there)
    -- not a redefinition of the harm rule, the same rule reproduced so
    it can be reused as data instead of only printed."""
    rows = load_rows()
    groups = {}
    for r in rows:
        key = (r["workload"], r["penalty"], r["config"])
        groups.setdefault(key, []).append(r)

    results = []
    for (workload, penalty, config), grp in groups.items():
        n = len(grp)
        rec = dict(workload=workload, penalty=penalty, config=config, n=n)
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
            p_harm = sign_p(harms, n_eff)

            new_harm = (harms > wins) and (p_harm < 0.05) and (mean_var > mean_base + TIE_TOLERANCE)

            per_metric[metric] = dict(wins=wins, harms=harms, ties=ties, n_eff=n_eff,
                                       pct=pct, sign_p=p_harm, new_harm=new_harm)
            any_new_harm = any_new_harm or new_harm

        rec["per_metric"] = per_metric
        rec["any_new_harm"] = any_new_harm
        results.append(rec)
    return results


def parse_config(config):
    parts = config.split("_")
    q = parts[0][1:]
    a = parts[1][1:]
    combine = parts[2]
    return q, a, combine


def main():
    results = compute_results()
    configs = sorted(set(r["config"] for r in results))
    assert len(configs) == 12, f"expected 12 configs, found {len(configs)}: {configs}"

    points = []
    mismatches = []
    for cfg in configs:
        cfg_recs = [r for r in results if r["config"] == cfg]

        # TASK 9 rule (e): only DISQUALIFYING penalties count toward
        # harm_count/benefit_score -- a no-op filter in v1/v2
        # (RULE_PENALTIES covers every penalty those grids have).
        harmed_workloads = sorted(set(r["workload"] for r in cfg_recs
                                       if r["any_new_harm"] and r["penalty"] in RULE_PENALTIES))
        harm_count = len(harmed_workloads)

        vals = {}
        for wl in ["stacked_medium", "stacked_high"]:
            for pen in ALL_PENALTIES:
                rec = next((r for r in cfg_recs if r["workload"] == wl and r["penalty"] == pen), None)
                if rec is not None:
                    vals[f"{wl}_{_pen_key(pen)}"] = rec["per_metric"]["p95_wait"]["pct"]
        benefit_vals = {k: v for k, v in vals.items()
                         if any(k == f"{wl}_{_pen_key(pen)}" for wl in ["stacked_medium", "stacked_high"]
                                for pen in RULE_PENALTIES)}
        benefit_score = -sum(benefit_vals.values()) / len(benefit_vals)  # pct is negative for a reduction

        q, a, combine = parse_config(cfg)
        points.append(dict(
            config=cfg, combine=combine, q=q, a=a,
            harm_count=harm_count, harmed_workloads=";".join(harmed_workloads),
            benefit_score=benefit_score, **vals,
        ))

        if not V3:
            if EXPECTED_HARM.get(cfg) != harm_count:
                mismatches.append(f"{cfg}: harm_count computed={harm_count} expected={EXPECTED_HARM.get(cfg)}")
            if cfg in EXPECTED_BENEFIT and abs(EXPECTED_BENEFIT[cfg] - benefit_score) > 0.01:
                mismatches.append(f"{cfg}: benefit_score computed={benefit_score:.2f} expected={EXPECTED_BENEFIT[cfg]}")

    print(f"{'config':16} harm  benefit   harmed_workloads")
    for p in sorted(points, key=lambda p: p["config"]):
        print(f"{p['config']:16} {p['harm_count']:4}  {p['benefit_score']:6.2f}   {p['harmed_workloads']}")

    if V2_SUFFIX or V3:
        tag = "TASK9_V3" if V3 else "TASK8_V2"
        print(f"\n({tag} mode: skipping the v1-reference mismatch gate below -- "
              f"a {tag} run is EXPECTED to differ from the original grid's values.)")
    elif mismatches:
        print("\nMISMATCH between computed values and threshold_grid_analysis.txt -- STOPPING:")
        for m in mismatches:
            print("  " + m)
        sys.exit(1)
    else:
        print("\nAll 12 harm_count and 4 benefit_score values match threshold_grid_analysis.txt exactly.")

    with open(f"tradeoff_points{SUFFIX}.csv", "w", newline="") as f:
        cols = ["config", "combine", "q", "a", "harm_count", "harmed_workloads", "benefit_score"] + \
               [f"{wl}_{_pen_key(pen)}" for wl in ["stacked_medium", "stacked_high"] for pen in ALL_PENALTIES]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(points)
    print(f"Wrote tradeoff_points{SUFFIX}.csv")

    # TASK 9 v3: SELECTED/RUNNER_UP come from selected_config_v3.json
    # (written by task6_threshold_grid_recompute_harm.py's v3 mode),
    # not the hardcoded v1 constants -- None/None (no star, no bold
    # label) if the file doesn't exist yet or no config was selected.
    selected, runner_up = SELECTED_DEFAULT, RUNNER_UP_DEFAULT
    if V3:
        selected, runner_up = None, None
        try:
            with open("selected_config_v3.json") as f:
                sel = json.load(f)
            selected = sel["selected"]["config"] if sel.get("selected") else None
            runner_up = sel["runner_up"]["config"] if sel.get("runner_up") else None
        except FileNotFoundError:
            print("\n(selected_config_v3.json not found -- run task6_threshold_grid_recompute_harm.py "
                  "first. Plotting with no SELECTED/RUNNER_UP marker.)")

    make_plot(points, selected, runner_up)


def make_plot(points, selected=SELECTED_DEFAULT, runner_up=RUNNER_UP_DEFAULT):
    plt.rcParams.update({
        "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
        "legend.fontsize": 6.5, "font.family": "sans-serif",
    })
    fig, ax = plt.subplots(figsize=(3.5, 2.6), dpi=300)

    max_x = max(p["harm_count"] for p in points)
    ax.axvspan(-0.5, 0.5, color="0.90", zorder=0)
    ax.text(0, ax.get_ylim()[1], "", alpha=0)  # placeholder, ylim set after scatter

    SELECTED = selected
    RUNNER_UP = runner_up

    # group near-identical points so overlapping dots get ONE marker + combined label
    def key(p):
        return (round(p["harm_count"]), round(p["benefit_score"], 1), p["combine"])

    groups = {}
    for p in points:
        groups.setdefault(key(p), []).append(p)

    def combined_label(grp):
        """Join full config names if 2+ points share one marker. Detects
        which of q/a actually differs across the group instead of
        assuming it's always q (q8_a0.8_and + q8_a1.5_and differ on a,
        not q -- a fixed assumption here produced "q8/8_a0.8_and",
        which is wrong; this checks both fields and falls back to
        listing full names if more than one field differs)."""
        if len(grp) == 1:
            return grp[0]["config"]
        qs = sorted(set(pp["q"] for pp in grp))
        as_ = sorted(set(pp["a"] for pp in grp))
        combines = sorted(set(pp["combine"] for pp in grp))
        if len(qs) > 1 and len(as_) == 1 and len(combines) == 1:
            return f"q{'/'.join(qs)}_a{as_[0]}_{combines[0]}"
        if len(as_) > 1 and len(qs) == 1 and len(combines) == 1:
            return f"q{qs[0]}_a{'/'.join(as_)}_{combines[0]}"
        return " / ".join(sorted(pp["config"] for pp in grp))

    # Per-config label anchor (dx, dy in data units) and alignment, tuned
    # by hand after inspecting the rendered plot (recipe fallback for no
    # adjustText: 12 points is few enough to place by hand and verify).
    LABEL_OFFSETS = {
        "q2_a1.5_and":            ( 0.18,  0.75, "left",  "bottom"),  # selected, star -- right of marker
        "q4_a1.5_or":             ( 0.18, -0.75, "left",  "top"),     # right of marker, clear of 17.5 ytick
        "q8_a0.8_and|q8_a1.5_and": (0.18, -0.55, "left",  "top"),
        "q2_a0.8_and":            (0.55,  0.00, "left",  "center"),
        "q2_a1.5_or":             (0.18,  0.65, "left",  "bottom"),
        "q4_a0.8_and":            (0.18, -0.85, "left",  "top"),
        "q4_a1.5_and":            (0.18,  0.65, "left",  "bottom"),
        "q8_a1.5_or":             (0.18, -0.05, "left",  "top"),
        "q2_a0.8_or":             (-0.25, -1.495, "center", "center"),  # manual: text centered ~(2.75, 20.3), below marker
        "q4_a0.8_or|q8_a0.8_or":  (-0.15,  1.472, "center", "center"),  # manual: text centered ~(2.85, 23.6), above marker -- shifted right, collided with q2_a0.8_and at x=2.75
    }

    for grp in groups.values():
        p0 = grp[0]
        marker = "o" if p0["combine"] == "and" else "^"
        is_selected = any(p["config"] == SELECTED for p in grp)

        if is_selected:
            ax.scatter(p0["harm_count"], p0["benefit_score"], marker="*", s=110,
                       facecolor="black", edgecolor="black", zorder=5)
        else:
            ax.scatter(p0["harm_count"], p0["benefit_score"], marker=marker, s=30,
                       facecolor="white" if len(grp) == 1 else "0.6",
                       edgecolor="black", linewidth=0.8, zorder=4)

        label = combined_label(grp)
        lookup_key = label if len(grp) == 1 else "|".join(sorted(pp["config"] for pp in grp))
        dx, dy, ha, va = LABEL_OFFSETS.get(lookup_key, (0.18, 0.5, "left", "bottom"))
        fontweight = "bold" if is_selected else "normal"
        ax.annotate(label, (p0["harm_count"], p0["benefit_score"]),
                    xytext=(p0["harm_count"] + dx, p0["benefit_score"] + dy),
                    fontsize=6.5 if is_selected else 5.8,
                    fontweight=fontweight, ha=ha, va=va,
                    arrowprops=dict(arrowstyle="-", lw=0.4, color="0.4",
                                     shrinkA=1, shrinkB=3))

    ax.set_xlabel("Workloads with significant harm (of 9)")
    ax.set_ylabel("p95 wait reduction on\nstacked medium/high (%)")
    ax.set_xlim(-0.6, 3.5)
    ax.set_xticks(range(0, max_x + 1))
    ymin = min(p["benefit_score"] for p in points) - 2
    ymax = max(p["benefit_score"] for p in points) + 3
    ax.set_ylim(ymin, ymax)
    ax.axvspan(-0.5, 0.5, color="0.90", zorder=0)
    ax.text(0, ymax - 0.3, "harm-free", fontsize=6, style="italic", color="0.35",
            ha="center", va="top")

    legend_elems = [
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="white",
                    markeredgecolor="black", markersize=5, label="combine=AND"),
        plt.Line2D([0], [0], marker="^", color="w", markerfacecolor="white",
                    markeredgecolor="black", markersize=5, label="combine=OR"),
        plt.Line2D([0], [0], marker="*", color="w", markerfacecolor="black",
                    markeredgecolor="black", markersize=9, label="selected"),
    ]
    ax.legend(handles=legend_elems, loc="upper right", frameon=False, handletextpad=0.3,
              borderaxespad=0.2, labelspacing=0.3)

    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    fig.tight_layout(pad=0.4)
    fig.savefig(f"figure_threshold_grid_tradeoff{SUFFIX}.png", dpi=300)
    fig.savefig(f"figure_threshold_grid_tradeoff{SUFFIX}.pdf")
    print(f"Wrote figure_threshold_grid_tradeoff{SUFFIX}.png and .pdf")


if __name__ == "__main__":
    main()
