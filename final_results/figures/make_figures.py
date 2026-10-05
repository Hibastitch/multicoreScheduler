"""
Presentation figures for the v4 confirmation result -- reads ONLY the
already-committed CSVs under final_results/2_confirmation/ and
final_results/1_calibration_grid/. No simulation runs here, and
nothing it reads is modified: this script is read-only over published
data, same discipline as final_results/3_idle_check_cost/build_cost_table.py.

Run as: python make_figures.py (no arguments, works from any cwd --
every path below is resolved relative to this file, same convention as
the other final_results/ pipeline scripts). Requires TASK10_V4=1, same
guard as task6_threshold_grid.py/task6_confirmation_run.py/etc: this
reads v4-suffixed CSVs specifically (not v1/v2/v3), so running it
without the env var set is refused rather than silently doing nothing
useful or picking up the wrong files.

Saves every figure as PNG (300 dpi) into this folder.

Everything shown (which workloads "help", where a value is exactly
zero, which cells are any_harm=True, which specific metrics tripped
that harm, how many seeds improved) is read from the CSVs at run time
-- nothing here is a hardcoded result. The only hardcoded choices are
the clearly-named settings directly below: plain-language labels,
colors, which workload/penalty Figs 2/3 look at, which penalties Fig 4
shows, and the one simulator topology constant (MACHINE_CORES) that
isn't itself a CSV column.
"""

import csv
import json
import os
import pathlib
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

HERE = pathlib.Path(__file__).resolve().parent
CONF_DIR = HERE.parent / "2_confirmation"
GRID_DIR = HERE.parent / "1_calibration_grid"

# Same guard as task6_threshold_grid.py/task6_confirmation_run.py/etc
# (pre-publication audit fix, 2026-10-01, docs/NOTEBOOK.md): this script
# reads v4-suffixed CSVs specifically -- refuse to run rather than
# silently produce nothing useful (or, worse, quietly read stale
# un-suffixed v1 files) if the version isn't selected explicitly.
if not os.environ.get("TASK10_V4"):
    raise SystemExit(
        "ERROR: TASK10_V4 is not set -- refusing to run. This script reads "
        "the v4-suffixed confirmation CSVs (results_task6_confirmation_v4_*); "
        "set TASK10_V4=1 before running it, matching every other script in "
        "the v4 pipeline (see docs/PIPELINE.md)."
    )

# ================================================================ settings ==
# The only hardcoded choices in this file. Everything plotted is still
# read from the CSVs; these just say WHICH labels/colors/cells to use.

LABELS = {
    "baseline": "Linux baseline",
    "selected_checked": "Burst trigger + idle check (v4)",
    "selected_unchecked": "Same trigger, no idle check",
}

COLOR = {
    "baseline": "#8C8C8C",
    "selected_checked": "#0072B2",
    "selected_unchecked": "#4A90C4",
    "harm": "#C1121F",
    "sig": "#000000",
}

FIG2_WORKLOAD = "stacked_high"
FIG2_PENALTY = "0.5"

FIG3_WORKLOAD = "bursty_high_s64"

FIG4_PENALTIES = ["0.5", "2.0"]  # matches Fig 1's penalty as the solid line

# simulator/Main.py:20 / simulator/Topology.py:367, num_cores=32 -- the
# default (and, verified, never-overridden) machine size for every v4
# grid/confirmation run. Not a CSV column, so it can't be read from one;
# cited here instead of silently baked into a label string.
MACHINE_CORES = 32

# The 6 metrics the harm rule is actually computed over (docs/PIPELINE.md's
# "Harm rule" / final_results/2_confirmation/task6_confirmation_analyze.py's
# own METRICS list) -- CSV schema, not a result; needed to know which
# columns to read any one cell's harm reason from.
HARM_METRICS = ["p95_wait", "p99_wait", "avg_wait", "avg_slowdown",
                "p95_slowdown", "makespan_excess"]

SIG_ALPHA = 0.05
ZERO_TOL = 1e-6  # same tie tolerance as simulator/paired_compare.py

