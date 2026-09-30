"""
Merges results_idle_check_cost.csv (this folder) with
../2_confirmation/results_task6_confirmation_v4_MAIN_TABLE.csv into
one per-(workload, penalty) cost/benefit table covering all 48
confirmation cells -- extra balancer scanning %, idle-check reads as
% of baseline scanning, total_migrations %, p95_wait %. No new
simulations; reads only committed CSVs.

Idle-check %% is 0 for a cell ONLY when detector_fires=0.0 exactly in
that cell (proven: the idle check can't run if the detector never
triggers -- see BurstScheduler.on_task_placed()). It is NOT assumed 0
just because a workload is one of the "11 silent workloads" from the
2026-09-30g headline (that's about p95_wait effect, not about whether
the idle check ran) -- bursty_high_s24 was the one case where this
mattered: its penalty=0.0 cell has detector_fires=0.0 exactly (->
proven 0%), but its penalty=0.5/2.0 cells have tiny nonzero fires
(0.067/0.033 per run). It was added to measure_idle_check_cost.py's
WORKLOADS dict on 2026-10-01c specifically to measure those two cells
directly rather than leave them n/a -- both came back ~0.0022% (not
exactly 0: 1/30 seeds at each penalty actually ran the idle-check scan
and read all 32 cores without finding one idle, same saturated-machine
pattern as bursty_high_s64). As of 2026-10-01c every one of the 48
cells is either "measured" or "proven" zero; none are n/a any more.
"""

import csv
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
CONF_DIR = HERE.parent / "2_confirmation"

MAIN_TABLE = CONF_DIR / "results_task6_confirmation_v4_MAIN_TABLE.csv"
IDLE_COST = HERE / "results_idle_check_cost.csv"


def load_idle_cost():
    rows = {}
    with open(IDLE_COST, newline="") as f:
        for r in csv.DictReader(f):
            rows[(r["workload"], r["penalty"])] = r
    return rows


def main():
    idle_cost = load_idle_cost()

    with open(MAIN_TABLE, newline="") as f:
        main_rows = list(csv.DictReader(f))

    out_rows = []
    for r in main_rows:
        key = (r["workload"], r["penalty"])
        fires = float(r["detector_fires"])
        idle_row = idle_cost.get(key)

        if idle_row is not None:
            idle_pct = float(idle_row["idle_check_pct_of_baseline_scanned"])
            # 4 decimals, not 2: bursty_high_s24's two rare-fire cells (penalty
            # 0.5/2.0) round to 0.00 at 2dp even though they're measured and
            # nonzero (~0.0022%) -- keep that visible rather than collapsing
            # it to the same "0.00" used for the proven-exact-zero cells.
            idle_pct_str = f"{idle_pct:.4f}"
            idle_note = "measured"
        elif fires == 0.0:
            idle_pct = 0.0
            idle_pct_str = "0.0000"
            idle_note = "proven (detector_fires=0.0)"
        else:
            idle_pct = None
            idle_pct_str = "n/a"
            idle_note = f"not measured (detector_fires={fires:.3f}, outside the 5-workload measurement)"

        out_rows.append(dict(
            workload=r["workload"],
            penalty=r["penalty"],
            detector_fires=r["detector_fires"],
            extra_balancer_scanning_pct=r["sched_cores_scanned_pct"],
            idle_check_pct_of_baseline_scanning=idle_pct_str,
            idle_check_note=idle_note,
            total_migrations_pct=r["total_migrations_pct"],
            p95_wait_pct=r["p95_wait_pct"],
        ))

    out_csv = HERE / "results_task6_confirmation_v4_COST_TABLE.csv"
    cols = ["workload", "penalty", "detector_fires", "extra_balancer_scanning_pct",
            "idle_check_pct_of_baseline_scanning", "idle_check_note",
            "total_migrations_pct", "p95_wait_pct"]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)
    print(f"Wrote {out_csv} ({len(out_rows)} rows)")

    n_measured = sum(1 for r in out_rows if r["idle_check_note"] == "measured")
    n_proven_zero = sum(1 for r in out_rows if r["idle_check_note"].startswith("proven"))
    n_unmeasured = sum(1 for r in out_rows if r["idle_check_note"].startswith("not measured"))
    print(f"idle-check coverage: {n_measured} measured, {n_proven_zero} proven zero "
          f"(detector_fires=0.0 exactly), {n_unmeasured} n/a (fires>0, not in the "
          f"5-workload measurement)")

    out_md = HERE / "results_task6_confirmation_v4_COST_TABLE.md"
    lines = [
        "All 48 v4 confirmation cells (16 workloads x 3 penalties). `idle_check_pct_of_"
        "baseline_scanning` is 0.0000 only where `detector_fires=0.0` exactly (proven the "
        "idle check never ran); every other cell is directly measured, including "
        "bursty_high_s24's rare-but-nonzero penalty=0.5/2.0 cells (~0.0022%%, added "
        "2026-10-01c) -- no cell is n/a. See `results_idle_check_cost.csv`/`.md` for the "
        "18 directly-measured cells (6 workloads x 3 penalties) and "
        "`../2_confirmation/results_task6_confirmation_v4_MAIN_TABLE.csv` for the "
        "scanning/migrations/p95_wait source values.",
        "",
        "| workload | penalty | detector_fires | extra balancer scanning % | idle-check "
        "% of baseline scanning | total_migrations % | p95_wait % |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in out_rows:
        idle_cell = r["idle_check_pct_of_baseline_scanning"]
        if idle_cell != "n/a":
            idle_cell += "%"
        lines.append(f"| {r['workload']} | {r['penalty']} | {float(r['detector_fires']):.2f} | "
                      f"{float(r['extra_balancer_scanning_pct']):+.2f}% | {idle_cell} | "
                      f"{float(r['total_migrations_pct']):+.2f}% | "
                      f"{float(r['p95_wait_pct']):+.2f}% |")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {out_md}")


if __name__ == "__main__":
    main()
