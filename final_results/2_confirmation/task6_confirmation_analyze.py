"""
Merge the 16 per-workload confirmation-run summary CSVs into the
paper's main table, plus the arrival-rate-vs-p95_wait plot (final vs
original detector, sizes 4 and 12). Report-only for the two secondary
variants (original_q2_a0.8_or, runner_up_q4_a1.5_or) -- no selection
happens here, that was already decided (2026-09-27i, see Readme.md).
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
import csv
import glob
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from paired_compare import format_p

# TASK 8 v2 RE-RUN (2026-09-29, Step 4): TASK8_V2=1 reads the _v2-
# suffixed confirmation CSVs and writes _v2-suffixed outputs. No new
# simulations either way -- this script only merges + plots.
V2_SUFFIX = "_v2" if os.environ.get("TASK8_V2") else ""

# TASK 9 v3 RE-RUN (2026-09-29, Step 4): TASK9_V3=1 reads _v3-suffixed
# CSVs, adds penalty=0.5, and uses rule (g)'s 4 variant names
# ("selected_gated" stands in for "final" as the headline comparison)
# instead of the old 3 ("final"/"original_q2_a0.8_or"/
# "runner_up_q4_a1.5_or").
V3 = bool(os.environ.get("TASK9_V3"))
V3_SUFFIX = "_v3" if V3 else ""

# TASK 10 v4 RE-RUN (2026-09-30, Step 3): TASK10_V4=1 reads _v4-suffixed
# CSVs, keeps the 3-penalty sweep, and uses Task 10's 4 variant names
# ("selected_checked" stands in for "selected_gated"/"final" as the
# headline comparison).
V4 = bool(os.environ.get("TASK10_V4"))
V4_SUFFIX = "_v4" if V4 else ""
SUFFIX = V4_SUFFIX or V3_SUFFIX or V2_SUFFIX
PENALTIES = ["0.0", "0.5", "2.0"] if (V3 or V4) else ["0.0", "2.0"]
if V4:
    HEADLINE_VARIANT = "selected_checked"
    SECONDARY_VARIANTS = ["selected_checked", "selected_unchecked",
                           "original_q2_a0.8_or_unchecked", "runner_up_checked"]
elif V3:
    HEADLINE_VARIANT = "selected_gated"
    SECONDARY_VARIANTS = ["selected_gated", "selected_ungated",
                           "original_q2_a0.8_or_ungated", "runner_up_ungated"]
else:
    HEADLINE_VARIANT = "final"
    SECONDARY_VARIANTS = ["final", "original_q2_a0.8_or", "runner_up_q4_a1.5_or"]

METRICS = ["p95_wait", "p99_wait", "avg_wait", "avg_slowdown", "p95_slowdown", "makespan_excess"]
COST_METRICS = ["sched_cores_scanned", "burst_balance_levels_walked", "total_migrations"]

WORKLOAD_ORDER = [
    "stacked_low", "stacked_medium", "stacked_high",
    "bursty_high_s24", "bursty_high_s64", "heavy_tail_high",
    "rate0.5_s4", "rate0.5_s12", "rate0.75_s4", "rate0.75_s12",
    "rate1.0_s4", "rate1.0_s12", "rate1.5_s4", "rate1.5_s12",
    "rate3.0_s4", "rate3.0_s12",
]


def load_rows():
    rows = []
    for path in glob.glob(f"results_task6_confirmation{SUFFIX}_*_summary.csv"):
        if SUFFIX == "" and ("_v2_" in path or "_v3_" in path or "_v4_" in path):
            continue  # plain mode must not also pick up v2/v3/v4 files
        rows.extend(csv.DictReader(open(path)))
    return rows


def fnum(r, k):
    v = r.get(k)
    return float(v) if v not in (None, "", "None") else None


def main():
    rows = load_rows()
    print(f"Loaded {len(rows)} summary rows from "
          f"{len(glob.glob(f'results_task6_confirmation{SUFFIX}_*_summary.csv'))} files")

    def get(workload, penalty, variant):
        for r in rows:
            if r["workload"] == workload and r["penalty"] == penalty and r["variant"] == variant:
                return r
        return None

    # ================= MAIN TABLE: baseline vs FINAL =================
    print("\n" + "=" * 130)
    print(f"MAIN TABLE -- baseline (A+B+C) vs {HEADLINE_VARIANT.upper()} burst-aware, all workloads, "
          f"{'3' if (V3 or V4) else 'both'} penalties")
    print("=" * 130)
    main_table_rows = []
    for wl in WORKLOAD_ORDER:
        for pen in PENALTIES:
            r = get(wl, pen, HEADLINE_VARIANT)
            if r is None:
                continue
            floored = r.get("p95_wait_floored") == "True"
            harm = r["any_harm"] == "True"
            flag = " [FLOORED]" if floored else ("  [HARM]" if harm else "")
            print(f"{wl:16} pen={pen:4} p95_wait {fnum(r,'p95_wait_base'):7.2f}->{fnum(r,'p95_wait_var'):7.2f} "
                  f"({fnum(r,'p95_wait_pct'):+6.1f}%) sign_p={format_p(fnum(r,'p95_wait_sign_p')):>8} "
                  f"fires={fnum(r,'detector_fires'):5.1f} recall={r['recall']:>5} precision={r['precision']:>5} "
                  f"cores_scanned={fnum(r,'sched_cores_scanned_pct'):+6.1f}% "
                  f"migrations={fnum(r,'total_migrations_pct'):+6.1f}%{flag}")
            main_table_rows.append(r)

    # write full main table CSV (every metric, every cost metric)
    if main_table_rows:
        cols = ["workload", "penalty", "any_harm", "detector_fires", "recall", "precision"]
        for m in METRICS:
            cols += [f"{m}_base", f"{m}_var", f"{m}_pct", f"{m}_sign_p", f"{m}_harm", f"{m}_floored"]
        for cm in COST_METRICS:
            cols += [f"{cm}_base", f"{cm}_var", f"{cm}_pct"]
        with open(f"results_task6_confirmation{SUFFIX}_MAIN_TABLE.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(main_table_rows)
        print(f"\nWrote results_task6_confirmation{SUFFIX}_MAIN_TABLE.csv")

    # ================= RQ4: cost vs benefit =================
    print("\n" + "=" * 130)
    print(f"RQ4 -- cost (extra scanning work) vs benefit (p95_wait change), {HEADLINE_VARIANT.upper()} only")
    print("=" * 130)
    for wl in WORKLOAD_ORDER:
        for pen in PENALTIES:
            r = get(wl, pen, HEADLINE_VARIANT)
            if r is None:
                continue
            cost_pct = fnum(r, "sched_cores_scanned_pct")
            benefit_pct = fnum(r, "p95_wait_pct")
            # burst_balance_levels_walked_base is always 0 for the baseline (it never runs the
            # burst-triggered path at all), so a %% change is undefined -- report the variant's
            # absolute count instead, which IS the meaningful "extra work" number here.
            levels_walked_abs = fnum(r, "burst_balance_levels_walked_var")
            print(f"{wl:16} pen={pen:4} extra_scanning={cost_pct:+7.1f}%  p95_wait_change={benefit_pct:+7.1f}%  "
                  f"migrations={fnum(r,'total_migrations_pct'):+7.1f}%  "
                  f"burst_levels_walked(abs)={levels_walked_abs:8.1f}")

    # ================= Secondary: all SECONDARY_VARIANTS (report only) =================
    print("\n" + "=" * 130)
    print(f"SECONDARY (report-only, no selection here) -- p95_wait %% change, "
          f"all {len(SECONDARY_VARIANTS)} burst-aware variants")
    print("=" * 130)
    VARIANT_LABELS = {"final": "final", "original_q2_a0.8_or": "original", "runner_up_q4_a1.5_or": "runner_up"}
    for wl in WORKLOAD_ORDER:
        for pen in PENALTIES:
            vals = {}
            for variant in SECONDARY_VARIANTS:
                r = get(wl, pen, variant)
                vals[variant] = (fnum(r, "p95_wait_pct"), r["any_harm"] == "True") if r else (None, None)
            if not (V3 or V4):
                print(f"{wl:16} pen={pen:4} "
                      f"final={vals['final'][0]:+7.1f}%(harm={vals['final'][1]})  "
                      f"original={vals['original_q2_a0.8_or'][0]:+7.1f}%(harm={vals['original_q2_a0.8_or'][1]})  "
                      f"runner_up={vals['runner_up_q4_a1.5_or'][0]:+7.1f}%(harm={vals['runner_up_q4_a1.5_or'][1]})")
            else:
                parts = []
                for variant in SECONDARY_VARIANTS:
                    pct, harm = vals[variant]
                    pct_str = f"{pct:+7.1f}%" if pct is not None else "    n/a"
                    parts.append(f"{variant}={pct_str}(harm={harm})")
                print(f"{wl:16} pen={pen:4} " + "  ".join(parts))

    # ================= PLOT: p95_wait %% change vs arrival rate, sizes 4 & 12 =================
    rates = [0.5, 0.75, 1.0, 1.5, 3.0]
    plot_variant_a = HEADLINE_VARIANT
    if V4:
        plot_variant_b = "original_q2_a0.8_or_unchecked"
        plot_label_a = "selected (checked)"
        plot_label_b = "original (q2_a0.8_or, unchecked)"
    elif V3:
        plot_variant_b = "original_q2_a0.8_or_ungated"
        plot_label_a = "selected (gated)"
        plot_label_b = "original (q2_a0.8_or, ungated)"
    else:
        plot_variant_b = "original_q2_a0.8_or"
        plot_label_a = "final (calibrated, AND)"
        plot_label_b = "original (q2_a0.8_or)"
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, size in zip(axes, [4, 12]):
        for variant, style in [(plot_variant_a, dict(marker="o", color="#2166ac", label=plot_label_a)),
                                (plot_variant_b, dict(marker="s", color="#b2182b", label=plot_label_b))]:
            ys_p0, ys_p2 = [], []
            pen_lo, pen_hi = PENALTIES[0], PENALTIES[-1]
            for rate in rates:
                wl = f"rate{rate}_s{size}"
                r0 = get(wl, pen_lo, variant)
                r2 = get(wl, pen_hi, variant)
                ys_p0.append(fnum(r0, "p95_wait_pct") if r0 else None)
                ys_p2.append(fnum(r2, "p95_wait_pct") if r2 else None)
            ax.plot(rates, ys_p0, linestyle="-", **style)
            style2 = dict(style)
            style2["label"] = style["label"] + " (penalty=2)"
            style2["marker"] = "x"
            ax.plot(rates, ys_p2, linestyle="--", **style2)
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.set_title(f"burst_size={size}")
        ax.set_xlabel("arrival_rate_during_burst (tasks/ms)")
    axes[0].set_ylabel("p95_wait %% change vs baseline")
    axes[0].legend(fontsize=8, loc="best")
    fig.suptitle("Burst-aware p95_wait change vs arrival rate -- "
                 + (f"{plot_label_a} vs {plot_label_b}" if (V3 or V4) else "final (calibrated) vs original detector"))
    fig.tight_layout()
    fig.savefig(f"figure_p95wait_vs_arrival_rate{SUFFIX}.png", dpi=150)
    print(f"\nWrote figure_p95wait_vs_arrival_rate{SUFFIX}.png")


if __name__ == "__main__":
    main()
