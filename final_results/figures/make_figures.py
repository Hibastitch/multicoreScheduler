"""
Presentation figures for the v4 confirmation result -- reads ONLY the
already-committed CSVs under final_results/2_confirmation/. No
simulation runs here, and nothing it reads is modified: this script is
read-only over published data, same discipline as
final_results/3_idle_check_cost/build_cost_table.py.

Saves every figure as both PNG (300 dpi) and PDF into this folder.

Consistent color roles across every figure (never reassigned):
  baseline                        -> grey (neutral, not a competing hue)
  selected_checked (v4, headline) -> strong blue
  selected_unchecked               -> a lighter shade of the same blue
  original_q2_a0.8_or_unchecked    -> amber/orange
  any_harm=True marker             -> reserved status red (never used as
                                       a 5th categorical color)

Palette chosen and validated with the dataviz skill's
scripts/validate_palette.js (categorical, light mode): the three true
hue-carrying colors (checked/unchecked/original) pass lightness band,
chroma floor, CVD separation (worst pair delta-E 22.0 protan / 24.5
tritan), normal-vision floor (delta-E 24.4), and contrast vs the
figure surface, all PASS. Baseline grey is a deliberate neutral and is
not a 4th categorical hue, so it isn't part of that categorical set.
"""

import csv
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

HERE = pathlib.Path(__file__).resolve().parent
CONF_DIR = HERE.parent / "2_confirmation"

# ---------------------------------------------------------------- style --

COLOR_BASELINE = "#8C8C8C"
COLOR_CHECKED = "#0072B2"
COLOR_UNCHECKED = "#4A90C4"
COLOR_ORIGINAL = "#B86B00"
COLOR_HARM = "#C1121F"

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

WORKLOAD_ORDER = [
    "stacked_low", "stacked_medium", "stacked_high",
    "bursty_high_s24", "bursty_high_s64", "heavy_tail_high",
    "rate0.5_s4", "rate0.5_s12", "rate0.75_s4", "rate0.75_s12",
    "rate1.0_s4", "rate1.0_s12", "rate1.5_s4", "rate1.5_s12",
    "rate3.0_s4", "rate3.0_s12",
]


