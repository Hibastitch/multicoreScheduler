# multicoreScheduler

A discrete-event simulation of Linux's CFS/EEVDF scheduler and its multi-level load-balancing pipeline, built to test one question: can a new, purely-observable-signal "a burst of tasks just landed here" trigger call the kernel's own balancing logic *earlier* than the kernel's own periodic/newly-idle timers would, without making anything else worse?

> **Reproduce the headline result** (the confirmed configuration is already selected — `final_results/1_calibration_grid/selected_config_v4.json` is committed, so this reproduces the *confirmation* run, not the calibration grid):
> ```powershell
> cd final_results/2_confirmation
> $env:TASK10_V4=1
> foreach ($wl in @("stacked_low","stacked_medium","stacked_high","bursty_high_s24","bursty_high_s64",
>     "heavy_tail_high","rate0.5_s4","rate0.5_s12","rate0.75_s4","rate0.75_s12",
>     "rate1.0_s4","rate1.0_s12","rate1.5_s4","rate1.5_s12","rate3.0_s4","rate3.0_s12")) {
>     python task6_confirmation_run.py $wl
> }
> python task6_confirmation_analyze.py
> python task6_confirmation_tables.py
> ```
> Rough estimate, not precisely re-measured (see §5d): a multi-hour background job, dominated by the 16-workload loop. Result: `results_task6_confirmation_v4_MAIN_TABLE.csv` (§2 below is read straight from it).

## 1. What this is

