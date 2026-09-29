"""
Task 8 pre-audit, Step 4: how many of this simulator's migrations would
real Linux's task_hot() (fair.c:10291-10329) have refused as cache-hot?

Uses Task.last_ran_until (Task.py, Core.py) -- a TRACKING-ONLY field
added for this measurement, read by nothing else in the simulator. Set
in Core.run() to env.now at the moment a task STOPS running (whether it
finished or was preempted back into the queue); None if the task has
never run since being forked. This is the direct analog of real Linux's
se.exec_start: __sched_fork() sets p->se.exec_start=0 at fork
(core.c:4568), and only update_stats_curr_start() (fair.c:2150-2156,
called when a task is actually picked to run) overwrites it with a real
timestamp -- so task_hot()'s `delta = rq_clock_task(...) - p->se.
exec_start` is enormous (never hot) for a task that has been forked but
never run, exactly like last_ran_until=None here.

Verified byte-identical before/after adding the field: a throwaway
fingerprint script (12 runs, 2 balancers x 2 workloads x 3 seeds, full
migration-event sequences + summary metrics, sha256) produced the
IDENTICAL hash before and after this file's Task.py/Core.py edits --
confirming the field changes no decision anywhere. Not committed (was a
scratch check); the claim is reproducible by reverting the two-line
Core.py/Task.py change and re-running such a script.

Measurement (read-only monkeypatch of LoadBalancer._do_migrate --
records the would-be-hot verdict, never alters it): baseline
(LoadBalancer) + burst-aware (BurstAwareLoadBalancer), 5 workloads
(canonical definitions from final_results/2_confirmation/
task6_confirmation_run.py), 10 seeds (50000-50009), checker_model=
"kernel" held constant (consistent with this audit's other scripts,
isolating this measurement from Task 7's already-measured checker
effect). CACHE_HOT_THRESHOLD_MS = 0.5, matching sysctl_sched_
migration_cost = 500,000 ns (fair.c:82).
"""

import sys, pathlib, csv
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer

N_SEEDS = 10
SEED_BASE = 50000
CACHE_HOT_THRESHOLD_MS = 0.5  # sysctl_sched_migration_cost = 500,000 ns, fair.c:82

WORKLOADS = {
    "stacked_medium":  dict(profile="stacked_burst", intensity="medium", overrides=None, n_tasks=200),
    "stacked_high":    dict(profile="stacked_burst", intensity="high", overrides=None, n_tasks=200),
    "rate3.0_s12":     dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 12, "arrival_rate_during_burst": 3.0,
                                        "burst_duration": 12 / 3.0, "inter_burst_interval": 60},
                             n_tasks=120),
    "bursty_high_s24": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 24, "burst_duration": 24 / 3.75}, n_tasks=240),
    "bursty_high_s64": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 64, "burst_duration": 64 / 3.75}, n_tasks=640),
}

BALANCERS = {"baseline": LoadBalancer, "burst_aware": BurstAwareLoadBalancer}

_orig_do_migrate = LoadBalancer._do_migrate


def patched_do_migrate(self, task, src_core, dst_core, now, tag="periodic"):
    never_run = task.last_ran_until is None
    hot = (not never_run) and (now - task.last_ran_until) < CACHE_HOT_THRESHOLD_MS
    self._audit_migration_record.append((tag, hot, never_run))
    return _orig_do_migrate(self, task, src_core, dst_core, now, tag)


def run_one(wl_name, wl, balancer_cls, seed):
    LoadBalancer._do_migrate = patched_do_migrate
    try:
        m, b, gt, migs, logger, plan = run_simulation(
            wl["profile"], balancer_cls, intensity_level=wl["intensity"], seed=seed,
            balancer_kwargs=dict(migration_penalty=0.0, checker_model="kernel"),
            intensity_overrides=wl["overrides"], n_tasks=wl["n_tasks"],
            extra_processes=None,
        )
    finally:
        LoadBalancer._do_migrate = _orig_do_migrate
    return b._audit_migration_record


def main():
    # _audit_migration_record must exist on the balancer instance before
    # patched_do_migrate runs -- attach via a tiny __init__ wrapper rather
    # than editing LoadBalancer.py.
    orig_init = LoadBalancer.__init__

    def patched_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self._audit_migration_record = []

    LoadBalancer.__init__ = patched_init

    rows = []
    detail_rows = []
    try:
        for wl_name, wl in WORKLOADS.items():
            for bal_name, bal_cls in BALANCERS.items():
                total = 0
                hot = 0
                never_run = 0
                by_trigger = {}
                for rep in range(N_SEEDS):
                    seed = SEED_BASE + rep
                    record = run_one(wl_name, wl, bal_cls, seed)
                    for tag, is_hot, is_never_run in record:
                        total += 1
                        hot += int(is_hot)
                        never_run += int(is_never_run)
                        d = by_trigger.setdefault(tag, [0, 0, 0])
                        d[0] += 1
                        d[1] += int(is_hot)
                        d[2] += int(is_never_run)
                pct_hot = 100 * hot / total if total else float("nan")
                pct_never_run = 100 * never_run / total if total else float("nan")
                print(f"{wl_name:16} {bal_name:12} total_migrations={total:6}  "
                      f"would_be_cache_hot={hot:5} ({pct_hot:5.1f}%)  "
                      f"never_run_at_migration_time={never_run:5} ({pct_never_run:5.1f}%)")
                rows.append(dict(workload=wl_name, balancer=bal_name, total_migrations=total,
                                  would_be_cache_hot=hot, pct_cache_hot=round(pct_hot, 2),
                                  never_run_at_migration=never_run,
                                  pct_never_run=round(pct_never_run, 2)))
                for tag, (t, h, nr) in by_trigger.items():
                    detail_rows.append(dict(workload=wl_name, balancer=bal_name, trigger=tag,
                                             total=t, would_be_cache_hot=h,
                                             pct_cache_hot=round(100 * h / t, 2) if t else float("nan"),
                                             never_run=nr,
                                             pct_never_run=round(100 * nr / t, 2) if t else float("nan")))
    finally:
        LoadBalancer.__init__ = orig_init

    with open(pathlib.Path(__file__).resolve().parent / "results_task8_step4_cache_hot_scope.csv",
              "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(pathlib.Path(__file__).resolve().parent / "results_task8_step4_cache_hot_scope_by_trigger.csv",
              "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
        w.writeheader()
        w.writerows(detail_rows)
    print("\nWrote results_task8_step4_cache_hot_scope.csv and _by_trigger.csv")


if __name__ == "__main__":
    main()
