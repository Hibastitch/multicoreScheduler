# multicoreScheduler

A SimPy discrete-event simulator of Linux's EEVDF/CFS scheduler and its
load-balancing pipeline (periodic, newly-idle, and placement) across a
32-core / 4-node machine. Research question: can a new, observable-signal
"a burst just landed here" trigger react faster than the kernel's own
periodic/newly-idle rebalancing — and can it do that without hurting
workloads it wasn't designed for? See `docs/NOTEBOOK.md` for the full,
dated investigation; this file is only the map and the final answer.

## Final configuration (the code's current defaults)

- **Baseline = A+B+C**: `per_cpu_last_balance=True` (per-CPU balance
  timers, not a shared one), `load_model="runnable"` (queue-aware PELT
  load, not queue-blind), `imbalance_model="kernel"` (the real 3-way
  `calculate_imbalance()`), `newidle_mode="transition"` (fires once on
  the busy→idle edge, not a polling EMA).
- **Detector = `q2_a1.5_and`**: `queue_growth_threshold=2`,
  `arrival_rate_threshold=1.5`, `combine="and"`; `burst_resets_timer=False`.
- **Fidelity fixes 1-6 (2026-09-29, Task 8, see `docs/FIDELITY_AUDIT.md`
  and `docs/NOTEBOOK.md`'s 2026-09-29c/d/e entries)**: `checker_model=
  "kernel"` (real `should_we_balance()` election), `busy_factor=16`
  (real `get_sd_balance_interval()` busy-CPU interval scaling),
  `placement_root="own"` (fork-path placement anchored to the entry
  core's own node, not a fixed node-0 root), `cache_hot=True` (real
  `task_hot()`/`can_migrate_task()` cache-hot migration refusal),
  `numa_fix=True` (corrected `NUMA_IMBALANCE_MIN`/`imb_numa_nr`
  constants, and `adjust_numa_imbalance()` no longer applied outside its
  real call site), `time_slice=2.8` (the EFFECTIVE, boot-scaled
  `sysctl_sched_base_slice` for a >=8-CPU machine, not the raw 0.7ms
  default). Baseline-only ablation of all six, individually and
  together: `docs/FIDELITY_AUDIT.md` §15.
- All of the above are now the plain constructor/`run_simulation()`
  defaults in `simulator/` — no kwargs needed to get the final
  configuration; every old (pre-2026-09-27i, and separately
  pre-2026-09-29 for the six fidelity fixes) value is still reachable by
  passing it explicitly (see `simulator/LoadBalancer.py`/
  `BurstDetector.py`/`Main.py`'s `run_simulation()`).

## Folder guide

- **`simulator/`** — the simulator itself. Nothing here reads or writes
  a result file; it's a library, called by everything below.
- **`final_results/`** — **everything the paper cites**, and the only
  folder that is. `1_calibration_grid/` (12-config detector threshold
  search) and `2_confirmation/` (the 16-workload final confirmation run
  and its tables/plot) — each self-contained: scripts and the CSVs they
  read/write live together.
- **`development/`** — the diagnosis and verification work that led to
  the final code (RNG isolation, the newidle self-suppression fix, the
  per-CPU timer bug, Fix B/C, the low-intensity-harm mechanism, and the
  sweeps that predate the calibration grid). Real, correct findings —
  just not the final numbers.
- **`superseded/`** — results generated under buggy or since-replaced
  code/config. **Never cite these.** See `superseded/INDEX.md` for why
  each one is invalid.
- **`docs/NOTEBOOK.md`** — the dated lab notebook: every finding, bug,
  fix, and correction, in the order they happened, including retractions.
- `Experiment.py` and a bare `results.csv` are NOT how the paper's
  numbers were produced — `Experiment.py` is a general-purpose 10-rep
  grid harness in `simulator/`, useful for exploration, but every number
  in the paper comes from a `final_results/` script run at `n=30`
  instead.

## How to reproduce

Run each from its own directory (scripts and their CSVs are co-located):

```
cd final_results/1_calibration_grid
python task6_threshold_grid.py <workload_key>   # seeds 10000+, n=30 per workload
python task6_threshold_grid_analyze.py          # merges all workloads, no new sims
python task6_threshold_grid_recompute_harm.py   # corrected-harm-rule recheck, no new sims

cd final_results/2_confirmation
python task6_confirmation_run.py <workload_key> # seeds 20000-21529, n=30 per workload
python task6_confirmation_analyze.py            # merges + plots, no new sims
python task6_confirmation_tables.py             # paper-ready tables, no new sims
```

`<workload_key>` is one of the workload names printed by each script when
run with no arguments (e.g. `stacked_medium`, `rate1.5_s12`).

## Full history

`docs/NOTEBOOK.md` — every dated finding, bug, fix, and correction.
