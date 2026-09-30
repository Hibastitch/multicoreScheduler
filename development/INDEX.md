# development/ — diagnosis and verification work behind the final code

Real, correct findings — just not the final paper numbers (those are in `final_results/`).

## topology_audit/ — Fix D: periodic-checker election vs. real should_we_balance()

- `task7_checker_election_audit.py` — STEP 1, measures BEFORE (no code change): elects a checker the current ("legacy") way, then asks whether that checker actually climbs the domain it was elected for. Baseline + burst-aware, 4 workloads, 10 seeds (30000+), penalty=0.
- `checker_audit_perseed.csv` / `_summary.csv` — its output: onehop/node23-30% and machine ~23-29% of periodic checks (per node/onehop, up to 70% under heavy load on a single workload) elect a checker that never asks the question itself — the pass is structurally, silently skipped. Pair and node levels: 0% (no span overlap there).
- `task7_checker_election_after.py` — STEP 3, re-measures with the new `checker_model="kernel"` flag (`simulator/LoadBalancer.py`, opt-in, default stays `"legacy"`) on the same seeds, plus a paired legacy-vs-kernel comparison (migrations, p95_wait, cores scanned), baseline only.
- `checker_audit_after_perseed.csv`, `checker_model_compare_perseed.csv` — its output: 0% invalid at every level under `checker_model="kernel"`.

## rng_isolation/ — Fix 3d: paired-seed workload isolation

- `control3c_legacy_check.py` — control run confirming the pre-fix RNG contamination was real.
- `control3c_v2_fixed_rng.py` — same check after the fix, confirming paired seeds hold.
- `results_control3c_legacy.csv` — output of `control3c_legacy_check.py`.

## newidle/ — diagnosing and fixing the newidle self-suppression bug

- `task3_newidle_trace.py` — traces `newidle_cost_avg`'s EMA decaying to near-zero during quiet stretches.
- `task3b_transition_check.py` — verifies `newidle_mode="transition"` fires once per busy→idle edge, no self-suppression.
- `results_task3b_transition.csv` — output of `task3b_transition_check.py`.
- `task6prep_idle_balance_trace.py` — early idle/balance tracing that fed into the Fix A investigation below.

## fixA_timers/ — the per-CPU balance timer bug

