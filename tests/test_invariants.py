"""
Task 8b (2026-09-29), extended by Task 9 Step 3 (2026-09-29): automated
invariant tests.

Task 9 extension: the full matrix below now also runs under
penalty_model="ran_only" + burst_gap_gate=True (Task 9's two new flags,
docs/NOTEBOOK.md 2026-09-29h), alongside the original penalty_model=
"all" + burst_gap_gate=False (legacy) config -- CONFIGS below. Invariant
3 (work accounting) no longer assumes every migration charges the
penalty when migration_penalty!=0 (true only under "all") -- it now
reads LoadBalancer.penalty_charges (the new per-run counter Task 9 Step
2 added) as the count of migrations that actually charged, which is
correct under both "all" and "ran_only".

Task 10 extension (2026-09-30, docs/NOTEBOOK.md 2026-09-30d): a third
config, penalty_model="ran_only" + burst_idle_check=True (burst_gap_gate
=False), added to CONFIGS/run_instrumented(). New invariant 11 checks
the two new per-run counters Task 10 Step 2 added
(BurstAwareLoadBalancer.idle_check_skipped_triggers, LoadBalancer.
burst_migrations_dst_busy) for internal consistency, and that
idle_check_skipped_triggers stays exactly 0 whenever burst_idle_check
is False (the flag has zero effect when off, matching Step 2's
fingerprint verification).

Catches plain PROGRAMMING bugs (a task lost or duplicated, a migration
that moves a currently-running task, double-counted migrations, a
non-deterministic replay) -- NOT Linux-fidelity questions. Fidelity
(how closely this simulator matches real Linux) is docs/FIDELITY_AUDIT.md's
job; this file only checks that the simulator's OWN bookkeeping is
internally consistent, regardless of whether it matches Linux.

NO simulator file is edited or behaviorally changed. Every piece of
instrumentation below (Core.enqueue, Core.tick_load, LoadBalancer.
_do_migrate) is monkeypatched onto the class for the duration of one
run only, and restored in a `finally` block immediately after -- see
`run_instrumented()`. The one new process this file adds
(`location_sampler`, registered via `run_simulation()`'s existing
`extra_processes` hook, the same mechanism `diagnostics.py`'s live
samplers already use) only reads state and calls `env.timeout()` --
it was already independently confirmed non-perturbing for this exact
mechanism by an earlier session's `development/instrumentation_checks/
task4_observer_effect_check.py`.

If ANY invariant fails: THIS SCRIPT ONLY REPORTS IT (workload,
scheduler, penalty, seed, exact violation) and exits non-zero. It must
never be used to "fix" the simulator -- a failure here is a stop-and-
report event, not a todo list.

All 10 invariants below were checkable with monkeypatch/wrapper
instrumentation alone; none required editing any simulator/ file.
(Nothing to list under "can't be checked without editing simulator/".)

Run:
    cd tests && python test_invariants.py          # always works
    cd tests && python -m pytest -q                 # if pytest is installed
"""

import sys
import pathlib
import csv
import hashlib
import json
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "simulator"))

from Main import run_simulation
from Core import Core
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
import paired_compare

N_SEEDS = 3
SEED_BASE = 90000
PENALTIES = [0.0, 2.0]
SCHEDULERS = {"baseline": LoadBalancer, "burst_aware": BurstAwareLoadBalancer}

# Task 9 Step 3 / Task 10 extension: sweep the legacy config, Task 9's
# two flags together, and Task 10's new flag, through the SAME full
# matrix. (penalty_model, burst_gap_gate, burst_idle_check) --
# burst_gap_gate/burst_idle_check only actually apply to
# BurstAwareLoadBalancer (run_instrumented() omits both from
# LoadBalancer's kwargs, which accepts neither).
CONFIGS = [("all", False, False), ("ran_only", True, False), ("ran_only", False, True)]

