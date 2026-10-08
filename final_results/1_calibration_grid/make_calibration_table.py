"""
Calibration-grid results table for the paper -- replaces Fig 0
(fig0_calibration_grid.png/.pdf, kept in the repo as-is) with a table
form of the exact same result, for the paper's text. No new
simulations, no recomputation of the harm rule: every number here is
read straight from already-committed v4 outputs --

  - results_task6_threshold_grid_v4_<workload>_summary.csv (9 files,
    one per calibration workload): each config's p95_wait_pct at
    penalty 0.0 and 0.5, averaged per cell.
  - tradeoff_points_v4.csv (written by task6_threshold_grid_tradeoff.py):
    harm_count/harmed_workloads per config (the authoritative,
    corrected-rule, penalty-0/0.5-only disqualification already used by
    Fig 0), plus the same 4 columns Fig 0 averages for its x-axis.
  - selected_config_v4.json: which config is selected/runner-up.
  - harm_breakdown_v4.csv (written by task6_threshold_grid_harm_breakdown.py):
    per-metric detail for every harmful cell, at every penalty tested
    (0, 0.5, 2ms) -- filtered here to penalty in {0.0, 0.5}, the only
    ones that disqualify a config (2ms is measured/reported elsewhere
    but never disqualifies, see docs/PIPELINE.md's Harm rule).

Cross-checked, not just copied: the config numbering (#1-12) is
verified against the config name strings themselves (not hand-typed),
and the disqualifying-harm set independently derived from
harm_breakdown_v4.csv is checked against tradeoff_points_v4.csv's own
harmed_workloads column before either is used to mark a cell.
"""

import csv
import json
import os
import pathlib
import sys

# Config labels use "≥" -- force UTF-8 stdout regardless of the console's
# codepage (Windows cp1252 can't encode it, raising UnicodeEncodeError
# on print() otherwise). Output files are opened with explicit utf-8
# below regardless of this.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = pathlib.Path(__file__).resolve().parent

# Same guard as every other v4 pipeline script (docs/PIPELINE.md): this
# reads v4-suffixed files specifically, refuse rather than silently
# picking up stale v1/v2/v3 ones.
if not os.environ.get("TASK10_V4"):
    raise SystemExit(
        "ERROR: TASK10_V4 is not set -- refusing to run. This script reads "
        "the v4-suffixed grid CSVs (results_task6_threshold_grid_v4_*, "
        "tradeoff_points_v4.csv, selected_config_v4.json, "
        "harm_breakdown_v4.csv); set TASK10_V4=1 before running it, matching "
        "every other script in the v4 pipeline (see docs/PIPELINE.md)."
    )

# The 9 calibration-grid workloads (docs/PIPELINE.md's Step 1), in the
# column order the paper wants, each paired with its paper-facing name.
WORKLOAD_COLUMNS = [
    ("stacked_low", "stacked low"),
    ("stacked_medium", "stacked medium"),
    ("stacked_high", "stacked high"),
    ("bursty_high_s24", "bursty s24"),
    ("bursty_high_s64", "bursty s64"),
    ("rate0.5_s4", "rate sweep s4 @0.5"),
    ("rate0.5_s12", "rate sweep s12 @0.5"),
    ("rate3.0_s4", "rate sweep s4 @3.0"),
    ("rate3.0_s12", "rate sweep s12 @3.0"),
]

# Penalties that count toward disqualification (docs/PIPELINE.md's Harm
# rule, same RULE_PENALTIES as task6_threshold_grid_recompute_harm.py):
# 2ms is measured and reported but never disqualifies a config.
DISQUALIFYING_PENALTIES = {"0.0", "0.5"}

# Expected #1-12 order, given in the task -- verified below against the
# actual config name strings (q{q}_a{a}_{and|or}) rather than trusted
# blindly: sorting those strings alphabetically groups by q (2<4<8),
# then by rate ("0.8"<"1.5"), then "and"<"or", which is exactly this.
EXPECTED_ORDER = [
    "q2_a0.8_and", "q2_a0.8_or", "q2_a1.5_and", "q2_a1.5_or",
    "q4_a0.8_and", "q4_a0.8_or", "q4_a1.5_and", "q4_a1.5_or",
    "q8_a0.8_and", "q8_a0.8_or", "q8_a1.5_and", "q8_a1.5_or",
]