def save(fig, name):
    fig.savefig(HERE / f"{name}.png", dpi=300, bbox_inches="tight")
    fig.savefig(HERE / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {name}.png and {name}.pdf")


def read_csv(name):
    path = CONF_DIR / name
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def index_by_wl_pen(rows):
    return {(r["workload"], r["penalty"]): r for r in rows}


def fnum(row, col):
    return float(row[col])


def harm_marker_legend():
    return [
        Line2D([0], [0], marker="*", linestyle="none", markersize=14,
               markerfacecolor="black", markeredgecolor="black",
               label="significant improvement (sign test p<0.05)"),
        Line2D([0], [0], marker="^", linestyle="none", markersize=10,
               markerfacecolor=COLOR_HARM, markeredgecolor=COLOR_HARM,
               label="any_harm=True (overall, across all 6 harm-checked metrics)"),
    ]


# =============================================================== Fig 1 ==

def fig1_headline():
    main = index_by_wl_pen(read_csv("results_task6_confirmation_v4_MAIN_TABLE.csv"))
    orig = index_by_wl_pen(read_csv(
        "results_task6_confirmation_v4_TABLE_original_q2_a0.8_or_unchecked.csv"))

    PEN = "0.5"
    checked_pct = {wl: fnum(main[(wl, PEN)], "p95_wait_pct") for wl in WORKLOAD_ORDER}
    checked_sig = {wl: fnum(main[(wl, PEN)], "p95_wait_sign_p") < 0.05 for wl in WORKLOAD_ORDER}
    checked_harm = {wl: main[(wl, PEN)]["any_harm"] == "True" for wl in WORKLOAD_ORDER}
    orig_pct = {wl: fnum(orig[(wl, PEN)], "p95_wait_pct") for wl in WORKLOAD_ORDER}
    orig_sig = {wl: fnum(orig[(wl, PEN)], "p95_wait_sign_p") < 0.05 for wl in WORKLOAD_ORDER}
    orig_harm = {wl: orig[(wl, PEN)]["any_harm"] == "True" for wl in WORKLOAD_ORDER}

    # "the 4 where v4 helps" -- derived from the data: a meaningfully
    # negative p95_wait_pct that is ALSO statistically significant
    # (sign_p<0.05), not just a nonzero pct. (bursty_high_s64 has
    # pct=-0.10% here but sign_p=1.0 -- not significant, correctly
    # excluded; it's the "neutral" workload, not a "helps" one.)
    helps = sorted([wl for wl in WORKLOAD_ORDER
                     if checked_pct[wl] < -1e-6 and checked_sig[wl]],
                   key=lambda wl: checked_pct[wl])
    rest = [wl for wl in WORKLOAD_ORDER if wl not in helps]
    print(f"Fig 1: {len(helps)} workloads where selected_checked helps at "
          f"penalty={PEN}ms: {helps}")
    ordered = helps + rest
    ordered_for_plot = list(reversed(ordered))  # barh: first entry plots at the bottom

    y = list(range(len(ordered_for_plot)))
    fig, ax = plt.subplots(figsize=(9, 7.5))
    h = 0.35
    ax.barh([yy + h / 2 for yy in y], [checked_pct[wl] for wl in ordered_for_plot],
            height=h, color=COLOR_CHECKED, label="selected_checked (v4)")
    ax.barh([yy - h / 2 for yy in y], [orig_pct[wl] for wl in ordered_for_plot],
            height=h, color=COLOR_ORIGINAL, label="original_q2_a0.8_or_unchecked")

    for yy, wl in zip(y, ordered_for_plot):
        if checked_sig[wl] and checked_pct[wl] < 0:
            ax.plot(checked_pct[wl], yy + h / 2, marker="*", markersize=14,
                    color="black", zorder=5)
        if checked_harm[wl]:
            ax.plot(checked_pct[wl], yy + h / 2, marker="^", markersize=10,
                    color=COLOR_HARM, zorder=5)
        if orig_sig[wl] and orig_pct[wl] < 0:
            ax.plot(orig_pct[wl], yy - h / 2, marker="*", markersize=14,
                    color="black", zorder=5)
        if orig_harm[wl]:
            ax.plot(orig_pct[wl], yy - h / 2, marker="^", markersize=10,
                    color=COLOR_HARM, zorder=5)

    ax.axvline(0, color="black", linewidth=1.0)
    ax.set_yticks(y)
    ax.set_yticklabels(ordered_for_plot)
    ax.set_xlabel("p95_wait % change vs baseline (penalty=0.5ms)")

    handles, labels = ax.get_legend_handles_labels()
    handles += harm_marker_legend()
    labels += [h.get_label() for h in harm_marker_legend()]
    fig.legend(handles, labels, loc="lower center", ncol=2, frameon=False,
               fontsize=11, bbox_to_anchor=(0.5, -0.08))
    fig.tight_layout(rect=(0, 0.06, 1, 1))

    save(fig, "fig1_headline_p95wait_by_workload")
    return dict(checked_pct=checked_pct, orig_pct=orig_pct)


# =============================================================== Fig 2 ==

def fig2_stacked_high_scatter():
    rows = read_csv("results_task6_confirmation_v4_stacked_high_perseed.csv")
    pts = [r for r in rows if r["penalty"] == "0.5" and r["variant"] == "selected_checked"]
    assert len(pts) == 30, f"expected 30 seeds, got {len(pts)}"

    base = [fnum(r, "baseline_p95_wait") for r in pts]
    var = [fnum(r, "variant_p95_wait") for r in pts]
    improved = sum(1 for b, v in zip(base, var) if v < b)
    print(f"Fig 2: stacked_high, penalty=0.5, selected_checked: "
          f"{improved} of {len(pts)} seeds improved (variant < baseline p95_wait)")

    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    lo, hi = min(base + var), max(base + var)
    pad = (hi - lo) * 0.08
    ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad],
            color=COLOR_BASELINE, linestyle="--", linewidth=1.5, label="y = x (no change)")
    ax.scatter(base, var, color=COLOR_CHECKED, s=60, zorder=5,
               label="selected_checked (one point per seed)")
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_aspect("equal")
    ax.set_xlabel("baseline p95_wait (ms)")
    ax.set_ylabel("selected_checked p95_wait (ms)")
    ax.annotate(f"{improved} of {len(pts)} seeds improved",
                xy=(0.04, 0.94), xycoords="axes fraction",
                fontsize=13, va="top")
    fig.legend(loc="lower center", ncol=2, frameon=False, fontsize=11,
               bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(rect=(0, 0.05, 1, 1))

    save(fig, "fig2_stacked_high_perseed_scatter")
    return dict(improved=improved, n=len(pts))


# =============================================================== Fig 3 ==

def fig3_bursty_high_s64():
    main = index_by_wl_pen(read_csv("results_task6_confirmation_v4_MAIN_TABLE.csv"))
    unchecked = index_by_wl_pen(read_csv("results_task6_confirmation_v4_TABLE_selected_unchecked.csv"))
    check_effect = index_by_wl_pen(read_csv("results_task6_confirmation_v4_CHECK_EFFECT.csv"))

    WL = "bursty_high_s64"
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
            checked_harm=m["any_harm"] == "True",
            unchecked_harm=u["any_harm"] == "True",
        )
        print(f"Fig 3 {WL}/{pen}: migrations base={base_mig:.1f} "
              f"unchecked={unchecked_mig:.1f} checked={checked_mig:.1f} | "
              f"p95_wait base={base_p95:.2f} unchecked={unchecked_p95:.2f} "
              f"checked={checked_p95:.2f} | checked_harm={data[pen]['checked_harm']} "
              f"unchecked_harm={data[pen]['unchecked_harm']}")

    x = range(len(penalties))
    w = 0.25
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))

    for ax, key, ylabel in [(ax1, "mig", "total_migrations (count)"),
                             (ax2, "p95", "p95_wait (ms)")]:
        base_vals = [data[p][f"base_{key}"] for p in penalties]
        unch_vals = [data[p][f"unchecked_{key}"] for p in penalties]
        chk_vals = [data[p][f"checked_{key}"] for p in penalties]
        ax.bar([xx - w for xx in x], base_vals, width=w, color=COLOR_BASELINE, label="baseline")
        ax.bar(x, unch_vals, width=w, color=COLOR_UNCHECKED, label="selected_unchecked")
        ax.bar([xx + w for xx in x], chk_vals, width=w, color=COLOR_CHECKED, label="selected_checked")
        for xx, p in zip(x, penalties):
            if data[p]["unchecked_harm"]:
                ax.plot(xx, data[p][f"unchecked_{key}"], marker="^", markersize=10,
                        color=COLOR_HARM, zorder=5)
            if data[p]["checked_harm"]:
                ax.plot(xx + w, data[p][f"checked_{key}"], marker="^", markersize=10,
                        color=COLOR_HARM, zorder=5)
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"{p} ms" for p in penalties])
        ax.set_xlabel("migration_penalty")
        ax.set_ylabel(ylabel)

    handles, labels = ax1.get_legend_handles_labels()
    handles.append(Line2D([0], [0], marker="^", linestyle="none", markersize=10,
                           markerfacecolor=COLOR_HARM, markeredgecolor=COLOR_HARM,
                           label="any_harm=True (overall)"))
    labels.append("any_harm=True (overall)")
    fig.legend(handles, labels, loc="upper center", ncol=4, frameon=False,
               fontsize=11, bbox_to_anchor=(0.5, 1.06))
    fig.tight_layout(rect=(0, 0, 1, 0.94))

    save(fig, "fig3_bursty_high_s64_checked_vs_unchecked")
    return data