WORKLOAD_ORDER = [
    "stacked_low", "stacked_medium", "stacked_high",
    "bursty_high_s24", "bursty_high_s64", "heavy_tail_high",
    "rate0.5_s4", "rate0.5_s12", "rate0.75_s4", "rate0.75_s12",
    "rate1.0_s4", "rate1.0_s12", "rate1.5_s4", "rate1.5_s12",
    "rate3.0_s4", "rate3.0_s12",
]

plt.rcParams.update({
    "font.size": 14,
    "axes.labelsize": 14,
    "legend.fontsize": 12,
    "xtick.labelsize": 12,
    "ytick.labelsize": 12,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": False,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
})


# ================================================================== I/O ==

def save(fig, name):
    fig.savefig(HERE / f"{name}.png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {name}.png")


def read_csv(name):
    path = CONF_DIR / name
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def read_grid_csv(name):
    path = GRID_DIR / name
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def read_grid_json(name):
    with open(GRID_DIR / name) as f:
        return json.load(f)


def index_by_wl_pen(rows):
    return {(r["workload"], r["penalty"]): r for r in rows}


def fnum(row, col):
    return float(row[col])


def is_harm(row):
    return row["any_harm"] == "True"


def harmful_metrics(row):
    """Which of HARM_METRICS actually tripped harm on this row -- read
    from that row's own <metric>_harm columns, not assumed from any_harm
    alone (any_harm is True iff at least one of these is)."""
    return [m for m in HARM_METRICS if row.get(f"{m}_harm") == "True"]


def pretty_metric(name):
    return name.replace("_", " ")


# =============================================================== Fig 0 ==

def config_label(row):
    combine_word = "OR" if row["combine"] == "or" else "AND"
    return f"q≥{int(float(row['q']))} {combine_word} rate≥{row['a']}"


