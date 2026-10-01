# Repository files

Full per-file breakdown of every folder. For the top-level tree, see `README.md`'s Repository layout section.

### `simulator/` — every module

| file | what it does |
|---|---|
| `Main.py` | Entry point: `run_simulation()` builds the topology, runs one simulation, returns metrics. Every experiment script imports this; running the file directly (`python Main.py`) is a small demo loop, not part of the pipeline. Owns 2 of Task 8's six fidelity fixes: `placement_root="own"` (fork-path placement anchored to the entry core's own node, not a fixed node-0 root) and `time_slice=2.8` (the effective, boot-scaled `sysctl_sched_base_slice`, not the raw constant). |
| `Topology.py` | Builds the `sched_domain` hierarchy (pair → node → one-hop → machine) mirroring `kernel/sched/topology.c`; also `classify_group()` (busy/spare/overloaded) and the `WorkStats` per-run instrumentation counters. |
| `Core.py` | One CPU: the EEVDF run/pick/enqueue loop, PELT load tracking, `is_idle()`/`running_count()`. |
| `EevdfTree.py` | The augmented rbtree `Core.pick_next()` walks — a port of `cfs_rq->tasks_timeline`, not an O(n) list scan. |
| `Task.py` | One task: the `sched_entity` analog (`vruntime`, `weight`, `prev_core`, `util_avg`, `last_ran_until`). |
| `LoadBalancer.py` | The baseline: periodic balancing, newly-idle balancing, migration (`_do_migrate`). Owns 4 of Task 8's six fidelity fixes directly — `checker_model`, `busy_factor`, `cache_hot`, `numa_fix` (the other two live in `Main.py`, above). Also owns `penalty_model` — not one of the six audit fixes, a separate cost-model *assumption* (Task 9a): real Linux has no migration-penalty parameter at all; the actual cost of a migration comes from hardware (cache misses, NUMA memory distance) and can't be simulated directly. This simulator adds a flat penalty instead, and `"ran_only"` charges it only to tasks that have already run — consistent with the kernel's own `task_hot()` treating a never-run task as not cache-hot (see `docs/LIMITATIONS.md`). |
| `BurstScheduler.py` | `BurstAwareLoadBalancer`, the one new mechanism: subclasses `LoadBalancer`, adds the detector-triggered early balance walk, `burst_gap_gate` (v3, superseded by v4 — see `docs/HISTORY.md`), and `burst_idle_check` (the confirmed v4 mechanism). |
| `BurstDetector.py` | The two-signal detector (arrival rate + queue growth on a pair domain) — never sees workload ground truth. |
| `Placement.py` | Fork-time core selection — the `select_task_rq_fair()` analog; also owns `placement_root`'s actual branching (`select_core_for_task()`). |
| `WorkloadGenerator.py` | The five standard profiles plus `stacked_burst` (Task 5); computes the entire task arrival plan up front (RNG-isolated from scheduling) so paired baseline/burst-aware runs see an identical task stream. |
| `Metrics.py` | Post-run metrics: p95/p99 wait, slowdown, makespan excess, `summary()`. |
| `Eventlog.py` | Flat timestamped record of every arrival/placement/detector-verdict/migration/completion. |
| `diagnostics.py` | Read-only live samplers and post-hoc analysis (detector recall/precision, lead time) — never changes behavior. |
| `paired_compare.py` | Shared sign-test/Wilcoxon/CI harness every experiment script uses for a paired baseline-vs-variant comparison. |

### `final_results/1_calibration_grid/` — pick a configuration

