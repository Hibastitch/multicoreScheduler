# final_results/ — everything the paper cites

## 1_calibration_grid/ — pre-registered 12-config detector threshold search (seeds 10000+, n=30)

- `task6_threshold_grid.py` — runs one workload's full 12-config grid, both migration penalties; `python task6_threshold_grid.py <workload_key>`.
- `task6_threshold_grid_analyze.py` — merges all 9 workloads' summary CSVs, applies the pre-registered selection rule, prints the full grid.
- `task6_threshold_grid_recompute_harm.py` — recomputes all 216 cells with the corrected (`harms > wins`) harm rule, from the existing per-seed CSVs, no new simulations.
- `threshold_grid_analysis.txt`, `threshold_grid_recompute_harm.txt` — captured stdout from the two analysis scripts above.
- `results_task6_threshold_grid_<workload>_perseed.csv` / `_summary.csv` (9 workloads × 2) — raw per-seed and aggregated results, one pair per workload.
- `logs/threshold_grid_<workload>.log` (9 files) — captured stdout from each workload's own `task6_threshold_grid.py` run (one parallel background job per workload).

## 2_confirmation/ — final 16-workload confirmation run, FINAL config vs baseline (fresh seeds 20000-21529, n=30)

- `task6_confirmation_run.py` — runs one workload: baseline vs FINAL vs two report-only secondary detector variants; `python task6_confirmation_run.py <workload_key>`.
- `task6_confirmation_analyze.py` — merges all 16 workloads, builds the main table, prints RQ4 cost/benefit, plots `figure_p95wait_vs_arrival_rate.png`.
- `task6_confirmation_tables.py` — builds the per-variant tables and the compact paper-ready table, from existing summary CSVs, no new simulations.
- `confirmation_run_analysis.txt` — captured stdout from `task6_confirmation_analyze.py`.
- `figure_p95wait_vs_arrival_rate.png` — p95_wait % change vs. arrival rate, sizes 4 & 12, FINAL vs. original detector.
- `results_task6_confirmation_MAIN_TABLE.csv` — one row per (workload, penalty): baseline vs. FINAL, every metric.
- `results_task6_confirmation_TABLE_original.csv` / `_TABLE_runnerup.csv` — same shape, for the two report-only secondary detector variants.
- `results_task6_confirmation_COMPACT_TABLE.csv` / `.md` — the paper-ready compact table (one row per workload, significance stars, harm-metric column, sign-flip dagger marks).
- `results_task6_confirmation_<workload>_perseed.csv` / `_summary.csv` (16 workloads × 2) — raw per-seed and aggregated results, one pair per workload.