def fig0_calibration_grid():
    rows = read_grid_csv("tradeoff_points_v4.csv")
    sel = read_grid_json("selected_config_v4.json")

    # The 4 cells the selection score is averaged over (docs/PIPELINE.md's
    # Selection step): stacked_medium/stacked_high x penalties 0/0.5.
    P95_COLS = ["stacked_medium_p0_0", "stacked_medium_p0_5",
                "stacked_high_p0_0", "stacked_high_p0_5"]
    points = []
    for r in rows:
        mean_pct = sum(fnum(r, c) for c in P95_COLS) / len(P95_COLS)
        points.append(dict(
            config=r["config"], harm_count=int(r["harm_count"]),
            mean_pct=mean_pct, label=config_label(r),
        ))

    # Verify against a SECOND, independently-written source
    # (selected_config_v4.json, written by task6_threshold_grid_recompute_
    # harm.py, not by task6_threshold_grid_tradeoff.py which wrote
    # tradeoff_points_v4.csv) before drawing anything.
    harm_free = sorted(p["config"] for p in points if p["harm_count"] == 0)
    disqualified = sorted(p["config"] for p in points if p["harm_count"] > 0)
    print(f"Fig 0: {len(disqualified)}/{len(points)} disqualified configs, "
          f"from tradeoff_points_v4.csv's harm_count>0: {disqualified}")
    expected_harm_free = sorted(sel["harm_free_configs"])
    print(f"Fig 0: {len(harm_free)}/{len(points)} harm-free configs: {harm_free}")
    if harm_free != expected_harm_free:
        print(f"MISMATCH: harm-free configs from tradeoff_points_v4.csv "
              f"({harm_free}) != selected_config_v4.json's harm_free_configs "
              f"({expected_harm_free})")
    else:
        print("Fig 0 cross-check OK: harm-free config list matches "
              "selected_config_v4.json exactly.")

    selected_name = sel["selected"]["config"]
    runner_up_name = sel["runner_up"]["config"]
    selected_pt = next(p for p in points if p["config"] == selected_name)
    runner_up_pt = next(p for p in points if p["config"] == runner_up_name)
    for pt, json_key, tag in [(selected_pt, "selected_mean_p95_pct", "selected"),
                               (runner_up_pt, "runner_up_mean_p95_pct", "runner-up")]:
        json_val = sel[json_key]
        if abs(pt["mean_pct"] - json_val) > 1e-6:
            print(f"MISMATCH: {tag} ({pt['config']}) mean_pct from "
                  f"tradeoff_points_v4.csv = {pt['mean_pct']:.6f}, but "
                  f"selected_config_v4.json's {json_key} = {json_val:.6f}")
        else:
            print(f"Fig 0 cross-check OK: {tag} ({pt['config']}) mean_pct "
                  f"{pt['mean_pct']:.4f}% matches selected_config_v4.json exactly.")
    print(f"Fig 0: SELECTED={selected_name} ({selected_pt['mean_pct']:.2f}%), "
          f"RUNNER-UP={runner_up_name} ({runner_up_pt['mean_pct']:.2f}%)")

    # Horizontal bar chart, one bar per config (all 12, never merged --
    # unlike a scatter, separate bars don't overplot even when two
    # configs' scores coincide, e.g. q4_a0.8_or/q8_a0.8_or below).
    # Sorted by selection score, best (most negative = biggest p95_wait
    # reduction) at the top, matching Fig 1's "best at top" convention.
    ordered = sorted(points, key=lambda p: p["mean_pct"])
    ordered_for_plot = list(reversed(ordered))  # barh: first entry plots at the bottom

    HARM_FREE_COLOR = COLOR["selected_checked"]
    DISQUALIFIED_COLOR = COLOR["harm"]

    y = list(range(len(ordered_for_plot)))
    fig, ax = plt.subplots(figsize=(10, 8.5))
    bar_colors = [HARM_FREE_COLOR if p["harm_count"] == 0 else DISQUALIFIED_COLOR
                  for p in ordered_for_plot]
    ax.barh(y, [p["mean_pct"] for p in ordered_for_plot], height=0.65,
            color=bar_colors, zorder=3)
    ax.axvline(0, color="black", linewidth=1.0)

    xmin = min(p["mean_pct"] for p in points)
    xmax = max(p["mean_pct"] for p in points)
    label_pad = (xmax - xmin) * 0.045 + 0.3  # layout: space for the harm-count number

    for yy, p in zip(y, ordered_for_plot):
        x_end = p["mean_pct"]
        going_left = x_end <= 0
        xtxt = x_end - label_pad if going_left else x_end + label_pad
        ax.annotate(str(p["harm_count"]), xy=(xtxt, yy), va="center",
                    ha="right" if going_left else "left", fontsize=10.5, color="black")
        if p["config"] == selected_name:
            ax.plot(x_end, yy, marker="*", markersize=20, color="black", zorder=6)
        elif p["config"] == runner_up_name:
            ax.plot(x_end, yy, marker="D", markersize=11, markerfacecolor="white",
                    markeredgecolor="black", markeredgewidth=2, zorder=6)

    ax.set_yticks(y)
    ax.set_yticklabels([p["label"] for p in ordered_for_plot])
    # Bars are drawn from 0 (barh's default), so the view must include 0
    # regardless of whether every value happens to be negative -- excluding
    # it here would silently clip every bar's true starting edge.
    ax.set_xlim(min(xmin, 0) - 3 * label_pad, max(xmax, 0) + 3 * label_pad)
    ax.set_xlabel("Mean p95 wait change, stacked_medium + stacked_high (%) "
                  "-- number at bar end = disqualifying harm cells (penalties 0 & 0.5)")

    handles = [
        Line2D([0], [0], marker="s", linestyle="none", markersize=14,
               markerfacecolor=HARM_FREE_COLOR, markeredgecolor=HARM_FREE_COLOR,
               label="Harm-free"),
        Line2D([0], [0], marker="s", linestyle="none", markersize=14,
               markerfacecolor=DISQUALIFIED_COLOR, markeredgecolor=DISQUALIFIED_COLOR,
               label="Disqualified (harmful)"),
        Line2D([0], [0], marker="*", linestyle="none", markersize=17, color="black",
               label=f"Selected (v4): {selected_pt['label']}"),
        Line2D([0], [0], marker="D", linestyle="none", markersize=10,
               markerfacecolor="white", markeredgecolor="black", markeredgewidth=2,
               label=f"Runner-up: {runner_up_pt['label']}"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=2, frameon=False,
               fontsize=11, bbox_to_anchor=(0.5, -0.1))
    fig.tight_layout(rect=(0, 0.1, 1, 1))

    save(fig, "fig0_calibration_grid")
    return dict(selected=selected_name, runner_up=runner_up_name,
                harm_free=harm_free, disqualified=disqualified)


# =============================================================== Fig 1 ==

def fig1_headline():
    main = index_by_wl_pen(read_csv("results_task6_confirmation_v4_MAIN_TABLE.csv"))

    PEN = "0.5"
    checked_pct = {wl: fnum(main[(wl, PEN)], "p95_wait_pct") for wl in WORKLOAD_ORDER}
    checked_sig = {wl: fnum(main[(wl, PEN)], "p95_wait_sign_p") < SIG_ALPHA for wl in WORKLOAD_ORDER}
    checked_harm = {wl: is_harm(main[(wl, PEN)]) for wl in WORKLOAD_ORDER}
    checked_zero = {wl: abs(checked_pct[wl]) < ZERO_TOL for wl in WORKLOAD_ORDER}

    # "the workloads where v4 helps" -- derived from the data: a
    # meaningfully negative p95_wait_pct that is ALSO statistically
    # significant (sign_p<0.05), not just a nonzero pct. (bursty_high_s64
    # has pct=-0.10% here but sign_p=1.0 -- not significant, correctly
    # excluded; it's the "neutral" workload, not a "helps" one.)
    helps = sorted([wl for wl in WORKLOAD_ORDER
                     if checked_pct[wl] < -ZERO_TOL and checked_sig[wl]],
                   key=lambda wl: checked_pct[wl])
    rest = [wl for wl in WORKLOAD_ORDER if wl not in helps]
    print(f"Fig 1: {len(helps)} workloads where {LABELS['selected_checked']} "
          f"helps at penalty={PEN}ms: {helps}")
    ordered = helps + rest
    ordered_for_plot = list(reversed(ordered))  # barh: first entry plots at the bottom

    y = list(range(len(ordered_for_plot)))
    fig, ax = plt.subplots(figsize=(9, 7.5))
    ax.barh(y, [checked_pct[wl] for wl in ordered_for_plot],
            height=0.6, color=COLOR["selected_checked"], label=LABELS["selected_checked"])

    for yy, wl in zip(y, ordered_for_plot):
        if checked_zero[wl]:
            ax.plot(0, yy, marker="o", markersize=7,
                    markerfacecolor="white", markeredgecolor=COLOR["selected_checked"],
                    markeredgewidth=1.6, zorder=6)
        if checked_sig[wl] and checked_pct[wl] < 0:
            ax.plot(checked_pct[wl], yy, marker="*", markersize=14,
                    color=COLOR["sig"], zorder=5)
        if checked_harm[wl]:
            ax.plot(checked_pct[wl], yy, marker="^", markersize=10,
                    color=COLOR["harm"], zorder=5)

    ax.axvline(0, color="black", linewidth=1.0)
    ax.set_yticks(y)
    ax.set_yticklabels(ordered_for_plot)
    ax.set_xlabel(f"p95 wait change vs baseline (%) -- penalty {PEN} ms")

    handles, labels = ax.get_legend_handles_labels()
    handles += [
        Line2D([0], [0], marker="o", linestyle="none", markersize=9,
               markerfacecolor="white", markeredgecolor=COLOR["selected_checked"],
               markeredgewidth=1.6, label="No measurable change (v4)"),
        Line2D([0], [0], marker="*", linestyle="none", markersize=14,
               color=COLOR["sig"], label=f"Statistically significant improvement (p<{SIG_ALPHA})"),
        Line2D([0], [0], marker="^", linestyle="none", markersize=10,
               markerfacecolor=COLOR["harm"], markeredgecolor=COLOR["harm"],
               label="Flagged harmful (any affected metric)"),
    ]
    labels += [h.get_label() for h in handles[-3:]]
    fig.legend(handles, labels, loc="lower center", ncol=2, frameon=False,
               fontsize=11, bbox_to_anchor=(0.5, -0.1))
    fig.tight_layout(rect=(0, 0.08, 1, 1))

    save(fig, "fig1_headline_p95wait_by_workload")
    return dict(checked_pct=checked_pct)


# =============================================================== Fig 2 ==

def fig2_stacked_high_scatter():
    rows = read_csv(f"results_task6_confirmation_v4_{FIG2_WORKLOAD}_perseed.csv")
    pts = [r for r in rows if r["penalty"] == FIG2_PENALTY and r["variant"] == "selected_checked"]
    assert pts, f"no rows found for {FIG2_WORKLOAD}/{FIG2_PENALTY}/selected_checked"

    base = [fnum(r, "baseline_p95_wait") for r in pts]
    var = [fnum(r, "variant_p95_wait") for r in pts]
    n = len(pts)
    improved = sum(1 for b, v in zip(base, var) if v < b)
    print(f"Fig 2: {FIG2_WORKLOAD}, penalty={FIG2_PENALTY}, selected_checked: "
          f"{improved} of {n} seeds improved (variant < baseline p95_wait)")

    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    lo, hi = min(base + var), max(base + var)
    pad = (hi - lo) * 0.08
    ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad],
            color=COLOR["baseline"], linestyle="--", linewidth=1.5, label="No change (y = x)")
    ax.scatter(base, var, color=COLOR["selected_checked"], s=60, zorder=5,
               label="One seed each")
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_aspect("equal")
    ax.set_xlabel("Linux baseline p95 wait (ms)")
    ax.set_ylabel("v4 p95 wait (ms)")
    ax.annotate(f"{improved} of {n} seeds improved",
                xy=(0.04, 0.94), xycoords="axes fraction",
                fontsize=13, va="top")
    ax.annotate(f"{FIG2_WORKLOAD}, migration penalty {FIG2_PENALTY} ms",
                xy=(0.04, 0.88), xycoords="axes fraction",
                fontsize=11, va="top", color="dimgray")
    fig.legend(loc="lower center", ncol=2, frameon=False, fontsize=11,
               bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(rect=(0, 0.05, 1, 1))

    save(fig, "fig2_stacked_high_perseed_scatter")
    return dict(improved=improved, n=n)


# =============================================================== Fig 3 ==

def fig3_bursty_high_s64():
    main = index_by_wl_pen(read_csv("results_task6_confirmation_v4_MAIN_TABLE.csv"))
    unchecked = index_by_wl_pen(read_csv("results_task6_confirmation_v4_TABLE_selected_unchecked.csv"))
    check_effect = index_by_wl_pen(read_csv("results_task6_confirmation_v4_CHECK_EFFECT.csv"))

    WL = FIG3_WORKLOAD
    penalties = ["0.0", "0.5", "2.0"]

    data = {}
    for pen in penalties:
        m, u = main[(WL, pen)], unchecked[(WL, pen)]
        base_mig = fnum(m, "total_migrations_base")
        base_mig_u = fnum(u, "total_migrations_base")
        assert abs(base_mig - base_mig_u) < 1e-6, (
            f"baseline total_migrations mismatch between MAIN_TABLE and "
            f"TABLE_selected_unchecked at {WL}/{pen}: {base_mig} vs {base_mig_u}")
        base_p95 = fnum(m, "p95_wait_base")
        base_p95_u = fnum(u, "p95_wait_base")
        assert abs(base_p95 - base_p95_u) < 1e-6, (
            f"baseline p95_wait mismatch between MAIN_TABLE and "
            f"TABLE_selected_unchecked at {WL}/{pen}: {base_p95} vs {base_p95_u}")

        checked_mig = fnum(m, "total_migrations_var")
        unchecked_mig = fnum(u, "total_migrations_var")
        checked_p95 = fnum(m, "p95_wait_var")
        unchecked_p95 = fnum(u, "p95_wait_var")

        # Cross-check against CHECK_EFFECT.csv (a THIRD, independent file):
        # recompute the checked-vs-unchecked % change from the two absolute
        # values above and compare against CHECK_EFFECT's own column.
        ce = check_effect[(WL, pen)]
        recomputed_pct = (checked_mig - unchecked_mig) / unchecked_mig * 100
        ce_pct = fnum(ce, "total_migrations_pct")
        # CHECK_EFFECT's sign convention: verify which side is numerator by
        # checking against both orderings before asserting a mismatch.
        alt_pct = (unchecked_mig - checked_mig) / checked_mig * 100
        ok = (abs(recomputed_pct - ce_pct) < 1e-3) or (abs(alt_pct - ce_pct) < 1e-3)
        if not ok:
            print(f"MISMATCH at {WL}/{pen}: recomputed checked-vs-unchecked "
                  f"total_migrations % ({recomputed_pct:.4f} or {alt_pct:.4f}) "
                  f"does not match CHECK_EFFECT.csv's total_migrations_pct "
                  f"({ce_pct:.4f})")
        else:
            print(f"Fig 3 cross-check OK at {WL}/{pen}: total_migrations "
                  f"checked-vs-unchecked % change matches CHECK_EFFECT.csv "
                  f"(within 1e-3)")

        data[pen] = dict(
            base_mig=base_mig, unchecked_mig=unchecked_mig, checked_mig=checked_mig,
            base_p95=base_p95, unchecked_p95=unchecked_p95, checked_p95=checked_p95,
            checked_harm=is_harm(m), unchecked_harm=is_harm(u),
            checked_harm_metrics=harmful_metrics(m), unchecked_harm_metrics=harmful_metrics(u),
        )
        print(f"Fig 3 {WL}/{pen}: migrations base={base_mig:.1f} "
              f"unchecked={unchecked_mig:.1f} checked={checked_mig:.1f} | "
              f"p95_wait base={base_p95:.2f} unchecked={unchecked_p95:.2f} "
              f"checked={checked_p95:.2f} | checked_harm={data[pen]['checked_harm']} "
              f"({data[pen]['checked_harm_metrics']}) "
              f"unchecked_harm={data[pen]['unchecked_harm']} "
              f"({data[pen]['unchecked_harm_metrics']})")

    x = range(len(penalties))
    w = 0.25
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))

    for ax, key, ylabel in [(ax1, "mig", "Total migrations (count)"),
                             (ax2, "p95", "p95 wait (ms)")]:
        base_vals = [data[p][f"base_{key}"] for p in penalties]
        unch_vals = [data[p][f"unchecked_{key}"] for p in penalties]
        chk_vals = [data[p][f"checked_{key}"] for p in penalties]
        ax.bar([xx - w for xx in x], base_vals, width=w,
               color=COLOR["baseline"], label=LABELS["baseline"])
        ax.bar(x, unch_vals, width=w,
               color=COLOR["selected_unchecked"], label=LABELS["selected_unchecked"])
        ax.bar([xx + w for xx in x], chk_vals, width=w,
               color=COLOR["selected_checked"], label=LABELS["selected_checked"])
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"{p} ms" for p in penalties])
        ax.set_xlabel("Migration penalty")
        ax.set_ylabel(ylabel)

    # Harm is reported as a text label above the affected bar, migrations
    # panel only (ax1) -- not a marker on both panels, and not on the
    # p95 panel at all, since the metrics that actually tripped harm here
    # (read from the data, see harmful_metrics()) are never p95_wait or
    # total_migrations themselves.
    mig_top = max(data[p][f"{side}_mig"] for p in penalties for side in ("base", "unchecked", "checked"))
    for xx, p in zip(x, penalties):
        for dx, side, barlabel in [(0, "unchecked", LABELS["selected_unchecked"]),
                                    (w, "checked", LABELS["selected_checked"])]:
            metrics = data[p][f"{side}_harm_metrics"]
            if metrics:
                text = "flagged harmful (" + ", ".join(pretty_metric(m) for m in metrics) + ")"
                ax1.annotate(text, xy=(xx + dx, data[p][f"{side}_mig"]),
                             xytext=(xx + dx, mig_top * 1.08),
                             ha="center", va="bottom", fontsize=10, color=COLOR["harm"],
                             arrowprops=dict(arrowstyle="-", color=COLOR["harm"], linewidth=1))
    ax1.set_ylim(top=mig_top * 1.25)

    ax1.annotate(f"{WL} ({burst_size_of(WL)}-task bursts, {MACHINE_CORES} cores)",
                 xy=(0.02, 0.98), xycoords="axes fraction", fontsize=11,
                 va="top", color="dimgray")

    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, frameon=False,
               fontsize=11, bbox_to_anchor=(0.5, 1.04))
    fig.tight_layout(rect=(0, 0, 1, 0.93))

    save(fig, "fig3_bursty_high_s64_checked_vs_unchecked")
    return data


