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
