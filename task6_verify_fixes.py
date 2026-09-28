"""
Verify Fix A (per_cpu_last_balance) and Fix B (load_model="runnable")
against the stacked_burst/high seed=5200 baseline drain that motivated
them. 4 configs: neither / A only / B only / A+B. For each: time until
core 30's queue (the stacked core, first burst) fully drains -- via a
live per-tick queue-length sampler, same methodology as the original
Task 6 prep drain trace, not an approximation from task finish times --
and how many migrations moved a task OFF core 30 (src==30), split by
trigger (periodic vs newidle).
"""

from Main import run_simulation
from LoadBalancer import LoadBalancer

SEED = 5200
CONFIGS = [
    ("neither", "legacy", False),
    ("A only (per_cpu_last_balance)", "legacy", True),
    ("B only (load_model=runnable)", "runnable", False),
    ("A+B", "runnable", True),
]


def make_qlen_tracer(core_id, state):
    state.setdefault("qlen_log", [])

    def _proc(env, cores, balancer):
        core = next(c for c in cores if c.core_id == core_id)
        while True:
            state["qlen_log"].append((env.now, len(core.rq), core.current_task is not None))
            yield env.timeout(1)
    return _proc


def main():
    # Fix A/B don't touch workload generation/RNG -- determine the
    # stacked core and burst window once, upfront, with a throwaway run.
    _, _, gt0, _, _, plan0 = run_simulation(
        "stacked_burst", LoadBalancer, intensity_level="high", seed=SEED,
        balancer_kwargs={"newidle_mode": "transition"}, load_model="legacy",
    )
    start, end, n = gt0[0]
    stack_core_id = next(e["direct_core"].core_id for e in plan0 if start <= e["arrival_time"] <= end)

    for label, load_model, per_cpu in CONFIGS:
        state = {}
        m, b, gt, migs, logger, plan = run_simulation(
            "stacked_burst", LoadBalancer, intensity_level="high", seed=SEED,
            balancer_kwargs={"newidle_mode": "transition", "per_cpu_last_balance": per_cpu},
            load_model=load_model,
            extra_processes=[make_qlen_tracer(stack_core_id, state)],
        )

        qlog = state["qlen_log"]
        drained_at = next((t for t, qlen, running in qlog if t > start and qlen == 0 and not running), None)

        off_stack = [e for e in migs if e["src"] == stack_core_id]
        periodic_off = sum(1 for e in off_stack if e["trigger"] == "periodic")
        newidle_off = sum(1 for e in off_stack if e["trigger"] == "newidle")
        other_off = len(off_stack) - periodic_off - newidle_off

        s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)

        print(f"=== {label}: load_model={load_model} per_cpu_last_balance={per_cpu} ===")
        print(f"  stacked on core {stack_core_id}, burst=[{start:.2f},{end:.2f}] n={n}")
        print(f"  core {stack_core_id}'s queue drained at t={drained_at}  "
              f"({(drained_at - end):.1f}ms after burst ended)" if drained_at is not None else
              f"  core {stack_core_id}'s queue never drained within the run")
        print(f"  whole-run makespan: {s['makespan']}")
        print(f"  migrations OFF core {stack_core_id}: total={len(off_stack)}  "
              f"periodic={periodic_off}  newidle={newidle_off}  other={other_off}")
        print()


if __name__ == "__main__":
    main()
