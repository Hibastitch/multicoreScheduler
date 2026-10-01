"""
Presentation figures for the v4 confirmation result -- reads ONLY the
already-committed CSVs under final_results/2_confirmation/. No
simulation runs here, and nothing it reads is modified: this script is
read-only over published data, same discipline as
final_results/3_idle_check_cost/build_cost_table.py.

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
import os
import pathlib
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

HERE = pathlib.Path(__file__).resolve().parent
CONF_DIR = HERE.parent / "2_confirmation"

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
    "original_q2_a0.8_or_unchecked": "Original detector, no check",
}

COLOR = {
    "baseline": "#8C8C8C",
    "selected_checked": "#0072B2",
    "selected_unchecked": "#4A90C4",
    "original_q2_a0.8_or_unchecked": "#B86B00",
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


# =============================================================== Fig 1 ==

def fig1_headline():
    main = index_by_wl_pen(read_csv("results_task6_confirmation_v4_MAIN_TABLE.csv"))
    orig = index_by_wl_pen(read_csv(
        "results_task6_confirmation_v4_TABLE_original_q2_a0.8_or_unchecked.csv"))

    PEN = "0.5"
    checked_pct = {wl: fnum(main[(wl, PEN)], "p95_wait_pct") for wl in WORKLOAD_ORDER}
    checked_sig = {wl: fnum(main[(wl, PEN)], "p95_wait_sign_p") < SIG_ALPHA for wl in WORKLOAD_ORDER}
    checked_harm = {wl: is_harm(main[(wl, PEN)]) for wl in WORKLOAD_ORDER}
    checked_zero = {wl: abs(checked_pct[wl]) < ZERO_TOL for wl in WORKLOAD_ORDER}
    orig_pct = {wl: fnum(orig[(wl, PEN)], "p95_wait_pct") for wl in WORKLOAD_ORDER}
    orig_sig = {wl: fnum(orig[(wl, PEN)], "p95_wait_sign_p") < SIG_ALPHA for wl in WORKLOAD_ORDER}
    orig_harm = {wl: is_harm(orig[(wl, PEN)]) for wl in WORKLOAD_ORDER}

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
    h = 0.35
    ax.barh([yy + h / 2 for yy in y], [checked_pct[wl] for wl in ordered_for_plot],
            height=h, color=COLOR["selected_checked"], label=LABELS["selected_checked"])
    ax.barh([yy - h / 2 for yy in y], [orig_pct[wl] for wl in ordered_for_plot],
            height=h, color=COLOR["original_q2_a0.8_or_unchecked"],
            label=LABELS["original_q2_a0.8_or_unchecked"])

    for yy, wl in zip(y, ordered_for_plot):
        if checked_zero[wl]:
            ax.plot(0, yy + h / 2, marker="o", markersize=7,
                    markerfacecolor="white", markeredgecolor=COLOR["selected_checked"],
                    markeredgewidth=1.6, zorder=6)
        if checked_sig[wl] and checked_pct[wl] < 0:
            ax.plot(checked_pct[wl], yy + h / 2, marker="*", markersize=14,
                    color=COLOR["sig"], zorder=5)
        if checked_harm[wl]:
            ax.plot(checked_pct[wl], yy + h / 2, marker="^", markersize=10,
                    color=COLOR["harm"], zorder=5)
        if orig_sig[wl] and orig_pct[wl] < 0:
            ax.plot(orig_pct[wl], yy - h / 2, marker="*", markersize=14,
                    color=COLOR["sig"], zorder=5)
        if orig_harm[wl]:
            ax.plot(orig_pct[wl], yy - h / 2, marker="^", markersize=10,
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
    return dict(checked_pct=checked_pct, orig_pct=orig_pct)


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
    orig = index_by_wl_pen(read_csv(
        "results_task6_confirmation_v4_TABLE_original_q2_a0.8_or_unchecked.csv"))

    rates = [0.5, 0.75, 1.0, 1.5, 3.0]
    pen_lo, pen_hi = FIG4_PENALTIES
    linestyle = {pen_lo: "-", pen_hi: "--"}
    marker = {pen_lo: "o", pen_hi: "x"}

    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5), sharey=True)
    for ax, size in zip(axes, [4, 12]):
        for key, table in [("selected_checked", main),
                            ("original_q2_a0.8_or_unchecked", orig)]:
            color = COLOR[key]
            for pen in FIG4_PENALTIES:
                ys, harmed = [], []
                for rate in rates:
                    wl = f"rate{rate}_s{size}"
                    row = table[(wl, pen)]
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
    r1 = fig1_headline()
    r2 = fig2_stacked_high_scatter()
    r3 = fig3_bursty_high_s64()
    fig4_arrival_rate()

    print("\n=== verification summary ===")
    print(f"Fig 1 selected_checked p95_wait% (penalty=0.5): {r1['checked_pct']}")
    print(f"Fig 2: {r2['improved']}/{r2['n']} seeds improved on "
          f"{FIG2_WORKLOAD}/penalty={FIG2_PENALTY}")
    print(f"Fig 3 raw data by penalty: {r3}")


if __name__ == "__main__":
    main()