# Canonical definitions, matching final_results/2_confirmation/task6_confirmation_run.py
# where a workload name is shared with it; "uniform"/"mixed" aren't tested
# there, so use Main.py's own demo defaults (medium intensity, n_tasks=200).
WORKLOADS = {
    "stacked_low":     dict(profile="stacked_burst", intensity="low", overrides=None, n_tasks=200),
    "stacked_high":    dict(profile="stacked_burst", intensity="high", overrides=None, n_tasks=200),
    "rate3.0_s12":     dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 12, "arrival_rate_during_burst": 3.0,
                                        "burst_duration": 12 / 3.0, "inter_burst_interval": 60},
                             n_tasks=120),
    "bursty_high_s24": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 24, "burst_duration": 24 / 3.75}, n_tasks=240),
    "bursty_high_s64": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 64, "burst_duration": 64 / 3.75}, n_tasks=640),
    "heavy_tail_high": dict(profile="heavy_tail", intensity="high", overrides=None, n_tasks=200),
    "uniform":         dict(profile="uniform", intensity="medium", overrides=None, n_tasks=200),
    "mixed":           dict(profile="mixed", intensity="medium", overrides=None, n_tasks=200),
}

# profile="stacked_burst" workloads only -- invariant 9 (direct_core
# placement) is scoped to these; every other profile leaves direct_core
# unset (None) for every task, so the check is vacuous there.
STACKED_WORKLOADS = {"stacked_low", "stacked_high", "rate3.0_s12"}

TIE_TOLERANCE = 1e-6


# ==================================================================
# Instrumentation: monkeypatch/wrap only, restored after every run.
# ==================================================================

class RunContext:
    def __init__(self, workload, scheduler_name, penalty, seed, penalty_model, burst_gap_gate,
                 burst_idle_check):
        self.workload = workload
        self.scheduler_name = scheduler_name
        self.penalty = penalty
        self.seed = seed
        self.penalty_model = penalty_model
        self.burst_gap_gate = burst_gap_gate
        self.burst_idle_check = burst_idle_check
        self.all_tasks = {}        # task_id -> Task, registered at first Core.enqueue()
        self.executed_time = {}    # core_id -> accumulated dt while current_task is not None
        self.location_violations = []
        self.migrate_violations = []


