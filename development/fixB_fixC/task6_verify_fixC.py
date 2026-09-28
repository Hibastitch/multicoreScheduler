"""
Fix C verification: A+B vs A+B+C on stacked_burst/high seed 5200,
baseline. Drain time, migrations off core 30 by trigger, and tasks
moved per successful _balance_domain pass (mean).
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
from Main import run_simulation
from LoadBalancer import LoadBalancer
import LoadBalancer as LB_module

SEED = 5200
CONFIGS = [
    ("A+B", "runnable", True, "legacy"),
    ("A+B+C", "runnable", True, "kernel"),
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
    _, _, gt0, _, _, plan0 = run_simulation(
        "stacked_burst", LoadBalancer, intensity_level="high", seed=SEED,
        balancer_kwargs={"newidle_mode": "transition"}, load_model="legacy",
    )
    start, end, n = gt0[0]
    stack_core_id = next(e["direct_core"].core_id for e in plan0 if start <= e["arrival_time"] <= end)

    for label, load_model, per_cpu, imbalance_model in CONFIGS:
        pass_sizes = []
        orig = LB_module.LoadBalancer._balance_domain

        def traced(self, domain, local_core, now, tag="periodic"):
            n = orig(self, domain, local_core, now, tag)
            if n:
                pass_sizes.append(n)
            return n

        LB_module.LoadBalancer._balance_domain = traced
        try:
            state = {}
            m, b, gt, migs, logger, plan = run_simulation(
                "stacked_burst", LoadBalancer, intensity_level="high", seed=SEED,
                balancer_kwargs={"newidle_mode": "transition", "per_cpu_last_balance": per_cpu,
                                  "imbalance_model": imbalance_model},
                load_model=load_model,
                extra_processes=[make_qlen_tracer(stack_core_id, state)],
            )
        finally:
            LB_module.LoadBalancer._balance_domain = orig

        qlog = state["qlen_log"]
        drained_at = next((t for t, qlen, running in qlog if t > start and qlen == 0 and not running), None)

        off_stack = [e for e in migs if e["src"] == stack_core_id]
        periodic_off = sum(1 for e in off_stack if e["trigger"] == "periodic")
        newidle_off = sum(1 for e in off_stack if e["trigger"] == "newidle")

        s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
        mean_pass_size = sum(pass_sizes) / len(pass_sizes) if pass_sizes else 0

        print(f"=== {label}: load_model={load_model} per_cpu_last_balance={per_cpu} "
              f"imbalance_model={imbalance_model} ===")
        print(f"  core {stack_core_id}'s queue drained at t={drained_at}  "
              f"({(drained_at - end):.1f}ms after burst ended)" if drained_at is not None else
              "  never drained within the run")
        print(f"  whole-run makespan: {s['makespan']}")
        print(f"  migrations OFF core {stack_core_id}: total={len(off_stack)}  "
              f"periodic={periodic_off}  newidle={newidle_off}")
        print(f"  tasks moved per successful pass (any domain, whole run): "
              f"mean={mean_pass_size:.2f}  n_passes={len(pass_sizes)}  max={max(pass_sizes) if pass_sizes else 0}")
        print()


if __name__ == "__main__":
    main()