| file | what it does |
|---|---|
| `task6_threshold_grid.py` | Runs the 12-config x 9-workload x 3-penalty grid (baseline once per seed, reused across configs). |
| `task6_threshold_grid_recompute_harm.py` | **Authoritative** harm rule + selection; writes `selected_config_v4.json`. |
| `task6_threshold_grid_analyze.py` | Older/report-only harm rule, full per-cell grid printout, human-readable cross-check. |
| `task6_threshold_grid_tradeoff.py` | Harm-count-vs-benefit scatter plot across all 12 configs. |
| `task6_threshold_grid_harm_breakdown.py` | Which metric(s) tripped harm, for every flagged cell — including non-disqualifying penalty=2ms hits. |
| `results_task6_threshold_grid*_<workload>_{perseed,summary}.csv` | Raw per-seed / per-config-aggregated grid output, one pair per workload per pipeline version (no suffix = v1, `_v2`/`_v3`/`_v4` = later re-runs under `TASK8_V2`/`TASK9_V3`/`TASK10_V4`). |
| `selected_config_v3.json`, `selected_config_v4.json` | The winning (and runner-up) config from each version's grid, consumed by the matching confirmation run. |
| `tradeoff_points*.csv`, `figure_threshold_grid_tradeoff*.{png,pdf}`, `harm_breakdown*.csv`, `threshold_grid_analysis*.txt`, `threshold_grid_recompute_harm*.txt` | Outputs/captured-stdout of the four scripts above, one set per pipeline version. |
| `logs/` | Captured console output (stdout) of the individual grid-workload runs -- provenance for the v4 grid, intentionally tracked despite `.gitignore`'s `*.log` rule (added 2026-10-01, pre-publication audit): that rule only affects new, untracked `.log` files; it does not untrack files already committed. |

### `final_results/2_confirmation/` — confirm it

| file | what it does |
|---|---|
| `task6_confirmation_run.py` | Runs one workload (all variants, all penalties, 30 paired seeds) against baseline. |
| `task6_confirmation_analyze.py` | Merges all 16 workloads' output into the main table, RQ4 cost/benefit table, secondary-variant table, and the arrival-rate-vs-p95 figure. |
| `task6_confirmation_tables.py` | Per-variant appendix tables, the compact paper-ready table, and (v4 only) the direct checked-vs-unchecked `CHECK_EFFECT` table. |
| `results_task6_confirmation*_<workload>_{perseed,summary}.csv` | Raw per-seed / per-variant-aggregated confirmation output, one pair per workload per pipeline version. |
| `results_task6_confirmation*_MAIN_TABLE.csv`, `_COMPACT_TABLE.{csv,md}`, `_TABLE_<variant>.csv`, `_CHECK_EFFECT.{csv,md}` | Merged outputs of the two analysis scripts above. |
| `figure_p95wait_vs_arrival_rate*.png`, `confirmation_run_analysis.txt` | The arrival-rate figure and a captured-stdout snapshot. |

### `final_results/3_idle_check_cost/` — the idle check's own scanning cost, measured on the published runs

Moved here from `development/` (2026-10-01) since it measures the exact seeds/thresholds behind the published v4 confirmation, not an independent diagnostic — see `docs/NOTEBOOK.md`'s `2026-10-01` entry.

| file | what it does |
|---|---|
| `measure_idle_check_cost.py` | Re-runs the exact (workload, penalty, seed) triples behind the v4 confirmation on 5 workloads x 3 penalties, reading `idle_check_runs`/`idle_check_cores_read` off the balancer after each run. Safety-checks every recomputed `p95_wait`/`avg_wait`/`total_migrations` against the committed per-seed CSVs before writing anything. |
| `results_idle_check_cost.{csv,md}` | Its output: mean cores read per idle-check scan, and that as a %% of the same run's baseline `sched_cores_scanned`, per workload/penalty. |
| `build_cost_table.py` | Merges `results_idle_check_cost.csv` with `final_results/2_confirmation/results_task6_confirmation_v4_MAIN_TABLE.csv` into one per-(workload,penalty) cost/benefit table covering all 48 confirmation cells -- no new simulations, reads only committed CSVs. |
| `results_task6_confirmation_v4_COST_TABLE.{csv,md}` | Its output. |

### `final_results/figures/` — presentation figures, built from committed CSVs only