This is a [SimPy](https://simpy.readthedocs.io/) discrete-event simulator of Linux's EEVDF process scheduler and its load-balancing pipeline (fork-time placement, periodic balancing, and newly-idle balancing), running on a simulated 32-core machine: 4 NUMA nodes on a ring, each node built from SMT (hyperthread) pairs, with the kernel's real `sched_domain` hierarchy (pair → node → one-hop → machine) modeled on top. The baseline scheduler — placement, periodic balancing, newly-idle balancing, the EEVDF run queue itself — is not a loose approximation; it was built and then individually audited against the actual Linux v7.2 kernel source (`kernel/sched/fair.c`, `core.c`, `topology.c`), file:line cited throughout `docs/FIDELITY_AUDIT.md`, with six real fidelity gaps found and fixed. On top of that verified baseline, the project adds exactly one new mechanism: a **burst-aware trigger** — a detector watching two observable signals (arrival rate and queue-growth on a pair-level domain) plus a machine-wide idle-capacity check, which together decide when to call the kernel's own existing balancing walk *early* instead of waiting for the next periodic tick. The mechanism is evaluated the same way an A/B test would be: a pre-registered calibration grid picks one configuration on 9 workloads, then a completely fresh set of seeds confirms (or doesn't) that the picked configuration actually works — including on 7 workloads the grid never saw.

## 2. Headline result

From the real, committed `final_results/2_confirmation/results_task6_confirmation_v4_MAIN_TABLE.csv` (16 workloads x 3 penalties = 48 cells, n=30 paired seeds each): the confirmed configuration (`queue_growth_threshold=8, arrival_rate_threshold=1.5, combine="or"`, plus the machine-wide idle check) is **harm-free on all 48 cells** — never significantly worse than the baseline on any metric, any workload, any penalty. Where it fires, it helps: p95 wait time drops 14.7%-30.9% on four dense-burst workloads (`stacked_medium`, `stacked_high`, `rate1.5_s12`, `rate3.0_s12`, all `sign_p<0.0001`). On the 11 workloads whose bursts are too small or too slow to trip the calibrated thresholds, it is silent — identical to baseline, not worse (this includes all five `rate*_s4` workloads, a structural blind spot explained in §9). Full numbers: `docs/NOTEBOOK.md`'s `2026-09-30g` entry.

## 3. Project history: tasks and versions

A **task** is a unit of work numbered in the order it happened — an experiment, an audit, a fix, a diagnostic. Script names carry the task number in which they were *written*, not the pipeline order they run in (e.g. `task7_checker_election_audit.py` is Task 7's own script, unrelated to `task6_*`'s step numbering). Task 6 is the one that defined the actual experiment — the calibration grid and the confirmation run — so its `task6_*` scripts are the main pipeline (§5) and get re-run, unmodified in *structure*, for every later version. A **version** (v1-v4) is the state of the baseline plus mechanism that Task 6's experiment was run on; which version runs is picked by an environment-variable switch (none / `TASK8_V2` / `TASK9_V3` / `TASK10_V4`), each with its own fresh seed range (§7).

| version | produced by | what changed | env var | seeds (grid / confirmation) | result | problem found | how it was found | fixed in |
|---|---|---|---|---|---|---|---|---|
| v1 | Task 6 | Task 6's own 12-config grid calibrates the detector from the original `q2_a0.8_or` up to `q2_a1.5_and` (kept as a report-only reference thereafter); baseline not yet audited against Linux source | *(none)* | 10000+ / 20000+ | confirmation: harm-free on every workload run, including one (`heavy_tail`) never seen by the grid | baseline not Linux-faithful — periodic-checker election doesn't match `should_we_balance()` | Task 7, reading `fair.c` directly | v2 |
| v2 | Tasks 7+8 | baseline fixed to match Linux v7.2: `checker_model`, `busy_factor`, `cache_hot`, `numa_fix`, `placement_root`, `time_slice` (§4's repo table shows which module owns each) | `TASK8_V2` | 10000+ / 40000+ (confirmation not run) | grid: **0/12 configs harm-free** — the fixed baseline exposes real harm the unaudited one hid | firing on a saturated machine (`bursty_high_s64`) harms `avg_slowdown`, concentrated at penalty=2ms (all 12 configs), 2 configs also at penalty=0 | the v2 grid run itself | v3 |
| v3 | Task 9 | `burst_gap_gate` (skip a domain unless the burst core is >=2 `nr_running` ahead of the least-loaded other core in it — bundled with a destination change, the balance target becomes that least-loaded core) + `penalty_model="ran_only"` | `TASK9_V3` | 70000+ / 80000+ | grid: 2/12 harm-free (`q4_a1.5_and` selected, `q2_a1.5_and` runner-up, near-tied); confirmation: 1 harm cell (`bursty_high_s64`, penalty=0, `avg_wait` +3.67%) | the DESTINATION change, not the gap check itself, causes the harm — `selected_gated` made MORE burst migrations than ungated, not fewer, because the least-loaded destination measures a larger imbalance | Task 9's own diagnostic (`development/task9_gap_gate_diagnostic/`) traced it to the destination change; Task 10a (`development/task10_check_mechanism/`) then showed idle capacity, not imbalance, is what actually separates helpful from harmful firings | v4 |
| v4 | Task 10 | `burst_idle_check` only: one machine-wide "is any core idle" check before the unchanged balancing walk — no gap gate, no destination change | `TASK10_V4` | 110000+ / 120000+ | grid: 8/12 harm-free (`q8_a1.5_or` selected, `q2_a1.5_and` runner-up, near-tied); confirmation: **48/48 harm-free** | known gap (not fixed, an accepted trade-off): `q8_a1.5_or` can never fire on a 4-task burst — `queue_growth_threshold=8 > burst_size=4` is arithmetically impossible | confirmed directly in the v4 confirmation data (`detector_fires=0.0` on every `rate*_s4` workload) | *(not fixed — traded off against the harm-free result)* |

Every task, in order (folders are where the *investigation* lives, not necessarily the final code — that's simulator/ and final_results/, §4):

| task | what it was | folder | NOTEBOOK entry |
|---|---|---|---|
| 1 | `heavy_tail`/high check — its ~5.7% gain didn't hold up once the RNG-isolation bug (Fix 3d) was found | `superseded/` (invalidated by that bug) | 2026-09-26c |
| 2 | explained why `placement_levels_walked_mean` was a flat constant (800) everywhere — not a bug, a deterministic product of fixed `n_tasks` and topology depth; renamed the metric for clarity | none — a `Metrics.py`/`Topology.py` naming fix | 2026-09-26d |
| 3 / 3b | diagnosed `newidle_cost_avg`'s EMA self-suppressing to near-zero; fixed with `newidle_mode="transition"` (fires once per busy→idle edge) | `development/newidle/` | 2026-09-26f / 2026-09-26h |
| 4 | implemented the live diagnostic samplers (`simulator/diagnostics.py`) and verified they don't perturb the simulation they're observing | `development/instrumentation_checks/` | 2026-09-26e, "Task 4 (slimmed)" |
| 5 | introduced the `stacked_burst` profile and confirmed the first real burst-aware effect — the original run was later superseded by Task 6 Step 3's full A+B+C re-run, but the workload itself lives on in `WorkloadGenerator.py` | `superseded/` (original run only) | 2026-09-26o (hypothesis), 2026-09-26s (result) |
| 6 | THE experiment: the per-CPU balance-timer bug (Fix A), the queue-blind load signal and real kernel `calculate_imbalance()` (Fix B/C), the `burst_resets_timer` ablation, burst-size/interval sweeps, the 12-config detector threshold calibration, and the full confirmation run | `final_results/1_calibration_grid/` + `final_results/2_confirmation/` (main pipeline); `development/fixA_timers/`, `fixB_fixC/`, `low_intensity_reversal/`, `sweeps_precalibration/` (its sub-investigations) | many entries, 2026-09-27 through 2026-09-27j |
| 7 | Fix D: periodic-checker election didn't match real `should_we_balance()` | `development/topology_audit/` | 2026-09-29 |
| 8 / 8b | 13-area Linux-fidelity audit against v7.2 source; six fixes made default; 8b = automated invariant test suite | `development/fidelity_audit/` (8); `tests/` (8b) | 2026-09-29 through 2026-09-29e (8); 2026-09-29f (8b) |
| 9 | `burst_gap_gate` + `penalty_model="ran_only"` (v3) | simulator/ + `final_results/` v3 outputs (its diagnostic: `development/task9_gap_gate_diagnostic/`) | 2026-09-29h (pre-registration), 2026-09-30 (diagnostic) |
| 10 / 10a | `burst_idle_check` (v4); 10a = the hypothetical-machine-wide-check diagnostic that motivated it | simulator/ + `final_results/` v4 outputs (10); `development/task10_check_mechanism/` (10a) | 2026-09-30d (pre-registration), 2026-09-30b (10a), 2026-09-30g (v4 result) |

## 4. Repository layout

```
simulator/              the scheduler itself — every module Main.run_simulation() wires together
final_results/
  1_calibration_grid/    Step 1 of the pipeline: pick a detector configuration
  2_confirmation/        Step 2: confirm the pick on fresh seeds and workloads
development/             one-off diagnosis/verification scripts — real findings, not the final numbers
superseded/              early results known to be invalid or replaced — kept for provenance, never cite
docs/                    the fidelity audit and the full dated lab notebook
tests/                   automated invariant checks (bookkeeping, not scheduling-fidelity)
```

### `simulator/` — every module

| file | what it does |
|---|---|
| `Main.py` | Entry point: `run_simulation()` builds the topology, runs one simulation, returns metrics. Every experiment script imports this; running the file directly (`python Main.py`) is a small demo loop, not part of the pipeline. Owns 2 of Task 8's six fidelity fixes: `placement_root="own"` (fork-path placement anchored to the entry core's own node, not a fixed node-0 root) and `time_slice=2.8` (the effective, boot-scaled `sysctl_sched_base_slice`, not the raw constant). |
| `Topology.py` | Builds the `sched_domain` hierarchy (pair → node → one-hop → machine) mirroring `kernel/sched/topology.c`; also `classify_group()` (busy/spare/overloaded) and the `WorkStats` per-run instrumentation counters. |
| `Core.py` | One CPU: the EEVDF run/pick/enqueue loop, PELT load tracking, `is_idle()`/`running_count()`. |
| `EevdfTree.py` | The augmented rbtree `Core.pick_next()` walks — a port of `cfs_rq->tasks_timeline`, not an O(n) list scan. |
| `Task.py` | One task: the `sched_entity` analog (`vruntime`, `weight`, `prev_core`, `util_avg`, `last_ran_until`). |
| `LoadBalancer.py` | The baseline: periodic balancing, newly-idle balancing, migration (`_do_migrate`). Owns 4 of Task 8's six fidelity fixes directly — `checker_model`, `busy_factor`, `cache_hot`, `numa_fix` (the other two live in `Main.py`, above). Also owns `penalty_model` — not one of the six audit fixes, a separate cost-model *assumption* (Task 9a): real Linux has no migration-penalty parameter at all; the actual cost of a migration comes from hardware (cache misses, NUMA memory distance) and can't be simulated directly. This simulator adds a flat penalty instead, and `"ran_only"` charges it only to tasks that have already run — consistent with the kernel's own `task_hot()` treating a never-run task as not cache-hot (see §9). |
| `BurstScheduler.py` | `BurstAwareLoadBalancer`, the one new mechanism: subclasses `LoadBalancer`, adds the detector-triggered early balance walk, `burst_gap_gate` (v3, superseded by v4 — §3), and `burst_idle_check` (the confirmed v4 mechanism). |
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
| `logs/` | Captured stdout from individual grid-workload runs. |

### `final_results/2_confirmation/` — confirm it

| file | what it does |
|---|---|
| `task6_confirmation_run.py` | Runs one workload (all variants, all penalties, 30 paired seeds) against baseline. |
| `task6_confirmation_analyze.py` | Merges all 16 workloads' output into the main table, RQ4 cost/benefit table, secondary-variant table, and the arrival-rate-vs-p95 figure. |
| `task6_confirmation_tables.py` | Per-variant appendix tables, the compact paper-ready table, and (v4 only) the direct checked-vs-unchecked `CHECK_EFFECT` table. |
| `results_task6_confirmation*_<workload>_{perseed,summary}.csv` | Raw per-seed / per-variant-aggregated confirmation output, one pair per workload per pipeline version. |
| `results_task6_confirmation*_MAIN_TABLE.csv`, `_COMPACT_TABLE.{csv,md}`, `_TABLE_<variant>.csv`, `_CHECK_EFFECT.{csv,md}` | Merged outputs of the two analysis scripts above. |
| `figure_p95wait_vs_arrival_rate*.png`, `confirmation_run_analysis.txt` | The arrival-rate figure and a captured-stdout snapshot. |

### `development/` — one-off diagnosis and verification, real findings that aren't the final numbers

Each subfolder is one investigation; `development/INDEX.md` indexes every script and what it found, cross-referenced to the `docs/NOTEBOOK.md` entry that explains it. Folder-to-task mapping is in §3's second table.

### `superseded/` — kept only for provenance, never cite

Early results invalidated by bugs found later (a shared-RNG bug that silently gave baseline and burst-aware *different* task streams for "the same seed"; a burst-size sweep with a workload-generation cap bug; a v1 attempt-tracer that matched the wrong domain) or superseded by a later, more complete re-run — plus `Experiment.py`, the original experiment harness, unused by the final pipeline (every `final_results/` script calls `Main.run_simulation()` directly) but kept for history since several modules' comments still cite its seed scheme. `superseded/INDEX.md` explains every file's specific reason, cited to the `docs/NOTEBOOK.md` entry that found it.

### `docs/`

`FIDELITY_AUDIT.md` — the 13-area, file:line-cited comparison against real Linux v7.2 source (its own §1-§15 numbering, unrelated to this README's sections — cross-references below always say `FIDELITY_AUDIT.md §N` to keep the two apart). `NOTEBOOK.md` — the complete dated lab notebook: every hypothesis, fix, correction, and result from the start of the project through the v4 confirmation, in the order it actually happened (corrections are appended, never silently edited in place).

### `tests/`

`test_invariants.py` — automated bookkeeping checks (§5a); no simulator file is edited to run it, every check is a monkeypatch/wrapper restored immediately after.

## 5. The pipeline, step by step

All commands are PowerShell (Windows). An environment variable set with `$env:NAME=1` lasts only for that PowerShell window — set it again in a new window, or `Remove-Item Env:\NAME` to clear it early.

### 5a. Invariant tests

```powershell
cd tests
python test_invariants.py
```

No arguments. Runs a fixed matrix: 8 workloads x 2 schedulers (baseline, burst-aware) x 2 penalties (0, 2ms) x 3 seeds (base 90000) x 3 configs (legacy; `penalty_model="ran_only"`+`burst_gap_gate=True`; `penalty_model="ran_only"`+`burst_idle_check=True`), each run twice for the determinism check — 576 simulation runs, verified: the real run this README was checked against took 1662s (~28 minutes; per-run cost varies a lot with background system load — an earlier run of the same suite took 260s). Every check is bookkeeping/consistency, **not** a Linux-fidelity claim (that's `docs/FIDELITY_AUDIT.md`'s job) — it catches a lost or duplicated task, a migration that moves a currently-running task, double-counted migrations, or non-deterministic replay:

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

### 5b. Calibration grid (v4)

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

Each call takes one required argument, the workload key (`sys.argv[1]`); two optional ones exist for splitting a slow workload across background jobs (`sys.argv[2]` = penalty index 0/1/2, `sys.argv[3]` = first/second half of the 12 configs) but weren't needed for the real run above. **12 configs**: `queue_growth_threshold` in `{2, 4, 8}` x `arrival_rate_threshold` in `{0.8, 1.5}` x `combine` in `{"and", "or"}`. **9 grid workloads** (a subset of the 16 confirmation workloads — see §6). **Penalties** 0, 0.5, 2ms. **Seeds** 110000-110029 per workload (30 paired seeds, fresh — see §7); baseline runs once per (workload, penalty, seed) and is reused across all 12 configs. Rough estimate, not precisely measured for the real run (task count 40-640 depending on workload, ~1080 variant runs + 90 baseline runs per workload): single-digit to low-double-digit minutes per workload call, more for `bursty_high_s64`'s 640 tasks; the full 9-workload grid is a background-job-sized job, not a quick one.

### 5c. Selection: `recompute_harm.py` → `selected_config_v4.json`

```powershell
python task6_threshold_grid_recompute_harm.py
```

No arguments; reads the 9 `_v4_..._perseed.csv` files the step above wrote. This is the **authoritative** selection script (`task6_threshold_grid_analyze.py` also runs a harm check, but with an older, direction-blind significance test kept only as a report-only cross-check — see its module docstring and `docs/NOTEBOOK.md`'s `2026-09-27h` correction).

**Harm rule, per metric** (`p95_wait`, `avg_wait`, `avg_slowdown`), per (workload, penalty): harmful iff `harms > wins` (more seeds got worse than got better — not just "the mean moved," which a symmetric significance test can get backwards) **and** `sign_p(harms, n_eff) < 0.05` **and** `mean_variant > mean_baseline` (the mean actually moved the harmful direction too). A **config** is disqualified if it's harmful on *any* (workload, metric) at penalty **0 or 0.5** — 2ms is measured and reported in full but does **not** disqualify (it's a deliberately pessimistic stress test, not a realistic migration cost: real Linux's own `sysctl_sched_migration_cost` estimate is 0.5ms). Among the harm-free configs, the winner is the one with the largest mean `p95_wait` reduction on `stacked_medium` + `stacked_high`, averaged over penalties 0 and 0.5.

`selected_config_v4.json` contains: `selected` and `runner_up` (each `{config, queue_growth_threshold, arrival_rate_threshold, combine}`), `selected_mean_p95_pct`/`runner_up_mean_p95_pct` (the ranking score), `harm_free_configs` (every config that passed, not just the winner), and `rule_penalties` (`["0.0", "0.5"]` — which penalties disqualify). If no config is harm-free, `selected`/`runner_up` are `null` and every downstream script reports that plainly instead of crashing.

### 5d. Confirmation (v4)

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

Each call takes one required argument, the workload key. **16 workloads** (§6 — the 9 grid workloads plus `rate0.75_*`/`rate1.0_*` and `heavy_tail_high`, none of which the grid touched, so the grid's own selection is tested on workloads it never saw). **Fresh seeds** 120000-120029 per workload (never used by the grid or by any prior audit/diagnostic — see §7). **4 variants**, all run against the SAME paired baseline seeds:

- `selected_checked` — the winning config, `burst_idle_check=True`. The headline.
- `selected_unchecked` — the same thresholds, `burst_idle_check=False`. Isolates what the idle check itself is doing, holding the detector fixed.
- `runner_up_checked` — the runner-up config, WITH the mechanism (unlike v3's equivalent, which tested its runner-up without the mechanism — here the mechanism itself is under test, not just the threshold choice).
- `original_q2_a0.8_or_unchecked` — the pre-calibration detector (`queue_growth_threshold=2, arrival_rate_threshold=0.8, combine="or"`), no mechanism. A historical reference point: is the calibrated+mechanism combination actually better than where the project started?

`penalty_model="ran_only"` is applied to every one of these (baseline included) — it's a cost-model correction/assumption (§4's `LoadBalancer.py` row), not one of the four things being compared. Rough estimate, not precisely measured for the real run (4 variants + baseline, 3 penalties, 30 seeds each, per workload): comparable per-workload cost to the grid step above; the full 16-workload run is a multi-hour background job, not a quick one.

**Caveat, verified against the code, not assumed:** the 4 variants above are what this script explicitly *requests* (`_load_v4_variants()`, reading `queue_growth_threshold`/`arrival_rate_threshold`/`combine` from `selected_config_v4.json` and passing `burst_idle_check=True`, `penalty_model="ran_only"` by hand) — this is **not** what you get from `BurstAwareLoadBalancer(...)` with no keyword arguments. The bare class defaults are still the older `queue_growth_threshold=2, arrival_rate_threshold=1.5, combine="and"` (v1's own calibrated pick — §3), `burst_idle_check=False`, `penalty_model="all"` (`simulator/BurstDetector.py`, `simulator/BurstScheduler.py`). If you call the classes directly instead of going through this script, pass the kwargs above by hand.

### 5e. Analysis and tables

```powershell
python task6_confirmation_analyze.py
python task6_confirmation_tables.py
```

No arguments; both read every `results_task6_confirmation_v4_*_summary.csv` already on disk.

`task6_confirmation_analyze.py` prints/writes: the **MAIN TABLE** (baseline vs `selected_checked`, all 16 workloads, all 3 penalties → `_MAIN_TABLE.csv`); **RQ4** (extra scanning work vs p95 benefit, cost/benefit tradeoff, printed only); **SECONDARY** (all 4 variants' p95 change side by side, printed only — no selection happens here, that was already decided in §5c); and the arrival-rate-vs-p95 figure (`figure_p95wait_vs_arrival_rate_v4.png`, `selected_checked` vs `original_q2_a0.8_or_unchecked` across the 5 arrival rates, burst sizes 4 and 12).

`task6_confirmation_tables.py` writes: one `_TABLE_<variant>.csv` per non-headline variant (full per-penalty detail); the **COMPACT TABLE** (`_COMPACT_TABLE.{csv,md}` — one row per workload, grouped by whether `selected_checked` showed disqualifying harm / helped / was silent / fired without benefit); and, v4 only, **CHECK_EFFECT** (`_CHECK_EFFECT.{csv,md}` — `selected_checked` vs `selected_unchecked` compared *directly*, same seed/workload/penalty, not against baseline, isolating the idle check's own effect).

## 6. Workloads

| workload | how the burst arrives | size | rate/gap | what it tests |
|---|---|---|---|---|
| `stacked_low`/`_medium`/`_high` | **stacked**: every task in a burst is force-placed onto the SAME randomly-chosen core, bypassing placement entirely — the deliberately worst case for a balancer, since nothing at arrival time spreads the load | 4/12/30 | 0.5/1.5/3.75 tasks/ms | low/medium/high burst intensity, no arrival-rate/size confound |
| `bursty_high_s24` | **bursty**: every task in a burst shares one entry core but goes through NORMAL top-down placement (still allowed to spread) | 24 | 3.75 tasks/ms | a real, less-adversarial burst shape at moderate size |
| `bursty_high_s64` | bursty, same as above | 64 | 3.75 tasks/ms | `64 > 32` cores — the burst alone saturates the entire machine, so no core is ever idle while it's landing — this is why `burst_idle_check` mostly skips it (§3's v3→v4 row) |
| `heavy_tail_high` | no burst; heavy-tailed CPU-time distribution at high intensity | — | — | load imbalance from task-SIZE variance, not arrival clustering |
| `rate{0.5,0.75,1.0,1.5,3.0}_s{4,12}` | stacked (as above), varying arrival rate independently of size | 4 or 12 | 0.5-3.0 tasks/ms | isolates arrival rate from burst size — the calibration grid only ever saw `rate0.5`/`rate3.0`; `rate0.75`/`rate1.0`/`rate1.5` are confirmation-only, unseen by the selection |

Grid workloads (§5b) are the 9-workload subset: `stacked_{low,medium,high}`, `bursty_high_s24`, `bursty_high_s64`, `rate0.5_s4`, `rate0.5_s12`, `rate3.0_s4`, `rate3.0_s12`. Confirmation (§5d) adds `heavy_tail_high` and the three untested rates (`0.75`, `1.0`, `1.5`) at both sizes, for 16 total.

## 7. Seed ranges

Every range used in this project's history, taken from `docs/NOTEBOOK.md`'s dated entries — each pipeline stage always gets a fresh range, never reused:

| range | used for |
|---|---|
| 10000+ | calibration grid, v1 (original detector thresholds, pre-fidelity-fixes) |
| 20000+ | original confirmation run, v1 |
| 30000+ | Task 7: periodic-checker-election audit |
| 40000+ | confirmation re-run, v2 (six baseline fidelity fixes made default) — **not actually run**; the v2 grid alone was already harmful, so confirmation was correctly skipped |
| 50000+ | Task 8 fidelity-audit's own measurement scripts (`development/fidelity_audit/`) |
| 60000+ | Task 8 Step 2's baseline-only ablation (`development/fidelity_audit/task8_step2_baseline_ablation.py`) — the number 60000 is *also*, separately, the seed **offset** v3 applied to the 10000/20000 bases (giving 70000/80000 below); same digits, unrelated origin |
| 70000+ | calibration grid, v3 (`burst_gap_gate`, the per-domain gap check + least-loaded destination) |
| 80000+ | confirmation, v3 |
| 90000+ | Task 8b / `tests/test_invariants.py` (fixed `SEED_BASE`, not an offset) |
| 95000+ | Task 9 diagnostic (`development/task9_gap_gate_diagnostic/`) |
| 96000+ | Task 10a diagnostic (`development/task10_check_mechanism/`) |
| 110000+ | calibration grid, v4 (`burst_idle_check`, the current confirmed mechanism) |
| 120000+ | confirmation, v4 |

## 8. How to read an output line

A real line from the v4 confirmation run (`results_task6_confirmation_v4_stacked_high_summary.csv`, reconstructed through `task6_confirmation_run.py`'s own print format):

```
stacked_high     penalty=0.5ms selected_checked       p95_wait: 24.523->18.448 (-24.8%) sign_p=<0.0001 harm=False fires=151.4 cores_scanned_pct=+10.9%
```

- `stacked_high` / `penalty=0.5ms` / `selected_checked` — which workload, penalty, and variant this row is.
- `p95_wait: 24.523->18.448` — the 95th-percentile wait time, in simulated ms, averaged across the 30 paired seeds: baseline (`24.523`) then this variant (`18.448`).
- `(-24.8%)` — the percent change, variant vs baseline.
- `sign_p=<0.0001` — the exact two-sided binomial sign test on the 30 paired per-seed differences (`paired_compare.sign_test_p`). With n=30 non-tied seeds, `p<0.0001` requires at least **26 of 30** moving the same direction (computed directly: `sign_test_p(25, 30) = 0.000325`, just above the threshold; `sign_test_p(26, 30) = 0.0000595`, just under it) — it does *not* mean literally every seed agreed. 30/30 would give `p ≈ 1.86e-9`.
- `harm=False` — this workload/penalty's overall verdict: did *any* of the SIX metrics the confirmation run checks (`p95_wait`, `p99_wait`, `avg_wait`, `avg_slowdown`, `p95_slowdown`, `makespan_excess` — more than the grid's own 3-metric harm rule in §5c, `task6_confirmation_run.py`'s own `METRICS` list) trip the same `harms>wins` rule?
- `fires=151.4` — mean number of times the detector fired per run (before the idle check even runs — see `docs/NOTEBOOK.md`'s `2026-09-30g` entry for why a high fire count doesn't always mean a high walk count).
- `cores_scanned_pct=+10.9%` — how much more periodic-balancer scanning work this variant did vs baseline, the mechanism's "cost" side of the cost/benefit tradeoff (§5e's RQ4).

## 9. Known limitations and simplifications

From `docs/FIDELITY_AUDIT.md`'s 13-area comparison against Linux v7.2, plus two verified directly from the code:

- **No NOHZ / idle load balancer.** Real Linux stops ticking idle CPUs and periodically kicks ONE idle CPU to balance on behalf of all of them in a single batched pass; this simulator's `periodic_ticker()` checks every core every tick regardless of idle state — more frequent, not batched. Not independently measured (`FIDELITY_AUDIT.md` §2): modeling it correctly needs a new mechanism, not a toggle.
- **No newly-idle cost budget.** Real `sched_balance_newidle()` is gated on `avg_idle < max_newidle_lb_cost` and can bail partway up the domain chain once its self-measured cost budget runs out; this simulator's default `newidle_mode="transition"` always makes exactly one unconditional attempt per busy→idle edge, with no cost accounting at all (`FIDELITY_AUDIT.md` §9). What it does correctly: it climbs the full domain chain, one attempt per level, same as real Linux's per-level loop — the earlier characterization of it as a "single-level probe" was wrong and corrected in the audit.
- **Three-type group classification, not eight.** `classify_group()` uses `HAS_SPARE`/`FULLY_BUSY`/`OVERLOADED`; real `group_classify()` has eight types in a strict priority order. The five missing types need mechanisms this simulator doesn't model at all (heterogeneous-capacity cores, CPU affinity masks, SMT-aware packing, LLC-aware balancing) — a genuinely inapplicable gap here, not a hidden bias (`FIDELITY_AUDIT.md` §4).
- **No sleeping/blocking tasks.** Verified directly in `Task.py`/`Core.py`, not itemized by name in the audit doc: every task is CPU-bound from arrival to completion (`remaining_time` only ever counts down); there's no blocked/sleeping state, no wakeup, nothing analogous to real Linux tasks that block on I/O or locks and get re-woken elsewhere. This directly limits how much the wake-affine/idle-balancing dynamics this simulator can show resemble a real, mixed CPU/IO workload.
- **A flat migration-cost assumption, not one of the audit's six fixes.** Verified directly in `LoadBalancer._do_migrate()`: `migration_penalty` is a single scalar added to a migrated task's remaining time, independent of cache locality, NUMA distance, or task size. This isn't a fidelity gap against a specific Linux mechanism the way the six fixes are — real Linux has no migration-penalty *parameter* at all; the cost comes from hardware (cache misses, memory distance) and can't be simulated directly, so this is a deliberate stand-in. `penalty_model="ran_only"` (used throughout v4) at least restricts the charge to tasks that have actually run before, consistent with the kernel's own `task_hot()` treating a never-run task as not cache-hot — but the charge itself is still flat once applied (§4's `LoadBalancer.py` row has the full explanation).
- **The `_s4` blind spot (this project's own finding, not from the audit).** `selected_checked`'s `queue_growth_threshold=8` cannot ever fire on a 4-task burst: growth of 8 within the detector's window is arithmetically impossible when only 4 tasks arrive. Confirmed directly: `detector_fires=0.0` on every `rate*_s4` workload in the v4 confirmation. Not a bug — a real, known coverage gap in this specific calibrated configuration, traded off against the harm-free guarantee the calibration grid was selecting for (§3).

---
*Full dated history, every hypothesis and correction as it actually happened: `docs/NOTEBOOK.md`. Kernel-fidelity comparison: `docs/FIDELITY_AUDIT.md`.*