# =============================================================== Fig 4 ==

def fig4_arrival_rate():
    main = index_by_wl_pen(read_csv("results_task6_confirmation_v4_MAIN_TABLE.csv"))
    orig = index_by_wl_pen(read_csv(
        "results_task6_confirmation_v4_TABLE_original_q2_a0.8_or_unchecked.csv"))

    rates = [0.5, 0.75, 1.0, 1.5, 3.0]
    pen_lo, pen_hi = "0.0", "2.0"

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for ax, size in zip(axes, [4, 12]):
        for table, color, label in [(main, COLOR_CHECKED, "selected_checked"),
                                     (orig, COLOR_ORIGINAL, "original_q2_a0.8_or_unchecked")]:
            ys_lo, ys_hi = [], []
            for rate in rates:
                wl = f"rate{rate}_s{size}"
                ys_lo.append(fnum(table[(wl, pen_lo)], "p95_wait_pct"))
                ys_hi.append(fnum(table[(wl, pen_hi)], "p95_wait_pct"))
            print(f"Fig 4 burst_size={size} {label}: "
                  f"penalty={pen_lo} -> {ys_lo}; penalty={pen_hi} -> {ys_hi}")
            ax.plot(rates, ys_lo, linestyle="-", marker="o", color=color,
                    label=f"{label} (penalty={pen_lo}ms)")
            ax.plot(rates, ys_hi, linestyle="--", marker="x", color=color,
                    label=f"{label} (penalty={pen_hi}ms)")
        ax.axhline(0, color="black", linewidth=1.0)
        ax.set_xlabel("arrival_rate_during_burst (tasks/ms)")
        ax.annotate(f"burst_size={size}", xy=(0.04, 0.04), xycoords="axes fraction",
                    fontsize=12, va="bottom")
    axes[0].set_ylabel("p95_wait % change vs baseline")
    axes[1].legend(loc="best", frameon=False, fontsize=10)
    fig.tight_layout()

    save(fig, "fig4_p95wait_vs_arrival_rate")


# ================================================================ main ==

def main():
    r1 = fig1_headline()
    r2 = fig2_stacked_high_scatter()
    r3 = fig3_bursty_high_s64()
    fig4_arrival_rate()

    print("\n=== verification summary ===")
    print(f"Fig 1 selected_checked p95_wait% (penalty=0.5): {r1['checked_pct']}")
    print(f"Fig 2: {r2['improved']}/{r2['n']} seeds improved on stacked_high/penalty=0.5")
    print(f"Fig 3 raw data by penalty: {r3}")


if __name__ == "__main__":
    main()
