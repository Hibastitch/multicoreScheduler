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
    for path in glob.glob(f"results_task6_confirmation{V2_SUFFIX}_*_summary.csv"):
        if V2_SUFFIX == "" and "_v2_" in path:
            continue  # non-v2 mode must not also pick up v2 files
        rows.extend(csv.DictReader(open(path)))
    return rows


def fnum(r, k):
    v = r.get(k)
    return float(v) if v not in (None, "", "None") else None


def main():
    rows = load_rows()
    print(f"Loaded {len(rows)} summary rows from "
          f"{len(glob.glob(f'results_task6_confirmation{V2_SUFFIX}_*_summary.csv'))} files")

    def get(workload, penalty, variant):
        for r in rows:
            if r["workload"] == workload and r["penalty"] == penalty and r["variant"] == variant:
                return r
        return None

    # ================= MAIN TABLE: baseline vs FINAL =================
    print("\n" + "=" * 130)
    print("MAIN TABLE -- baseline (A+B+C) vs FINAL burst-aware, all workloads, both penalties")
    print("=" * 130)
    main_table_rows = []
    for wl in WORKLOAD_ORDER:
        for pen in ["0.0", "2.0"]:
            r = get(wl, pen, "final")
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
        with open(f"results_task6_confirmation{V2_SUFFIX}_MAIN_TABLE.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(main_table_rows)
        print(f"\nWrote results_task6_confirmation{V2_SUFFIX}_MAIN_TABLE.csv")

    # ================= RQ4: cost vs benefit =================
    print("\n" + "=" * 130)
    print("RQ4 -- cost (extra scanning work) vs benefit (p95_wait change), FINAL only")
    print("=" * 130)
    for wl in WORKLOAD_ORDER:
        for pen in ["0.0", "2.0"]:
            r = get(wl, pen, "final")
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

    # ================= Secondary: original vs runner-up vs final (report only) =================
    print("\n" + "=" * 130)
    print("SECONDARY (report-only, no selection here) -- p95_wait %% change, all 3 burst-aware variants")
    print("=" * 130)
    for wl in WORKLOAD_ORDER:
        for pen in ["0.0", "2.0"]:
            vals = {}
            for variant in ["final", "original_q2_a0.8_or", "runner_up_q4_a1.5_or"]:
                r = get(wl, pen, variant)
                vals[variant] = (fnum(r, "p95_wait_pct"), r["any_harm"] == "True") if r else (None, None)
            print(f"{wl:16} pen={pen:4} "
                  f"final={vals['final'][0]:+7.1f}%(harm={vals['final'][1]})  "
                  f"original={vals['original_q2_a0.8_or'][0]:+7.1f}%(harm={vals['original_q2_a0.8_or'][1]})  "
                  f"runner_up={vals['runner_up_q4_a1.5_or'][0]:+7.1f}%(harm={vals['runner_up_q4_a1.5_or'][1]})")

    # ================= PLOT: p95_wait %% change vs arrival rate, sizes 4 & 12, final vs original =================
    rates = [0.5, 0.75, 1.0, 1.5, 3.0]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, size in zip(axes, [4, 12]):
        for variant, style in [("final", dict(marker="o", color="#2166ac", label="final (calibrated, AND)")),
                                ("original_q2_a0.8_or", dict(marker="s", color="#b2182b", label="original (q2_a0.8_or)"))]:
            ys_p0, ys_p2 = [], []
            for rate in rates:
                wl = f"rate{rate}_s{size}"
                r0 = get(wl, "0.0", variant)
                r2 = get(wl, "2.0", variant)
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
    fig.suptitle("Burst-aware p95_wait change vs arrival rate -- final (calibrated) vs original detector")
    fig.tight_layout()
    fig.savefig(f"figure_p95wait_vs_arrival_rate{V2_SUFFIX}.png", dpi=150)
    print(f"\nWrote figure_p95wait_vs_arrival_rate{V2_SUFFIX}.png")


if __name__ == "__main__":
    main()
