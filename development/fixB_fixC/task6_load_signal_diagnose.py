"""
Diagnose (report only, no changes yet): does the balancer's load signal
actually see queued tasks? At t=40 in stacked_burst/high seed 5200
(baseline), print core.load()/running_count()/len(rq)/is_idle() for
core 30, its pair-mate, and node, plus classify_group() output at pair
and node level. Same for core 24 (the frequent newidle destination in
the earlier drain trace). Snapshots VALUES at t=40 (not object
references -- the simulation keeps mutating the same Core objects for
the rest of the run after this sampler fires).
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
from Main import run_simulation
from LoadBalancer import LoadBalancer
from Topology import classify_group

SEED = 5200
SNAPSHOT_T = 40.0
WATCH_CORE_IDS = [30, 24]


def describe_core(c):
    return dict(core_id=c.core_id, load=c.load(), running_count=c.running_count(),
                rq_len=len(c.rq), is_idle=c.is_idle(),
                current_task=c.current_task.task_id if c.current_task else None)


def describe_group(group):
    gtype, load, cap, idle, running = classify_group(group)
    type_name = {0: "HAS_SPARE", 1: "FULLY_BUSY", 2: "OVERLOADED"}[gtype]
    return dict(type=type_name, load=load, capacity=cap, idle=idle, running=running)


def make_snapshot_process(state, watch_core_ids):
    def _proc(env, cores, balancer):
        while True:
            if env.now >= SNAPSHOT_T and "taken" not in state:
                state["taken"] = True
                cores_by_id = {c.core_id: c for c in cores}
                state["core_snapshots"] = {cid: describe_core(cores_by_id[cid]) for cid in watch_core_ids}
                state["group_snapshots"] = {}
                for cid in watch_core_ids:
                    core = cores_by_id[cid]
                    pair = core.parent
                    node = pair.parent if pair is not None else None
                    state["group_snapshots"][cid] = dict(
                        pair=describe_group(pair) if pair is not None else None,
                        pair_name=pair.name if pair is not None else None,
                        node=describe_group(node) if node is not None else None,
                        node_name=node.name if node is not None else None,
                    )
                    if pair is not None:
                        state["group_snapshots"][cid]["pair_members"] = [
                            describe_core(pc) for pc in pair.cores()
                        ]
            yield env.timeout(1)
    return _proc


def main():
    state = {}
    m, b, gt, migs, logger, plan = run_simulation(
        "stacked_burst", LoadBalancer, intensity_level="high", seed=SEED,
        balancer_kwargs={"migration_penalty": 0.0, "newidle_mode": "transition"},
        extra_processes=[make_snapshot_process(state, WATCH_CORE_IDS)],
    )

    print(f"=== Snapshot at t={SNAPSHOT_T} (stacked_burst/high, baseline, seed={SEED}) ===\n")
    for cid in WATCH_CORE_IDS:
        cs = state["core_snapshots"][cid]
        gs = state["group_snapshots"][cid]
        print(f"core {cid}: {cs}")
        print(f"  pair ({gs['pair_name']}) members: {gs['pair_members']}")
        print(f"  pair ({gs['pair_name']}) classify_group: {gs['pair']}")
        print(f"  node ({gs['node_name']}) classify_group: {gs['node']}")
        print()


if __name__ == "__main__":
    main()
