# The pipeline, step by step

All commands are PowerShell (Windows). An environment variable set with `$env:NAME=1` lasts only for that PowerShell window — set it again in a new window, or `Remove-Item Env:\NAME` to clear it early.

## Pipeline version selection (required)

`task6_threshold_grid.py`, `task6_threshold_grid_recompute_harm.py`, `task6_threshold_grid_analyze.py`, `task6_confirmation_run.py`, and `task6_confirmation_analyze.py` (which `task6_threshold_grid_tradeoff.py`/`task6_threshold_grid_harm_breakdown.py`/`task6_confirmation_tables.py` each import from, so the same guard covers all eight) each require exactly one of `TASK8_V2` / `TASK9_V3` / `TASK10_V4` to be set — **`$env:TASK10_V4=1` for every command in this file**, reproducing the published v4 results. Running any of these scripts with none of the three set now exits immediately with a non-zero code and an error, before reading or writing anything (pre-publication audit fix, 2026-10-01, see `docs/NOTEBOOK.md`): forgetting the env var used to silently fall back to the original v1 config/seeds *and* overwrite the un-suffixed v1 result files already on disk, with no warning. Set `$env:TASK6_V1_LEGACY=1` instead if you deliberately want that old v1 behavior back.

## Invariant tests

```powershell
cd tests
python test_invariants.py
```

No arguments. Runs a fixed matrix: 8 workloads x 2 schedulers (baseline, burst-aware) x 2 penalties (0, 2ms) x 3 seeds (base 90000) x 3 configs (legacy; `penalty_model="ran_only"`+`burst_gap_gate=True`; `penalty_model="ran_only"`+`burst_idle_check=True`), each run twice for the determinism check — 576 simulation runs, verified: the real run this doc was checked against took 1662s (~28 minutes; per-run cost varies a lot with background system load — an earlier run of the same suite took 260s). Every check is bookkeeping/consistency, **not** a Linux-fidelity claim (that's `docs/FIDELITY_AUDIT.md`'s job) — it catches a lost or duplicated task, a migration that moves a currently-running task, double-counted migrations, or non-deterministic replay:

1. **conservation** — every planned task completes exactly once; no core ends the run with a task still running or queued.
2. **timing** — a task never starts before it arrives, its turnaround is never less than its own CPU time, its wait is never negative.
3. **work accounting** — independently-sampled total executed time equals the sum of every task's `cpu_time` plus `migration_penalty × (migrations that actually charged the penalty)`.
4. **single location** — at every sampled tick, every live task is running or queued on exactly one core, never zero, never more than one.
5. **only queued tasks migrate** — a migrated task was actually sitting in the source core's run queue (not currently running) at the moment of the migration.
6. **migration bookkeeping** — the balancer's own migration counter, the logged migration events, and the sum of every task's own migration count all agree.
7. **determinism** — the same config run twice on the same seed produces identical metrics and an identical event-log hash.
8. **paired workload** — baseline and burst-aware, same seed, actually saw the identical generated task stream (the precondition every paired comparison in this project depends on).
9. **stacked placement** — on `stacked_burst`-family workloads, a task with a forced `direct_core` actually lands on that exact core.
10. **burst bookkeeping** — the baseline scheduler never triggers the burst path and never logs a `"burst"`-tagged migration (it has no burst path to trigger).
11. **idle-check bookkeeping** — `burst_idle_check`'s two new counters stay internally consistent, and the skip counter stays exactly zero whenever the flag is off.

## Calibration grid (v4)

```powershell
cd final_results/1_calibration_grid
$env:TASK10_V4=1
python task6_threshold_grid.py stacked_low
python task6_threshold_grid.py stacked_medium
python task6_threshold_grid.py stacked_high
python task6_threshold_grid.py bursty_high_s24
python task6_threshold_grid.py bursty_high_s64
python task6_threshold_grid.py rate0.5_s4
python task6_threshold_grid.py rate0.5_s12
python task6_threshold_grid.py rate3.0_s4
python task6_threshold_grid.py rate3.0_s12
```

