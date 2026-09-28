"""
Diagnose (report only, no changes): why does burst-aware make small
stacked bursts (stacked_burst/low) WORSE on p95_wait, for the 2 worst
seeds? Traces, for both schedulers: detector signal (arrival_rate vs
queue_growth, values vs thresholds) from the "burst_check"/"burst_trigger"
log events, every burst-tagged migration (task, src, dst, dst queue
length/running state AT THAT MOMENT, from a live re-run with a snapshot
sampler keyed on migration times), and each burst task's start_time
under both schedulers.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from BurstDetector import BurstDetector

SEEDS = [5004, 5027]
PENALTY = 0.0
BALANCER_KWARGS = {"newidle_mode": "transition", "per_cpu_last_balance": True,
                    "imbalance_model": "kernel", "migration_penalty": PENALTY}
WORKLOAD_KWARGS = {"load_model": "runnable"}


def make_dst_state_sampler(state):
    """Snapshots, at every migration event's exact tick, every core's
    (rq_len, running) -- read back afterward keyed by (t, dst) to recover
    "dst queue length/running state at that moment" without needing to
    modify _do_migrate() itself."""
    state.setdefault("core_states_by_tick", {})

    def _proc(env, cores, balancer):
        while True:
            state["core_states_by_tick"][env.now] = {
                c.core_id: (len(c.rq), c.current_task is not None) for c in cores
            }
            yield env.timeout(1)
    return _proc


def run_one(cls, seed):
    state = {}
    m, b, gt, migs, logger, plan = run_simulation(
        "stacked_burst", cls, intensity_level="low", seed=seed,
        balancer_kwargs=BALANCER_KWARGS, load_model=WORKLOAD_KWARGS["load_model"],
        extra_processes=[make_dst_state_sampler(state)],
    )
    task_starts = {}
    for c in m.cores:
        for t in c.completed_task_list:
            task_starts[t.task_id] = (t.start_time, t.finish_time, t.arrival_time)
    burst_checks = logger.filter("burst_check") if hasattr(logger, "filter") else []
    burst_triggers = logger.filter("burst_trigger") if hasattr(logger, "filter") else []
    return dict(gt=gt, migs=migs, plan=plan, task_starts=task_starts,
                core_states=state["core_states_by_tick"],
                burst_checks=burst_checks, burst_triggers=burst_triggers, logger=logger)


def main():
    for seed in SEEDS:
        print(f"\n{'#' * 90}\nSEED {seed}\n{'#' * 90}")

        base = run_one(LoadBalancer, seed)
        burst = run_one(BurstAwareLoadBalancer, seed)

        assert base["gt"] == burst["gt"], "ground truth differs -- workload isolation regression!"
        print(f"ground_truth_bursts: {base['gt']}")

        for bi, (start, end, n) in enumerate(base["gt"]):
            burst_task_ids = [e["task_id"] for e in burst["plan"]
                               if start <= e["arrival_time"] <= end]
            print(f"\n--- burst {bi}: start={start:.2f} end={end:.2f} n={n} tasks={burst_task_ids} ---")

            print("  [burst_aware] detector checks in this window (t, core, arrival_rate, queue_growth, triggered):")
            for e in burst["burst_checks"]:
                if start - 2 <= e["t"] <= end + 15:
                    flag = " <-- TRIGGERED" if e["triggered"] else ""
                    print(f"    t={e['t']:7.2f}  core={e['core']:3d}  "
                          f"arrival_rate={e['arrival_rate']:.3f} (thr=0.8)  "
                          f"queue_growth={e['queue_growth']:.3f} (thr=2.0){flag}")

            print("  [burst_aware] burst_trigger events (post-cooldown):")
            for e in burst["burst_triggers"]:
                if start - 2 <= e["t"] <= end + 15:
                    print(f"    t={e['t']:7.2f}  core={e['core']:3d}  "
                          f"arrival_rate={e['arrival_rate']:.3f}  queue_growth={e['queue_growth']:.3f}")

            print("  [burst_aware] burst-tagged migrations in this window:")
            for e in burst["migs"]:
                if e["trigger"] == "burst" and start - 2 <= e["t"] <= end + 15:
                    dst_state = burst["core_states"].get(e["t"], {}).get(e["dst"])
                    print(f"    t={e['t']:7.2f}  task={e['task_id']:4d}  src={e['src']:3d} -> dst={e['dst']:3d}  "
                          f"dst_state_at_that_tick(rq_len,running)={dst_state}")

            print("  [burst_aware] ALL migrations (any trigger) in this window:")
            for e in burst["migs"]:
                if start - 2 <= e["t"] <= end + 15:
                    print(f"    t={e['t']:7.2f}  task={e['task_id']:4d}  src={e['src']:3d} -> dst={e['dst']:3d}  "
                          f"trigger={e['trigger']}")

            print("  [baseline] ALL migrations in this window:")
            for e in base["migs"]:
                if start - 2 <= e["t"] <= end + 15:
                    print(f"    t={e['t']:7.2f}  task={e['task_id']:4d}  src={e['src']:3d} -> dst={e['dst']:3d}  "
                          f"trigger={e['trigger']}")

            print("  task start_time/finish_time (baseline vs burst_aware):")
            for tid in burst_task_ids:
                bs = base["task_starts"].get(tid)
                brs = burst["task_starts"].get(tid)
                print(f"    task {tid}: baseline start={bs[0] if bs else None} finish={bs[1] if bs else None}  |  "
                      f"burst_aware start={brs[0] if brs else None} finish={brs[1] if brs else None}")


if __name__ == "__main__":
    main()