Read-only, same discipline as `3_idle_check_cost/build_cost_table.py`: no simulation runs, reads only the already-committed CSVs under `final_results/2_confirmation/`, writes nothing back into that folder. Same `TASK10_V4=1` guard as the pipeline scripts it follows (`docs/PIPELINE.md`'s Figures step) -- refuses to run otherwise. Figures are PNG only (300 dpi), no PDF.

| file | what it does |
|---|---|
| `make_figures.py` | One script, four figures. Reads `results_task6_confirmation_v4_MAIN_TABLE.csv`, `_TABLE_selected_unchecked.csv`, `_TABLE_original_q2_a0.8_or_unchecked.csv`, `_CHECK_EFFECT.csv`, and the `stacked_high` per-seed CSV. Cross-checks every plotted number against a second source table before drawing it (e.g. Fig 3's migration counts are independently recomputed and compared against `_CHECK_EFFECT.csv`'s own percentage column) and prints every check's result. |
| `fig1_headline_p95wait_by_workload.png` | All 16 workloads, p95_wait % change vs baseline at penalty=0.5ms, `selected_checked` vs `original_q2_a0.8_or_unchecked`, sorted so the 4 workloads where v4 significantly helps are at the top; a small circle marks a workload where v4's own change is exactly zero. |
| `fig2_stacked_high_perseed_scatter.png` | `stacked_high`/penalty=0.5: one point per seed, baseline vs `selected_checked` p95_wait, with the y=x line and the improved-seed count annotated. |
| `fig3_bursty_high_s64_checked_vs_unchecked.png` | `bursty_high_s64`, all 3 penalties: `total_migrations` and `p95_wait` for baseline/`selected_unchecked`/`selected_checked`. The one harmful cell (`selected_unchecked`, penalty=2.0) is called out by a text label on the migrations panel naming the specific metrics that tripped it (read from that row's own per-metric harm columns -- `avg_slowdown`/`makespan_excess`, not p95_wait or migrations themselves), rather than a marker on both panels. |
| `fig4_p95wait_vs_arrival_rate.png` | The arrival-rate figure (`figure_p95wait_vs_arrival_rate_v4.png`'s data), restyled: p95_wait % change vs `arrival_rate_during_burst`, burst sizes 4 and 12, `selected_checked` vs `original_q2_a0.8_or_unchecked`, penalties 0.5 and 2ms (matching Fig 1's penalty), with any harmful point marked. |

All four figures use plain-language series names (e.g. "Burst trigger + idle check (v4)" for `selected_checked`) rather than the CSV's own variant codes -- the one `LABELS` dict at the top of `make_figures.py` is the only place that mapping lives.

### `development/` — one-off diagnosis and verification, real findings that aren't the final numbers

Each subfolder is one investigation; `development/INDEX.md` indexes every script and what it found, cross-referenced to the `docs/NOTEBOOK.md` entry that explains it. Folder-to-task mapping is in `docs/HISTORY.md`'s task table.

### `superseded/` — kept only for provenance, never cite

Early results invalidated by bugs found later (a shared-RNG bug that silently gave baseline and burst-aware *different* task streams for "the same seed"; a burst-size sweep with a workload-generation cap bug; a v1 attempt-tracer that matched the wrong domain) or superseded by a later, more complete re-run — plus `Experiment.py`, the original experiment harness, unused by the final pipeline (every `final_results/` script calls `Main.run_simulation()` directly) but kept for history since several modules' comments still cite its seed scheme. `superseded/INDEX.md` explains every file's specific reason, cited to the `docs/NOTEBOOK.md` entry that found it.

### `docs/`

`FIDELITY_AUDIT.md` — the 13-area, file:line-cited comparison against real Linux v7.2 source (its own §1-§15 numbering — cross-references in this project's other docs always say `FIDELITY_AUDIT.md §N` explicitly, since none of this project's own documentation uses numbered sections, to keep the two apart). `NOTEBOOK.md` — the complete dated lab notebook: every hypothesis, fix, correction, and result from the start of the project through the v4 confirmation, in the order it actually happened (corrections are appended, never silently edited in place). `HISTORY.md`, `PIPELINE.md`, `FILES.md` (this file), `LIMITATIONS.md` — the four docs `README.md` links out to; see `README.md`'s Further documentation section for what's in each.

### `tests/`

`test_invariants.py` — automated bookkeeping checks (see `docs/PIPELINE.md`'s Invariant tests section); no simulator file is edited to run it, every check is a monkeypatch/wrapper restored immediately after.