Each call takes one required argument, the workload key (`sys.argv[1]`); two optional ones exist for splitting a slow workload across background jobs (`sys.argv[2]` = penalty index 0/1/2, `sys.argv[3]` = first/second half of the 12 configs) but weren't needed for the real run above. **12 configs**: `queue_growth_threshold` in `{2, 4, 8}` x `arrival_rate_threshold` in `{0.8, 1.5}` x `combine` in `{"and", "or"}`. **9 grid workloads** (a subset of the 16 confirmation workloads — see `README.md`'s Workloads table). **Penalties** 0, 0.5, 2ms. **Seeds** 110000-110029 per workload (30 paired seeds, fresh — see `docs/HISTORY.md`'s Seed ranges section); baseline runs once per (workload, penalty, seed) and is reused across all 12 configs. Rough estimate, not precisely measured for the real run (task count 40-640 depending on workload, ~1080 variant runs + 90 baseline runs per workload): single-digit to low-double-digit minutes per workload call, more for `bursty_high_s64`'s 640 tasks; the full 9-workload grid is a background-job-sized job, not a quick one.

## Selection: `recompute_harm.py` → `selected_config_v4.json`

```powershell
python task6_threshold_grid_recompute_harm.py
```

No arguments; reads the 9 `_v4_..._perseed.csv` files the step above wrote. This is the **authoritative** selection script (`task6_threshold_grid_analyze.py` also runs a harm check, but with an older, direction-blind significance test kept only as a report-only cross-check — see its module docstring and `docs/NOTEBOOK.md`'s `2026-09-27h` correction).

**Harm rule, per metric** (`p95_wait`, `avg_wait`, `avg_slowdown`), per (workload, penalty): harmful iff `harms > wins` (more seeds got worse than got better — not just "the mean moved," which a symmetric significance test can get backwards) **and** `sign_p(harms, n_eff) < 0.05` **and** `mean_variant > mean_baseline` (the mean actually moved the harmful direction too). A **config** is disqualified if it's harmful on *any* (workload, metric) at penalty **0 or 0.5** — 2ms is measured and reported in full but does **not** disqualify (it's a deliberately pessimistic stress test, not a realistic migration cost: real Linux's own `sysctl_sched_migration_cost` estimate is 0.5ms). Among the harm-free configs, the winner is the one with the largest mean `p95_wait` reduction on `stacked_medium` + `stacked_high`, averaged over penalties 0 and 0.5.

`selected_config_v4.json` contains: `selected` and `runner_up` (each `{config, queue_growth_threshold, arrival_rate_threshold, combine}`), `selected_mean_p95_pct`/`runner_up_mean_p95_pct` (the ranking score), `harm_free_configs` (every config that passed, not just the winner), and `rule_penalties` (`["0.0", "0.5"]` — which penalties disqualify). If no config is harm-free, `selected`/`runner_up` are `null` and every downstream script reports that plainly instead of crashing.

## Confirmation (v4)

```powershell
cd ../2_confirmation
$env:TASK10_V4=1
foreach ($wl in @(
    "stacked_low","stacked_medium","stacked_high","bursty_high_s24","bursty_high_s64",
    "heavy_tail_high","rate0.5_s4","rate0.5_s12","rate0.75_s4","rate0.75_s12",
    "rate1.0_s4","rate1.0_s12","rate1.5_s4","rate1.5_s12","rate3.0_s4","rate3.0_s12"
)) {
    python task6_confirmation_run.py $wl
    if ($LASTEXITCODE -ne 0) { Write-Error "FAILED: $wl"; break }
}
(Get-ChildItem "results_task6_confirmation_v4_*_summary.csv").Count   # expect 16
```

Each call takes one required argument, the workload key. **16 workloads** (see `README.md`'s Workloads table — the 9 grid workloads plus `rate0.75_*`/`rate1.0_*` and `heavy_tail_high`, none of which the grid touched, so the grid's own selection is tested on workloads it never saw). **Fresh seeds** 120000-120029 per workload (never used by the grid or by any prior audit/diagnostic — see `docs/HISTORY.md`'s Seed ranges section). **4 variants**, all run against the SAME paired baseline seeds:

- `selected_checked` — the winning config, `burst_idle_check=True`. The headline.
- `selected_unchecked` — the same thresholds, `burst_idle_check=False`. Isolates what the idle check itself is doing, holding the detector fixed.
- `runner_up_checked` — the runner-up config, WITH the mechanism (unlike v3's equivalent, which tested its runner-up without the mechanism — here the mechanism itself is under test, not just the threshold choice).
- `original_q2_a0.8_or_unchecked` — the pre-calibration detector (`queue_growth_threshold=2, arrival_rate_threshold=0.8, combine="or"`), no mechanism. A historical reference point: is the calibrated+mechanism combination actually better than where the project started?

`penalty_model="ran_only"` is applied to every one of these (baseline included) — it's a cost-model correction/assumption (see `docs/FILES.md`'s `LoadBalancer.py` row), not one of the four things being compared. Rough estimate, not precisely measured for the real run (4 variants + baseline, 3 penalties, 30 seeds each, per workload): comparable per-workload cost to the grid step above; the full 16-workload run is a multi-hour background job, not a quick one.

**Caveat, verified against the code, not assumed:** the 4 variants above are what this script explicitly *requests* (`_load_v4_variants()`, reading `queue_growth_threshold`/`arrival_rate_threshold`/`combine` from `selected_config_v4.json` and passing `burst_idle_check=True`, `penalty_model="ran_only"` by hand) — this is **not** what you get from `BurstAwareLoadBalancer(...)` with no keyword arguments. The bare class defaults are still the older `queue_growth_threshold=2, arrival_rate_threshold=1.5, combine="and"` (v1's own calibrated pick — see `docs/HISTORY.md`), `burst_idle_check=False`, `penalty_model="all"` (`simulator/BurstDetector.py`, `simulator/BurstScheduler.py`). If you call the classes directly instead of going through this script, pass the kwargs above by hand.

## Analysis and tables

```powershell
python task6_confirmation_analyze.py
python task6_confirmation_tables.py
```

No arguments; both read every `results_task6_confirmation_v4_*_summary.csv` already on disk.

`task6_confirmation_analyze.py` prints/writes: the **MAIN TABLE** (baseline vs `selected_checked`, all 16 workloads, all 3 penalties → `_MAIN_TABLE.csv`); **RQ4** (extra scanning work vs p95 benefit, cost/benefit tradeoff, printed only); **SECONDARY** (all 4 variants' p95 change side by side, printed only — no selection happens here, that was already decided in the Selection step above); and the arrival-rate-vs-p95 figure (`figure_p95wait_vs_arrival_rate_v4.png`, `selected_checked` vs `original_q2_a0.8_or_unchecked` across the 5 arrival rates, burst sizes 4 and 12).

`task6_confirmation_tables.py` writes: one `_TABLE_<variant>.csv` per non-headline variant (full per-penalty detail); the **COMPACT TABLE** (`_COMPACT_TABLE.{csv,md}` — one row per workload, grouped by whether `selected_checked` showed disqualifying harm / helped / was silent / fired without benefit); and, v4 only, **CHECK_EFFECT** (`_CHECK_EFFECT.{csv,md}` — `selected_checked` vs `selected_unchecked` compared *directly*, same seed/workload/penalty, not against baseline, isolating the idle check's own effect).

## Figures

```powershell
cd ../figures
python make_figures.py
```

No arguments; same `TASK10_V4=1` requirement as every script above (refuses to run otherwise). Reads only CSVs the two steps above already wrote — no new simulations, nothing in `final_results/2_confirmation/` is modified. Writes four PNG figures (300 dpi) into `final_results/figures/`:

- `fig1_headline_p95wait_by_workload.png` — all 16 workloads' `p95_wait` % change at penalty=0.5ms, `selected_checked` vs `original_q2_a0.8_or_unchecked`, from `_MAIN_TABLE.csv` and `_TABLE_original_q2_a0.8_or_unchecked.csv`.
- `fig2_stacked_high_perseed_scatter.png` — `stacked_high`/penalty=0.5, one point per seed, from `results_task6_confirmation_v4_stacked_high_perseed.csv`.
- `fig3_bursty_high_s64_checked_vs_unchecked.png` — `bursty_high_s64`, all 3 penalties, `total_migrations` and `p95_wait` for baseline/`selected_unchecked`/`selected_checked`, from `_MAIN_TABLE.csv` and `_TABLE_selected_unchecked.csv`, cross-checked against `_CHECK_EFFECT.csv`.
- `fig4_p95wait_vs_arrival_rate.png` — the same data as `figure_p95wait_vs_arrival_rate_v4.png` above, restyled to match the other three (penalties 0.5/2ms, matching Fig 1, rather than that figure's 0/2ms), from `_MAIN_TABLE.csv` and `_TABLE_original_q2_a0.8_or_unchecked.csv`.

All four use plain-language series names (e.g. "Burst trigger + idle check (v4)") rather than the CSVs' own variant codes, and read every plotted fact -- which workloads help, where a value is exactly zero, which cells are harmful and on which specific metric -- from the CSVs at run time (see `make_figures.py`'s own settings block for the only hardcoded choices: labels, colors, and which workload/penalty Figs 2-4 look at).

## How to read an output line

A real line from the v4 confirmation run (`results_task6_confirmation_v4_stacked_high_summary.csv`, reconstructed through `task6_confirmation_run.py`'s own print format):

```
stacked_high     penalty=0.5ms selected_checked       p95_wait: 24.523->18.448 (-24.8%) sign_p=<0.0001 harm=False fires=151.4 cores_scanned_pct=+10.9%
```

- `stacked_high` / `penalty=0.5ms` / `selected_checked` — which workload, penalty, and variant this row is.
- `p95_wait: 24.523->18.448` — the 95th-percentile wait time, in simulated ms, averaged across the 30 paired seeds: baseline (`24.523`) then this variant (`18.448`).
- `(-24.8%)` — the percent change, variant vs baseline.
- `sign_p=<0.0001` — the exact two-sided binomial sign test on the 30 paired per-seed differences (`paired_compare.sign_test_p`). With n=30 non-tied seeds, `p<0.0001` requires at least **26 of 30** moving the same direction (computed directly: `sign_test_p(25, 30) = 0.000325`, just above the threshold; `sign_test_p(26, 30) = 0.0000595`, just under it) — it does *not* mean literally every seed agreed. 30/30 would give `p ≈ 1.86e-9`.
- `harm=False` — this workload/penalty's overall verdict: did *any* of the SIX metrics the confirmation run checks (`p95_wait`, `p99_wait`, `avg_wait`, `avg_slowdown`, `p95_slowdown`, `makespan_excess` — more than the grid's own 3-metric harm rule in the Selection section above, `task6_confirmation_run.py`'s own `METRICS` list) trip the same `harms>wins` rule?
- `fires=151.4` — mean number of times the detector fired per run (before the idle check even runs — see `docs/NOTEBOOK.md`'s `2026-09-30g` entry for why a high fire count doesn't always mean a high walk count).
- `cores_scanned_pct=+10.9%` — how much more periodic-balancer scanning work this variant did vs baseline, the mechanism's "cost" side of the cost/benefit tradeoff (the Analysis and tables step's RQ4, above).