def read_csv(name):
    with open(HERE / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def config_label(cfg_row):
    combine_word = "OR" if cfg_row["combine"] == "or" else "AND"
    return f"q≥{int(float(cfg_row['q']))} {combine_word} rate≥{cfg_row['a']}"


def main():
    # ---------------------------------------------------------- tradeoff ==
    tradeoff_rows = read_csv("tradeoff_points_v4.csv")
    tradeoff_by_cfg = {r["config"]: r for r in tradeoff_rows}

    actual_configs = sorted(tradeoff_by_cfg)
    assert actual_configs == EXPECTED_ORDER, (
        f"config numbering mismatch: sorted config names from "
        f"tradeoff_points_v4.csv are {actual_configs}, expected {EXPECTED_ORDER}"
    )
    config_number = {cfg: i + 1 for i, cfg in enumerate(EXPECTED_ORDER)}
    print(f"Verified #1-12 order against tradeoff_points_v4.csv's own config "
          f"names: {EXPECTED_ORDER}")

    sel = json.loads((HERE / "selected_config_v4.json").read_text(encoding="utf-8"))
    selected_name = sel["selected"]["config"]
    runner_up_name = sel["runner_up"]["config"]
    harm_free_configs = set(sel["harm_free_configs"])

    P95_COLS = ["stacked_medium_p0_0", "stacked_medium_p0_5",
                "stacked_high_p0_0", "stacked_high_p0_5"]

    def selection_score(cfg):
        r = tradeoff_by_cfg[cfg]
        return sum(float(r[c]) for c in P95_COLS) / len(P95_COLS)

    sel_score = selection_score(selected_name)
    run_score = selection_score(runner_up_name)
    assert abs(sel_score - sel["selected_mean_p95_pct"]) < 1e-6, (
        f"selected ({selected_name}) selection score {sel_score:.6f} != "
        f"selected_config_v4.json's selected_mean_p95_pct "
        f"{sel['selected_mean_p95_pct']:.6f}")
    assert abs(run_score - sel["runner_up_mean_p95_pct"]) < 1e-6, (
        f"runner-up ({runner_up_name}) selection score {run_score:.6f} != "
        f"selected_config_v4.json's runner_up_mean_p95_pct "
        f"{sel['runner_up_mean_p95_pct']:.6f}")
    assert round(sel_score, 2) == -16.91, f"selected score rounds to {round(sel_score, 2)}, expected -16.91"
    assert round(run_score, 2) == -16.87, f"runner-up score rounds to {round(run_score, 2)}, expected -16.87"
    print(f"Verified selection score: #{config_number[selected_name]} {config_label(tradeoff_by_cfg[selected_name])} "
          f"(selected) = {sel_score:.2f}, matches selected_config_v4.json and the task's -16.91")
    print(f"Verified selection score: #{config_number[runner_up_name]} {config_label(tradeoff_by_cfg[runner_up_name])} "
          f"(runner-up) = {run_score:.2f}, matches selected_config_v4.json and the task's -16.87")

    # harmed_workloads per config, from tradeoff_points_v4.csv (the
    # authoritative, already-corrected-rule source Fig 0 itself reads).
    harmed_workloads_by_cfg = {
        cfg: set(w for w in tradeoff_by_cfg[cfg]["harmed_workloads"].split(";") if w)
        for cfg in EXPECTED_ORDER
    }

    # ------------------------------------------------ per-workload p95 cells ==
    cell_pct = {}  # (config, workload) -> mean p95_wait_pct over penalty 0/0.5
    for wl, _ in WORKLOAD_COLUMNS:
        rows = read_csv(f"results_task6_threshold_grid_v4_{wl}_summary.csv")
        by_cfg_pen = {(r["config"], r["penalty"]): float(r["p95_wait_pct"]) for r in rows}
        for cfg in EXPECTED_ORDER:
            vals = [by_cfg_pen[(cfg, pen)] for pen in ("0.0", "0.5")]
            assert len(vals) == 2, f"expected penalty 0.0 and 0.5 rows for {cfg}/{wl}, found {len(vals)}"
            cell_pct[(cfg, wl)] = sum(vals) / 2

    # ------------------------------------------- disqualifying harm detail ==
    harm_breakdown_rows = read_csv("harm_breakdown_v4.csv")
    harm_detail = [
        r for r in harm_breakdown_rows if r["penalty"] in DISQUALIFYING_PENALTIES
    ]
    # Cross-check: the (config, workload) pairs found here must be exactly
    # the same set tradeoff_points_v4.csv's own harmed_workloads lists --
    # two independently-written files, verified to agree before either is
    # trusted, same discipline as make_figures.py's Fig 0 cross-checks.
    from_breakdown = set((r["config"], r["workload"]) for r in harm_detail)
    from_tradeoff = set(
        (cfg, wl) for cfg in EXPECTED_ORDER for wl in harmed_workloads_by_cfg[cfg]
    )
    if from_breakdown != from_tradeoff:
        only_breakdown = from_breakdown - from_tradeoff
        only_tradeoff = from_tradeoff - from_breakdown
        raise AssertionError(
            f"harm_breakdown_v4.csv (penalty 0/0.5 only) and tradeoff_points_v4.csv's "
            f"harmed_workloads disagree on which (config, workload) pairs are "
            f"disqualifying -- only in breakdown: {only_breakdown}, only in tradeoff: {only_tradeoff}")
    print(f"Cross-check OK: harm_breakdown_v4.csv (penalty 0/0.5 rows) and "
          f"tradeoff_points_v4.csv's harmed_workloads agree exactly on "
          f"{len(from_tradeoff)} disqualifying (config, workload) pairs.")

    wl_display = dict(WORKLOAD_COLUMNS)

    def status_of(cfg):
        base = "harm-free" if cfg in harm_free_configs else "disqualified"
        if cfg == selected_name:
            return f"{base} (SELECTED)"
        if cfg == runner_up_name:
            return f"{base} (runner-up)"
        return base

    # ---------------------------------------------------------- build rows ==
    table_rows = []
    for cfg in EXPECTED_ORDER:
        row = {
            "#": config_number[cfg],
            "config": config_label(tradeoff_by_cfg[cfg]),
        }
        for wl, disp in WORKLOAD_COLUMNS:
            pct = cell_pct[(cfg, wl)]
            marker = "†" if wl in harmed_workloads_by_cfg[cfg] else ""
            row[disp] = f"{pct:+.1f}{marker}"
        row["selection score"] = f"{selection_score(cfg):+.2f}"
        row["status"] = status_of(cfg)
        table_rows.append(row)

    columns = ["#", "config"] + [disp for _, disp in WORKLOAD_COLUMNS] + ["selection score", "status"]

    # -------------------------------------------------------------- write ==
    csv_path = HERE / "calibration_table_v4.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(table_rows)
    print(f"Wrote {csv_path.name}")

    tsv_path = HERE / "calibration_table_v4.tsv"
    with open(tsv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, delimiter="\t")
        w.writeheader()
        w.writerows(table_rows)
    print(f"Wrote {tsv_path.name}")

    md_path = HERE / "calibration_table_v4.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Calibration grid results (table form of Fig 0)\n\n")
        f.write(
            "p95 wait % change vs baseline, mean over migration cost 0 and "
            "0.5 ms, per (config, calibration workload). "
            "† marks a cell with disqualifying harm (any of p95_wait/"
            "avg_wait/avg_slowdown, corrected rule, penalty 0 or 0.5 ms only "
            "-- see calibration_harm_detail_v4.csv). \"selection score\" "
            "is the mean of stacked medium + stacked high at penalty 0 and "
            "0.5 ms, the quantity the selection rule ranks configs by.\n\n"
        )
        f.write("| " + " | ".join(columns) + " |\n")
        f.write("|" + "|".join(["---"] * len(columns)) + "|\n")
        for row in table_rows:
            f.write("| " + " | ".join(str(row[c]) for c in columns) + " |\n")
    print(f"Wrote {md_path.name}")

    detail_columns = ["config", "workload", "migration cost", "metric",
                       "% change", "wins", "losses", "sign p"]
    detail_rows = []
    for r in sorted(harm_detail, key=lambda r: (config_number[r["config"]], r["workload"])):
        cfg = r["config"]
        detail_rows.append({
            "config": f"#{config_number[cfg]} {config_label(tradeoff_by_cfg[cfg])}",
            "workload": wl_display.get(r["workload"], r["workload"]),
            "migration cost": f"{float(r['penalty']):.1f} ms",
            "metric": r["metric"].replace("_", " "),
            "% change": f"{float(r['pct']):+.3f}",
            "wins": r["wins"],
            "losses": r["harms"],
            "sign p": r["sign_p"],
        })
    detail_path = HERE / "calibration_harm_detail_v4.csv"
    with open(detail_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=detail_columns)
        w.writeheader()
        w.writerows(detail_rows)
    print(f"Wrote {detail_path.name} ({len(detail_rows)} rows)")

    # -------------------------------------------------------------- print ==
    print("\n=== calibration_table_v4 ===")
    widths = {c: max(len(c), max(len(str(row[c])) for row in table_rows)) for c in columns}
    header = " | ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("-" * len(header))
    for row in table_rows:
        print(" | ".join(str(row[c]).ljust(widths[c]) for c in columns))

    print("\n=== disqualifying harm detail (calibration_harm_detail_v4.csv) ===")
    if not detail_rows:
        print("(none)")
    else:
        dwidths = {c: max(len(c), max(len(str(r[c])) for r in detail_rows)) for c in detail_columns}
        dheader = " | ".join(c.ljust(dwidths[c]) for c in detail_columns)
        print(dheader)
        print("-" * len(dheader))
        for r in detail_rows:
            print(" | ".join(str(r[c]).ljust(dwidths[c]) for c in detail_columns))


if __name__ == "__main__":
    main()