- `task6_why_periodic_stalls.py` — the direct trace that found the checker-stealing bug (core 30 stealing core 31's timer on every one of 178 checks); motivated `per_cpu_last_balance`.

## fixB_fixC/ — queue-aware load signal and the real kernel `calculate_imbalance()`

- `task6_kernel_compare_diagnose.py` — diagnoses the queue-blind `load_avg`/`util_avg` conflation.
- `task6_load_signal_diagnose.py` — further diagnosis of the same signal gap.
- `task6_verify_fixes.py` — verifies Fix A + Fix B together.
- `task6_verify_fixC.py` — verifies Fix C (`imbalance_model="kernel"`) specifically.
- `task6_check_max_pass.py` — verifies `MAX_MIGRATE_PER_PASS` behavior against the kernel-cited constant.

## low_intensity_reversal/ — why burst-aware can hurt at low intensity

- `task6_low_intensity_reversal.py` — the original hypothesis test finding burst-aware worse at low intensity.
- `task6_verify_timer_hypothesis.py` — tests whether `burst_resets_timer` explains the reversal.
- `task6_verify_timer_attempts_v2.py` — corrected attempt-trace (identifies the checker/domain directly from the burst-tagged call — see `superseded/INDEX.md` for the v1 bug this fixes).
- `task6_ablation_burst_resets_timer.py` — the pre-registered 3-way ablation (baseline / resets=True / resets=False) that set the `burst_resets_timer=False` default.
- `results_task6_ablation_burst_resets_timer.csv`, `task6_ablation_output.txt` — outputs of the ablation script.

## instrumentation_checks/ — verifying the diagnostics don't perturb the simulation

- `task4_observer_effect_check.py` — confirms `diagnostics.py`'s live samplers don't change scheduling behavior.
- `task4_verify_one_seed.py` — single-seed sanity check of the diagnostics/metrics pipeline.

## fidelity_audit/ — Task 8 pre-audit: 13-area Linux-fidelity comparison

Real, correct findings, but strictly an AUDIT — no simulator logic was changed here.
See `docs/FIDELITY_AUDIT.md` for the full report; this folder holds only the
cheap, monkeypatch-based impact measurements for the mismatches that were
togglable without editing simulator files.

- `task8_pre_audit_impact_measurements.py` — three monkeypatch toggles (each
  measured independently against the current default, `checker_model="kernel"`
  held constant): busy_factor (periodic-check interval x16 when the checking
  core is busy, `fair.c` `get_sd_balance_interval()`), the `!idle` out_balanced
  gate (`sched_balance_find_src_group()`), and the corrected
  `NUMA_IMBALANCE_MIN`/`NUMA_DST_BUSY_THRESHOLD` constants (`adjust_numa_
  imbalance()`). 10 seeds (50000+), `stacked_high` + `bursty_high_s64`,
  baseline only.
- `results_task8_pre_audit_impact.csv` — its output. Headline: the missing
  busy_factor scaling is large and highly significant (+26% to +48% p95_wait
  when corrected); the idle-gate and NUMA-constant fixes both measured zero
  effect at these two high-intensity workloads.
- `task8_step4_cache_hot_scope.py` — Step 4 of the 2026-09-29 corrections
  pass: measures what fraction of this sim's migrations real Linux's
  `task_hot()` would refuse as cache-hot, using a new TRACKING-ONLY
  `Task.last_ran_until` field (`simulator/Task.py`/`Core.py`, verified
  byte-identical decisions before/after via a fingerprint hash — the one
  simulator-file edit in this whole audit, explicitly authorized). Baseline +
  burst-aware, 5 workloads, 10 seeds (50000+).
- `results_task8_step4_cache_hot_scope.csv`, `_by_trigger.csv` — its output.
  Headline: only 2.8%-10.1% of migrations would actually be refused (most are
  either already-cold or never-run — real Linux's cache-hot check only ever
  blocks tasks that have ACTUALLY run recently), a much smaller scope than
  originally assumed in `docs/FIDELITY_AUDIT.md`'s first pass.

- `task8_step2_baseline_ablation.py` — Task 8 Step 2: baseline-only ablation
  of the 6 fidelity fixes (checker_model, busy_factor, placement_root,
  cache_hot, numa_fix, time_slice), each alone and all six together, vs
  all-legacy. LoadBalancer only (burst-aware not run), 10 seeds (60000+),
  5 workloads. Headline table: `docs/FIDELITY_AUDIT.md` §15.
- `results_task8_step2_ablation_perseed.csv`, `_summary.csv` — its output.

## task9_gap_gate_diagnostic/ — what the gap gate is actually doing on the v3 confirmation harm cell

Diagnostic only (2026-09-30, see `docs/NOTEBOOK.md`'s entry of the same
date for the full writeup) -- no rule changed, no simulator file
edited. Answers why `selected_gated` shows disqualifying harm on
`bursty_high_s64`/penalty=0 (`avg_wait`) in the v3 confirmation but not
on `stacked_high`.

- `task9_gap_gate_diagnostic.py` — 5 fresh seeds (95000+/95100+, never
  used elsewhere) per (workload, config, penalty); monkeypatches
  `LoadBalancer._do_migrate` at the class level for the duration of
  each run only (tag + `task.last_ran_until` observed, original
  delegated to unchanged) to get a tag/already-ran breakdown no saved
  CSV has. `selected_gated` vs `selected_ungated`, `bursty_high_s64`
  vs `stacked_high`, penalties 0/0.5/2.
- `results_task9_gap_gate_diagnostic_summary.csv`, `_perseed.csv` — its
  output. Headline: the gate blocks few domains on either workload
  (~4%); the real driver is Task 9a's `ran_only` exposure -- 82-90% of
  `bursty_high_s64`'s burst-tagged migrations move an already-run
  (warm) task (penalty charged), vs only 18-24% on `stacked_high`. The
  gate does not reduce the `bursty_high_s64` harm (confirmation CSVs:
  gated avg_wait +3.67%/significant vs ungated +0.22%/not significant,
  penalty=0) -- if anything it makes this cell WORSE, moving more
  already-warm tasks per triggered domain via its least-loaded-core
  destination choice.

## task10_check_mechanism/ — Task 10a: burst-gate mechanism check (hypothetical machine-wide checks)

Diagnostic only (2026-09-30, see `docs/NOTEBOOK.md`'s "2026-09-30b"
entry for the full writeup) -- no simulator file edited, no rule/
selection/default changed. Reports ONLY decision counts and machine
state, never a performance/harm statistic.

- `task10_check_mechanism.py` — at every burst trigger that passes
  cooldown, before its domain_chain walk, evaluates 4 HYPOTHETICAL
  machine-wide checks (C1: some core `nr_running <= burst core's - 2`;
  C2: same with `-3`; C3: any core idle; C4: C3 OR C2) against the real
  machine state at that instant, and records how many of that
  trigger's burst-tagged migrations each check would have blocked --
  none of these checks is ever actually installed (`burst_gap_gate=
  False` throughout; this is not Task 9b's per-domain gate). Splice
  technique: a line-for-line copy of `BurstScheduler.on_task_placed`
  with two additive read-only insertions, monkeypatched in for one run
  and restored after. 5 fresh seeds x 5 workloads (96000+, 100 apart
  per workload), q4_a1.5_and (the v3 selected config), `penalty_model=
  "ran_only"`, `migration_penalty=0`.
- `results_task10_check_mechanism_summary.csv`, `_perseed.csv` — its
  output (401 total per-trigger records). Headline: C3 (the idle-core
  check) blocks 99.4% of `bursty_high_s64`'s triggers (removing 603/606
  burst migrations) while blocking 0% on `stacked_medium`/`rate1.5_s12`/
  `rate3.0_s12` and only 18.5% on `stacked_high` -- C1/C2/C4 barely
  block anywhere (C2 tops out at 4.9%). By trigger time, `bursty_high_
  s64` (burst_size=64) has NO idle core machine-wide in 99.4% of cases;
  the smaller-burst workloads still have idle capacity most or all of
  the time -- offering a candidate mechanism, distinct from max_gap
  (which never discriminates: C1 never blocks anywhere).

## sweeps_precalibration/ — burst-size / interval sweeps before the detector was calibrated

- `task6_sweeps_v2.py` — corrected-duration burst-size and inter-burst-interval sweeps.
- `task6_plot_sweeps_v2.py` — plots the two sweeps above.
- `results_task6_sweepA_burstsize_v2.csv`, `results_task6_sweepB_interval_v2.csv` — sweep data.
- `task6_sweepA_burstsize_v2.png`, `task6_sweepB_interval_v2.png` — sweep plots.
- `task6_sweeps_v2_output.txt` — captured stdout from `task6_sweeps_v2.py`.
- `task6_bursty_sweep_fixed.py` — tie-tolerance-corrected re-run of the `bursty` burst-size question, with per-seed CSV.
- `results_task6_bursty_sweep_fixed_perseed.csv`, `results_task6_bursty_sweep_fixed_summary.csv` — its output.
- `task6_bursty_sweep_fixed_output.txt` — captured stdout.
- `stress_test_bursty.py` — larger-n stress test of the same question.

## idle_check_cost/ — measuring burst_idle_check's own scan cost (2026-10-01)

Measurement only, see `docs/NOTEBOOK.md`'s `2026-10-01` entry for the full writeup -- no
rule/selection/default changed. `simulator/BurstScheduler.py`'s idle-check scan was never
counted in `sched_cores_scanned`; two new balancer counters (`idle_check_runs`,
`idle_check_cores_read`) make it measurable without changing behavior (the `any(...)` call
was replaced by an equivalent explicit loop, same iteration order, same short-circuit,
boolean result identical -- `tests/test_invariants.py` re-run after, all pass, result CSVs
byte-identical to before the change).

- `measure_idle_check_cost.py` — re-runs the EXACT (workload, penalty, seed) triples behind
  the published v4 confirmation (seeds 120000+, `selected_config_v4.json`'s `q8_a1.5_or`) on
  5 workloads x 3 penalties; SAFETY CHECK compares every recomputed `p95_wait`/`avg_wait`/
  `total_migrations` against the committed per-seed CSVs before writing anything (2700/2700
  matched exactly) -- these are confirmed to be the identical runs behind the published
  numbers, not a resample.
- `results_idle_check_cost.csv`, `.md` — its output. Headline: `idle_check_cores_read` mean
  ranges 11.2-1084.6/run across the 15 cells measured; as a %% of that same run's baseline
  `sched_cores_scanned`, the idle check adds 0.03%-1.91% -- small everywhere measured, largest
  on `bursty_high_s64` (mean cores/check 31.85-31.93, essentially the whole machine scanned
  almost every time), the same workload where the mechanism itself is closest to saturated
  and least effective (2026-09-30g).
