"""
Verify: does periodic_balance() run for idle cores every tick, and are
idle cores elected as balance checkers? (LoadBalancer.py's
_find_checker() prioritizes any idle sibling over the min-core_id
fallback; Main.py's periodic_ticker calls periodic_balance() for EVERY
core, busy or idle, every 1ms tick -- confirmed by direct code reading,
this script traces it directly on a real run instead of trusting that
reading alone.)

Then: trace ONE stacked_burst/high burst (seed 5200, baseline) --
the stacked core's queue length every tick from burst start until it
drains to empty, plus every migration (time, src, dst, domain level)
during that window.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
from Main import run_simulation
from LoadBalancer import LoadBalancer
import LoadBalancer as LB_module
import diagnostics

SEED = 5200


def patch_balance_domain_tracer(domain_log):
    """Monkey-patches LoadBalancer._balance_domain (class-level, restored
    after) to record (t, domain.name, domain.level, tag, n_migrated) for
    every EFFECTIVE call. Pure observation -- returns exactly what the
    real method returns, changes no decision. Same technique as the
    wake_affine/fork-path call-count check above: patch, run, restore,
    rather than touching LoadBalancer.py itself to thread a new
    parameter through 4 functions just for one diagnostic trace."""
    orig = LB_module.LoadBalancer._balance_domain

    def traced(self, domain, local_core, now, tag="periodic"):
        n = orig(self, domain, local_core, now, tag)
        if n:
            domain_log.append((now, domain.name, domain.level, tag, n))
        return n

    LB_module.LoadBalancer._balance_domain = traced
    return orig


def unpatch_balance_domain_tracer(orig):
    LB_module.LoadBalancer._balance_domain = orig


def make_checker_trace_procs(state):
    """Records, once per tick, which core each domain's checker is and
    whether that core is idle -- direct evidence for the claim, not just
    a code reading."""
    state.setdefault("checker_log", [])

    def _proc(env, cores, balancer):
        # Sample one representative pair-domain (core 0's) each tick.
        core0 = cores[0]
        while True:
            d = core0.parent
            if d is not None:
                checker = balancer._find_checker(d)
                state["checker_log"].append((env.now, checker.core_id, checker.is_idle()))
            yield env.timeout(1)
    return _proc


def main():
    checker_state = {}
    m, b, gt, migs, logger, plan = run_simulation(
        "stacked_burst", LoadBalancer, intensity_level="high", seed=SEED,
        balancer_kwargs={"migration_penalty": 0.0, "newidle_mode": "transition"},
        extra_processes=[make_checker_trace_procs(checker_state)],
    )

    log = checker_state["checker_log"]
    idle_checker_ticks = sum(1 for _, _, is_idle in log if is_idle)
    print(f"=== Checker-election check (core 0's pair-domain, {len(log)} ticks sampled) ===")
    print(f"ticks where the elected checker was IDLE: {idle_checker_ticks}/{len(log)} "
          f"({idle_checker_ticks / len(log):.1%})")
    print(f"first 15 samples (t, checker_core_id, checker_is_idle): {log[:15]}")

    # --- first burst's stacked core, and its queue length over the burst ---
    start, end, n = gt[0]
    stack_core_id = next(e["direct_core"].core_id for e in plan if start <= e["arrival_time"] <= end)
    stack_core = next(c for c in m.cores if c.core_id == stack_core_id)
    print(f"\n=== First burst: start={start:.2f} end={end:.2f} n={n} stacked on core {stack_core_id} ===")

    # Replay: we don't have a live rq-length trace for a specific past run
    # already completed, so re-run WITH a sampler that watches this core
    # specifically (same seed -> same workload, Fix 3d-guaranteed).
    qlen_state = {}

    def make_qlen_tracer(core_id, state):
        state.setdefault("qlen_log", [])

        def _proc(env, cores, balancer):
            core = next(c for c in cores if c.core_id == core_id)
            while True:
                state["qlen_log"].append((env.now, len(core.rq), core.current_task is not None))
                yield env.timeout(1)
        return _proc

    domain_log = []
    orig = patch_balance_domain_tracer(domain_log)
    try:
        m2, b2, gt2, migs2, logger2, plan2 = run_simulation(
            "stacked_burst", LoadBalancer, intensity_level="high", seed=SEED,
            balancer_kwargs={"migration_penalty": 0.0, "newidle_mode": "transition"},
            extra_processes=[make_qlen_tracer(stack_core_id, qlen_state)],
        )
    finally:
        unpatch_balance_domain_tracer(orig)

    qlog = [(t, qlen, running) for t, qlen, running in qlen_state["qlen_log"] if t >= start - 2]
    drained_at = next((t for t, qlen, running in qlog if t > start and qlen == 0 and not running), None)
    print(f"queue length trace from burst start until drained (t, qlen, task_running):")
    for t, qlen, running in qlog:
        print(f"  t={t:6.2f}  qlen={qlen:3d}  running={running}")
        if drained_at is not None and t >= drained_at:
            break

    print(f"\nDrained at t={drained_at}")

    window_end = (drained_at or end) + 5
    print(f"\n=== effective _balance_domain() calls (any domain, any core) during "
          f"[{start:.2f}, {window_end:.2f}] -- (t, domain_name, level, tag, n_migrated) ===")
    for t, dname, dlevel, tag, n_mig in domain_log:
        if start - 1 <= t <= window_end:
            print(f"  t={t:7.2f}  domain={dname!r:20}  level={dlevel}  tag={tag:9}  n={n_mig}")

    print(f"\n=== migrations touching core {stack_core_id} during [{start:.2f}, {window_end:.2f}] "
          f"(time, task, src->dst, trigger) ===")
    for e in migs2:
        if start - 1 <= e["t"] <= window_end and (e["src"] == stack_core_id or e["dst"] == stack_core_id):
            print(f"  t={e['t']:7.2f}  task={e['task_id']:4d}  src={e['src']:3d} -> dst={e['dst']:3d}  trigger={e['trigger']}")


if __name__ == "__main__":
    main()