def burst_size_of(workload):
    """Parse the burst size out of a workload NAME (e.g. "bursty_high_s64"
    -> "64") -- derived from the FIG3_WORKLOAD setting string itself, not
    a separately hardcoded number."""
    m = re.search(r"_s(\d+)$", workload)
    return m.group(1) if m else "?"


# =============================================================== Fig 4 ==

def fig4_arrival_rate():
    main = index_by_wl_pen(read_csv("results_task6_confirmation_v4_MAIN_TABLE.csv"))

    rates = [0.5, 0.75, 1.0, 1.5, 3.0]
    pen_lo, pen_hi = FIG4_PENALTIES
    linestyle = {pen_lo: "-", pen_hi: "--"}
    marker = {pen_lo: "o", pen_hi: "x"}

    key = "selected_checked"
    color = COLOR[key]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5), sharey=True)
    for ax, size in zip(axes, [4, 12]):
        for pen in FIG4_PENALTIES:
            ys, harmed = [], []
            for rate in rates:
                wl = f"rate{rate}_s{size}"
                row = main[(wl, pen)]
                ys.append(fnum(row, "p95_wait_pct"))
                harmed.append(is_harm(row))
            print(f"Fig 4 burst_size={size} {key} penalty={pen}: {ys} "
                  f"(harm={harmed})")
            ax.plot(rates, ys, linestyle=linestyle[pen], marker=marker[pen],
                    color=color, label=f"{LABELS[key]} (penalty={pen}ms)")
            for rate, yval, h in zip(rates, ys, harmed):
                if h:
                    ax.plot(rate, yval, marker="^", markersize=10,
                            color=COLOR["harm"], zorder=6)
        ax.axhline(0, color="black", linewidth=1.0)
        ax.set_xlabel("Arrival rate during burst (tasks/ms)")
        ax.annotate(f"burst size = {size}", xy=(0.04, 0.04), xycoords="axes fraction",
                    fontsize=12, va="bottom")
    axes[0].set_ylabel("p95 wait change vs baseline (%)")

    handles, labels = axes[0].get_legend_handles_labels()
    handles.append(Line2D([0], [0], marker="^", linestyle="none", markersize=10,
                           markerfacecolor=COLOR["harm"], markeredgecolor=COLOR["harm"],
                           label="Flagged harmful (any affected metric)"))
    labels.append("Flagged harmful (any affected metric)")
    fig.legend(handles, labels, loc="lower center", ncol=2, frameon=False,
               fontsize=10, bbox_to_anchor=(0.5, -0.16))
    fig.tight_layout(rect=(0, 0.12, 1, 1))

    save(fig, "fig4_p95wait_vs_arrival_rate")


# ================================================================ main ==

def main():
    r0 = fig0_calibration_grid()
    r1 = fig1_headline()
    r2 = fig2_stacked_high_scatter()
    r3 = fig3_bursty_high_s64()
    fig4_arrival_rate()

    print("\n=== verification summary ===")
    print(f"Fig 0: selected={r0['selected']}, runner_up={r0['runner_up']}, "
          f"harm_free={r0['harm_free']}, disqualified={r0['disqualified']}")
    print(f"Fig 1 selected_checked p95_wait% (penalty=0.5): {r1['checked_pct']}")
    print(f"Fig 2: {r2['improved']}/{r2['n']} seeds improved on "
          f"{FIG2_WORKLOAD}/penalty={FIG2_PENALTY}")
    print(f"Fig 3 raw data by penalty: {r3}")


if __name__ == "__main__":
    main()