def run_instrumented(workload_key, wl, scheduler_cls, scheduler_name, penalty, seed,
                      penalty_model="all", burst_gap_gate=False, burst_idle_check=False):
    ctx = RunContext(workload_key, scheduler_name, penalty, seed, penalty_model, burst_gap_gate,
                      burst_idle_check)

    orig_enqueue = Core.enqueue
    orig_tick_load = Core.tick_load
    orig_do_migrate = LoadBalancer._do_migrate

    def patched_enqueue(self, task, vruntime_baseline=None):
        # Invariant 4's registry: every task passes through Core.enqueue()
        # exactly once, at its first placement (see Core.enqueue's own
        # docstring -- migrations/preemption-requeues append to rq
        # directly, bypassing this method).
        ctx.all_tasks[task.task_id] = task
        return orig_enqueue(self, task, vruntime_baseline=vruntime_baseline)

    def patched_tick_load(self, dt):
        # Invariant 3: independently accumulate "time actually spent
        # executing a task" from the OUTSIDE, not by reusing
        # Core.total_busy_time (which is the simulator's OWN accounting
        # of the same thing -- reusing it wouldn't catch a bug in it).
        if self.current_task is not None:
            ctx.executed_time[self.core_id] = ctx.executed_time.get(self.core_id, 0.0) + dt
        return orig_tick_load(self, dt)

    def patched_do_migrate(self, task, src_core, dst_core, now, tag="periodic"):
        # Invariant 5: check the PRE-condition before the original call
        # removes the task from src_core.rq -- read-only, changes nothing.
        if task not in src_core.rq:
            ctx.migrate_violations.append(
                f"seed={seed} t={now:.3f} tag={tag}: migrated task {task.task_id} "
                f"was NOT in src_core {src_core.core_id}'s rq"
            )
        if src_core.current_task is task:
            ctx.migrate_violations.append(
                f"seed={seed} t={now:.3f} tag={tag}: migrated task {task.task_id} "
                f"WAS src_core {src_core.core_id}'s currently-running task"
            )
        return orig_do_migrate(self, task, src_core, dst_core, now, tag)

    def location_sampler(env, cores, balancer):
        # Invariant 4, sampled every 1 sim-ms via the same extra_processes
        # hook diagnostics.py's own live samplers use (already confirmed
        # non-perturbing for this exact mechanism, development/
        # instrumentation_checks/task4_observer_effect_check.py).
        while True:
            yield env.timeout(1)
            locations = {}
            for c in cores:
                if c.current_task is not None:
                    locations.setdefault(c.current_task.task_id, []).append((c.core_id, "running"))
                for t in c.rq:
                    locations.setdefault(t.task_id, []).append((c.core_id, "queued"))
            for tid, locs in locations.items():
                if len(locs) > 1:
                    ctx.location_violations.append(
                        f"seed={seed} t={env.now}: task {tid} found in {len(locs)} places: {locs}"
                    )
            for tid, t in ctx.all_tasks.items():
                if t.finish_time is None and t.arrival_time <= env.now and tid not in locations:
                    ctx.location_violations.append(
                        f"seed={seed} t={env.now}: live task {tid} (arrived {t.arrival_time}) "
                        f"found in ZERO places"
                    )

    Core.enqueue = patched_enqueue
    Core.tick_load = patched_tick_load
    LoadBalancer._do_migrate = patched_do_migrate
    try:
        balancer_kwargs = dict(migration_penalty=penalty, penalty_model=penalty_model)
        if scheduler_cls is BurstAwareLoadBalancer:
            balancer_kwargs["burst_gap_gate"] = burst_gap_gate
            balancer_kwargs["burst_idle_check"] = burst_idle_check
        m, b, gt, migs, logger, plan = run_simulation(
            wl["profile"], scheduler_cls, intensity_level=wl["intensity"], seed=seed,
            balancer_kwargs=balancer_kwargs,
            intensity_overrides=wl["overrides"], n_tasks=wl["n_tasks"],
            extra_processes=[location_sampler],
        )
    finally:
        Core.enqueue = orig_enqueue
        Core.tick_load = orig_tick_load
        LoadBalancer._do_migrate = orig_do_migrate

    ctx.metrics = m
    ctx.balancer = b
    ctx.ground_truth = gt
    ctx.migration_events = migs
    ctx.logger = logger
    ctx.plan = plan
    return ctx


def event_log_hash(logger):
    blob = json.dumps(logger.events, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def metrics_dict(ctx):
    return ctx.metrics.summary(balancer=ctx.balancer, ground_truth_bursts=ctx.ground_truth,
                                migration_events=ctx.migration_events)


# ==================================================================
# Invariant checks. Each returns (status, [violation strings]);
# status is True/False, or None for "not applicable to this run".
# ==================================================================

def check_1_conservation(ctx):
    v = []
    cores = ctx.metrics.cores
    completed = [t for c in cores for t in c.completed_task_list]
    completed_ids = [t.task_id for t in completed]
    plan_ids = [e["task_id"] for e in ctx.plan]

    if sorted(completed_ids) != sorted(plan_ids):
        missing = sorted(set(plan_ids) - set(completed_ids))
        extra = sorted(set(completed_ids) - set(plan_ids))
        if missing:
            v.append(f"seed={ctx.seed}: {len(missing)} plan task_id(s) never completed: {missing[:20]}")
        if extra:
            v.append(f"seed={ctx.seed}: {len(extra)} completed task_id(s) not in plan: {extra[:20]}")
    if len(completed_ids) != len(set(completed_ids)):
        dupes = sorted({tid for tid in completed_ids if completed_ids.count(tid) > 1})
        v.append(f"seed={ctx.seed}: duplicate completions for task_id(s) {dupes[:20]}")
    for c in cores:
        if c.current_task is not None:
            v.append(f"seed={ctx.seed}: core {c.core_id} still running task "
                      f"{c.current_task.task_id} at run end")
        if len(c.rq) > 0:
            queued_ids = [t.task_id for t in c.rq]
            v.append(f"seed={ctx.seed}: core {c.core_id} still has {len(c.rq)} "
                      f"task(s) queued at run end: {queued_ids}")
    return (len(v) == 0, v)


def check_2_timing(ctx):
    v = []
    cores = ctx.metrics.cores
    for c in cores:
        for t in c.completed_task_list:
            if t.start_time < t.arrival_time - TIE_TOLERANCE:
                v.append(f"seed={ctx.seed}: task {t.task_id} start={t.start_time} "
                          f"< arrival={t.arrival_time}")
            turnaround = t.finish_time - t.arrival_time
            if turnaround < t.cpu_time - 1e-6:
                v.append(f"seed={ctx.seed}: task {t.task_id} turnaround={turnaround} "
                          f"< cpu_time={t.cpu_time}")
            wait = t.start_time - t.arrival_time
            if wait < -TIE_TOLERANCE:
                v.append(f"seed={ctx.seed}: task {t.task_id} negative wait={wait}")
    return (len(v) == 0, v)


def check_3_work_accounting(ctx):
    # TASK 9 STEP 3: under penalty_model="all" EVERY migration charges
    # when penalty!=0, so len(migration_events) and
    # balancer.penalty_charges agree; under "ran_only" only SOME do, so
    # this must read the actual count of charges LoadBalancer._do_migrate
    # applied (the new penalty_charges counter, Task 9 Step 2) rather
    # than assume every migration charged.
    v = []
    executed_total = sum(ctx.executed_time.values())
    charged_migrations = ctx.balancer.penalty_charges
    expected = sum(e["cpu_time"] for e in ctx.plan) + ctx.penalty * charged_migrations
    if abs(executed_total - expected) > 1e-6:
        v.append(f"seed={ctx.seed}: executed_total={executed_total} != expected={expected} "
                  f"(sum(cpu_time)={sum(e['cpu_time'] for e in ctx.plan)}, "
                  f"penalty={ctx.penalty} * penalty_charges={charged_migrations})")
    return (len(v) == 0, v)


def check_4_single_location(ctx):
    return (len(ctx.location_violations) == 0, list(ctx.location_violations))


def check_5_only_queued_migrate(ctx):
    return (len(ctx.migrate_violations) == 0, list(ctx.migrate_violations))


def check_6_migration_bookkeeping(ctx):
    v = []
    cores = ctx.metrics.cores
    completed = [t for c in cores for t in c.completed_task_list]
    n_logged = len(ctx.migration_events)
    n_task_sum = sum(t.migrations for t in completed)
    n_balancer = ctx.balancer.migrations
    if not (n_balancer == n_logged == n_task_sum):
        v.append(f"seed={ctx.seed}: balancer.migrations={n_balancer}, "
                  f"logged migration events={n_logged}, sum(task.migrations)={n_task_sum} "
                  f"-- expected all three equal")
    return (len(v) == 0, v)


def check_7_determinism(ctx_a, ctx_b):
    v = []
    m_a, m_b = metrics_dict(ctx_a), metrics_dict(ctx_b)
    if m_a != m_b:
        diff_keys = [k for k in m_a if m_a.get(k) != m_b.get(k)]
        v.append(f"seed={ctx_a.seed}: metrics dict differs between two runs of the "
                  f"same config on keys {diff_keys}")
    h_a, h_b = event_log_hash(ctx_a.logger), event_log_hash(ctx_b.logger)
    if h_a != h_b:
        v.append(f"seed={ctx_a.seed}: event-log hash differs between two runs of the "
                  f"same config ({h_a[:12]}... vs {h_b[:12]}...)")
    return (len(v) == 0, v)


def check_9_stacked_placement(ctx):
    if ctx.workload not in STACKED_WORKLOADS:
        return (None, [])
    v = []
    arrivals = {e["task_id"]: e["core"] for e in ctx.logger.filter("arrival")}
    for entry in ctx.plan:
        dc = entry["direct_core"]
        if dc is None:
            continue
        tid = entry["task_id"]
        actual_core = arrivals.get(tid)
        if actual_core != dc.core_id:
            v.append(f"seed={ctx.seed}: task {tid} had direct_core={dc.core_id} but "
                      f"was first enqueued on core {actual_core}")
    return (len(v) == 0, v)


def check_10_burst_bookkeeping(ctx):
    if ctx.scheduler_name != "baseline":
        return (None, [])
    v = []
    triggers = getattr(ctx.balancer, "burst_triggers", 0)
    if triggers:
        v.append(f"seed={ctx.seed}: baseline balancer has burst_triggers={triggers} (expected 0)")
    burst_migs = [e for e in ctx.migration_events if e.get("trigger") == "burst"]
    if burst_migs:
        v.append(f"seed={ctx.seed}: baseline run logged {len(burst_migs)} migration(s) "
                  f"tagged 'burst' (expected 0)")
    return (len(v) == 0, v)


def check_11_idle_check_bookkeeping(ctx):
    # TASK 10 STEP 2/3: BurstAwareLoadBalancer.idle_check_skipped_triggers
    # and LoadBalancer.burst_migrations_dst_busy are the two new per-run
    # counters. N/A for baseline (neither attribute means anything
    # there -- baseline never runs on_task_placed at all).
    if ctx.scheduler_name != "burst_aware":
        return (None, [])
    v = []
    skipped = ctx.balancer.idle_check_skipped_triggers
    triggers = ctx.balancer.burst_triggers
    dst_busy = ctx.balancer.burst_migrations_dst_busy
    burst_migs = ctx.balancer.burst_triggered_migrations
    if not (0 <= skipped <= triggers):
        v.append(f"seed={ctx.seed}: idle_check_skipped_triggers={skipped} not in [0, "
                  f"burst_triggers={triggers}]")
    if not ctx.burst_idle_check and skipped != 0:
        v.append(f"seed={ctx.seed}: burst_idle_check=False but idle_check_skipped_triggers="
                  f"{skipped} (expected 0 -- the flag must have zero effect when off)")
    if not (0 <= dst_busy <= burst_migs):
        v.append(f"seed={ctx.seed}: burst_migrations_dst_busy={dst_busy} not in [0, "
                  f"burst_triggered_migrations={burst_migs}] (dst_busy counts a SUBSET of "
                  f"burst-tagged migrations)")
    return (len(v) == 0, v)


def check_8_paired_workload(plan_a, plan_b, workload, penalty, seed):
    try:
        paired_compare.assert_same_workload(plan_a, plan_b, "baseline", "burst_aware")
    except AssertionError as e:
        return (False, [f"seed={seed}: {e}"])
    return (True, [])


# ==================================================================
# Matrix driver
# ==================================================================

INVARIANT_NAMES = [
    "1_conservation", "2_timing", "3_work_accounting", "4_single_location",
    "5_only_queued_migrate", "6_migration_bookkeeping", "7_determinism",
    "9_stacked_placement", "10_burst_bookkeeping", "11_idle_check_bookkeeping",
]


def run_matrix():
    all_results = []   # dicts: config, workload, scheduler, penalty, seed, invariant, status, violations
    paired_results = []  # dicts: config, workload, penalty, seed, status, violations
    n_runs = 0

    for penalty_model, burst_gap_gate, burst_idle_check in CONFIGS:
        config_label = (f"penalty_model={penalty_model},burst_gap_gate={burst_gap_gate},"
                         f"burst_idle_check={burst_idle_check}")
        for workload_key, wl in WORKLOADS.items():
            for penalty in PENALTIES:
                for i in range(N_SEEDS):
                    seed = SEED_BASE + i
                    ctxs = {}
                    for scheduler_name, scheduler_cls in SCHEDULERS.items():
                        ctx_a = run_instrumented(workload_key, wl, scheduler_cls, scheduler_name, penalty, seed,
                                                  penalty_model=penalty_model, burst_gap_gate=burst_gap_gate,
                                                  burst_idle_check=burst_idle_check)
                        ctx_b = run_instrumented(workload_key, wl, scheduler_cls, scheduler_name, penalty, seed,
                                                  penalty_model=penalty_model, burst_gap_gate=burst_gap_gate,
                                                  burst_idle_check=burst_idle_check)
                        n_runs += 2

                        per = {
                            "1_conservation": check_1_conservation(ctx_a),
                            "2_timing": check_2_timing(ctx_a),
                            "3_work_accounting": check_3_work_accounting(ctx_a),
                            "4_single_location": check_4_single_location(ctx_a),
                            "5_only_queued_migrate": check_5_only_queued_migrate(ctx_a),
                            "6_migration_bookkeeping": check_6_migration_bookkeeping(ctx_a),
                            "7_determinism": check_7_determinism(ctx_a, ctx_b),
                            "9_stacked_placement": check_9_stacked_placement(ctx_a),
                            "10_burst_bookkeeping": check_10_burst_bookkeeping(ctx_a),
                            "11_idle_check_bookkeeping": check_11_idle_check_bookkeeping(ctx_a),
                        }
                        for inv_name, (status, violations) in per.items():
                            all_results.append(dict(
                                config=config_label, workload=workload_key, scheduler=scheduler_name,
                                penalty=penalty, seed=seed, invariant=inv_name, status=status,
                                violations="; ".join(violations),
                            ))
                        ctxs[scheduler_name] = ctx_a

                    status8, viol8 = check_8_paired_workload(
                        ctxs["baseline"].plan, ctxs["burst_aware"].plan, workload_key, penalty, seed,
                    )
                    paired_results.append(dict(
                        config=config_label, workload=workload_key, penalty=penalty, seed=seed,
                        status=status8, violations="; ".join(viol8),
                    ))

                    print(f"  [{config_label}] ran {workload_key:16} penalty={penalty:3} seed={seed}  "
                          f"({n_runs} runs so far)")

    return all_results, paired_results, n_runs


# ==================================================================
# Reporting
# ==================================================================

def _cell_status(rows):
    """rows: list of (status, violations) for one (invariant, workload,
    scheduler, penalty) cell across seeds. PASS iff every seed that
    applied (status is not None) passed; N/A if none applied."""
    applicable = [r for r in rows if r[0] is not None]
    if not applicable:
        return "N/A"
    return "PASS" if all(r[0] for r in applicable) else "FAIL"


def print_report(all_results, paired_results, n_runs, elapsed):
    configs = [f"penalty_model={pm},burst_gap_gate={g},burst_idle_check={c}" for pm, g, c in CONFIGS]
    any_fail = False

    for config_label in configs:
        print("\n" + "=" * 100)
        print(f"INVARIANT TABLE [{config_label}] -- invariant x (workload, scheduler, penalty), "
              f"aggregated over seeds")
        print("=" * 100)

        cfg_results = [r for r in all_results if r["config"] == config_label]
        cells = {}  # (workload, scheduler, penalty) -> {invariant: [(status, violations), ...]}
        for r in cfg_results:
            key = (r["workload"], r["scheduler"], r["penalty"])
            cells.setdefault(key, {}).setdefault(r["invariant"], []).append(
                (r["status"], r["violations"].split("; ") if r["violations"] else [])
            )

        header = f"{'workload':16} {'sched':11} {'pen':4} " + " ".join(f"{n.split('_')[0]:>4}" for n in INVARIANT_NAMES)
        print(header)
        print("  legend: " + ", ".join(f"{n.split('_')[0]}={n}" for n in INVARIANT_NAMES))
        print("-" * len(header))
        for workload_key in WORKLOADS:
            for scheduler_name in SCHEDULERS:
                for penalty in PENALTIES:
                    key = (workload_key, scheduler_name, penalty)
                    row_cells = cells.get(key, {})
                    cell_strs = []
                    for inv in INVARIANT_NAMES:
                        status = _cell_status(row_cells.get(inv, []))
                        if status == "FAIL":
                            any_fail = True
                        cell_strs.append(f"{status:>4}")
                    print(f"{workload_key:16} {scheduler_name:11} {penalty:<4.1f} " + " ".join(cell_strs))

        print("\n" + "-" * 100)
        print(f"INVARIANT 8 [{config_label}] (paired workload: baseline vs. burst_aware plan, same seed)")
        print("-" * 100)
        p8 = {}
        for r in paired_results:
            if r["config"] != config_label:
                continue
            key = (r["workload"], r["penalty"])
            p8.setdefault(key, []).append(r["status"])
        for workload_key in WORKLOADS:
            for penalty in PENALTIES:
                statuses = p8.get((workload_key, penalty), [])
                status = "PASS" if statuses and all(statuses) else ("N/A" if not statuses else "FAIL")
                if status == "FAIL":
                    any_fail = True
                print(f"{workload_key:16} penalty={penalty:<4.1f} {status}")

    print("\n" + "=" * 100)
    if any_fail:
        print("FAILURES (full detail -- config, seed, workload, scheduler, penalty, exact violation)")
        print("=" * 100)
        for r in all_results:
            if r["status"] is False:
                print(f"  [{r['config']}][{r['invariant']}] workload={r['workload']} "
                      f"scheduler={r['scheduler']} penalty={r['penalty']} seed={r['seed']}")
                print(f"      {r['violations']}")
        for r in paired_results:
            if r["status"] is False:
                print(f"  [{r['config']}][8_paired_workload] workload={r['workload']} "
                      f"penalty={r['penalty']} seed={r['seed']}")
                print(f"      {r['violations']}")
    else:
        print("ALL INVARIANTS PASSED (all 3 configs: legacy, penalty_model=ran_only+burst_gap_gate=True, "
              "and penalty_model=ran_only+burst_idle_check=True).")
    print("=" * 100)
    print(f"\n{n_runs} simulation runs, {elapsed:.1f}s total runtime "
          f"({elapsed / n_runs:.3f}s/run average).")

    return not any_fail


def write_csv(all_results, paired_results):
    out_dir = pathlib.Path(__file__).resolve().parent
    with open(out_dir / "results_invariants_perseed.csv", "w", newline="") as f:
        cols = ["config", "workload", "scheduler", "penalty", "seed", "invariant", "status", "violations"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_results)
    with open(out_dir / "results_invariants_paired_perseed.csv", "w", newline="") as f:
        cols = ["config", "workload", "penalty", "seed", "status", "violations"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(paired_results)
    print(f"\nWrote {out_dir / 'results_invariants_perseed.csv'}")
    print(f"Wrote {out_dir / 'results_invariants_paired_perseed.csv'}")


def main():
    start = time.time()
    print(f"Running invariant matrix: {len(CONFIGS)} configs "
          f"({', '.join(f'penalty_model={pm}/burst_gap_gate={g}/burst_idle_check={c}' for pm, g, c in CONFIGS)}) x "
          f"{len(WORKLOADS)} workloads x {len(SCHEDULERS)} schedulers x "
          f"{len(PENALTIES)} penalties x {N_SEEDS} seeds (seeds {SEED_BASE}+), each run TWICE "
          f"for the determinism check (invariant 7).\n")
    all_results, paired_results, n_runs = run_matrix()
    elapsed = time.time() - start
    ok = print_report(all_results, paired_results, n_runs, elapsed)
    write_csv(all_results, paired_results)
    return 0 if ok else 1


# ==================================================================
# Optional pytest integration (only exercised if pytest is installed;
# this environment does not have it, so this path is unverified here --
# the plain `python test_invariants.py` path above is the tested one).
# ==================================================================

try:
    import pytest as _pytest

    @_pytest.fixture(scope="session")
    def _matrix():
        all_results, paired_results, n_runs = run_matrix()
        return all_results, paired_results, n_runs

    def test_no_invariant_failures(_matrix):
        all_results, paired_results, n_runs = _matrix
        failures = [r for r in all_results if r["status"] is False]
        failures += [r for r in paired_results if r["status"] is False]
        assert not failures, "\n".join(
            f"[{r.get('invariant', '8_paired_workload')}] "
            f"workload={r['workload']} penalty={r['penalty']} seed={r['seed']}: {r['violations']}"
            for r in failures
        )
except ImportError:
    pass


if __name__ == "__main__":
    sys.exit(main())
