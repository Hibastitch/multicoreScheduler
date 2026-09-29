import random
import simpy

from Topology import build_topology, STATS
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from Placement import select_core_for_task
from WorkloadGenerator import WorkloadGenerator, PROFILE_NAMES
from Metrics import Metrics
from Eventlog import EventLog


def run_simulation(profile_name, balancer_cls, intensity_level="medium",
                    intensity_overrides=None, num_cores=32, n_tasks=200,
                    seed=42, balancer_kwargs=None, extra_processes=None, load_model="runnable",
                    time_slice=2.8, placement_root="own"):
    # TASK 8 FIX 6 (2026-09-29, docs/FIDELITY_AUDIT.md §10): time_slice
    # defaults to 2.8 -- the EFFECTIVE sysctl_sched_base_slice for a
    # >=8-CPU machine under default tunable scaling, not the module
    # constant TIME_SLICE=4 every run before this task used
    # unconditionally. Pass time_slice=4 explicitly to reproduce a
    # pre-Task-8 run's exact scheduling granularity.
    # TASK 8 FIX 3 (docs/FIDELITY_AUDIT.md §11): placement_root="own"
    # (DEFAULT since 2026-09-29, following the Step 2 baseline-only
    # ablation) descends from the entry core's own per-node machine
    # domain. Pass placement_root="fixed" explicitly to reproduce
    # pre-Task-8 behavior (always machines[0], the node-0-anchored root
    # every caller used to be handed regardless of the task's actual
    # node -- see Placement.py).
    # FINAL CONFIGURATION (2026-09-27i, see Readme.md): load_model
    # defaults to "runnable" (Fix C's queue-aware PELT signal, distinct
    # util_avg vs load_avg) -- "legacy" (the original conflated,
    # queue-blind signal) is what every pre-2026-09-27i CSV used. Pass
    # load_model="legacy" explicitly to reproduce that.
    # RNG ISOLATION (Fix 3d, 2026-09-26, see Readme.md): workload
    # generation and scheduling-side randomness each get their OWN
    # random.Random instance, seeded from this call's `seed` but never
    # touching the shared `random` module -- a scheduler-dependent random
    # draw elsewhere can no longer perturb what tasks get generated (this
    # was found to silently break the "same seed = same workload"
    # assumption every paired baseline-vs-burst-aware comparison depends
    # on). See WorkloadGenerator.py's module docstring and LoadBalancer.py
    # for the two halves of this fix.
    workload_rng = random.Random(seed)
    STATS.reset()  # Topology.WorkStats: per-run, not per-process -- see its docstring
    env = simpy.Environment()
    logger = EventLog()
    balancer_kwargs = balancer_kwargs or {}

    machine, cores = build_topology(num_cores, cores_per_pair=2, pairs_per_node=4, nodes_in_one_hop=3,
                                     load_model=load_model, time_slice=time_slice)
    cores_by_id = {c.core_id: c for c in cores}

    balancer = balancer_cls(machine, cores_by_id, logger=logger, seed=seed, **balancer_kwargs)
    for c in cores:
        c.balancer = balancer
        env.process(c.run(env))

    def place(task, entry_core=None, direct_core=None):
        # CHANGED 2026-09-24 (was the fixed cores[0] every time): real
        # Linux's fork path (wake_up_new_task() -> select_task_rq_fair())
        # descends from wherever the FORKING task's own CPU actually is,
        # which varies task to task -- pinning every placement decision to
        # one fixed core biased every "stay local" decision toward the
        # same corner of the machine, regardless of where load actually
        # was.
        #
        # CHANGED 2026-09-26 (Fix 3d): entry_core is now ALWAYS supplied
        # by WorkloadGenerator (drawn from its own isolated RNG at
        # planning time, before this run's Core processes exist) -- no
        # more per-call random.choice(cores) fallback here, which used to
        # pull from the shared `random` module. Fails loudly rather than
        # silently defaulting if that invariant is ever violated.
        #
        # ADDED 2026-09-26 (Task 5, stacked_burst -- opt-in, see
        # Readme.md): `direct_core`, when set, means "enqueue directly on
        # this core, bypassing placement's wake-affine/idle-sibling
        # search entirely" -- the deliberately worst-case test for a
        # balancer when placement itself does nothing to spread load.
        # None for every other profile, so behavior there is unchanged.
        if direct_core is not None:
            target = direct_core
        else:
            assert entry_core is not None, (
                "place() called without an entry_core or direct_core -- WorkloadGenerator "
                "must always supply one now (Fix 3d); a None here means something bypassed "
                "the generator's plan and would reintroduce shared-random contamination."
            )
            target = select_core_for_task(task, entry_core, cores_by_id, machine=machine,
                                           placement_root=placement_root)
        target.enqueue(task, vruntime_baseline=target.avg_vruntime())
        logger.log(env.now, "arrival", task_id=task.task_id, core=target.core_id)
        balancer.on_task_placed(target, env.now)

    gen = WorkloadGenerator(env, place, n_tasks=n_tasks, cores=cores, rng=workload_rng)
    env.process(gen.run_profile(profile_name, intensity_level, intensity_overrides))

    def periodic_ticker():
        while True:
            yield env.timeout(1)
            balancer.sample_queues(cores, env.now)
            for c in cores:
                balancer.periodic_balance(c, env.now)

    env.process(periodic_ticker())

    # Task 4 diagnostics hook (2026-09-26): optional list of `fn(env, cores,
    # balancer) -> generator` callables, registered as extra simpy
    # processes -- e.g. diagnostics.py's live idle/queue-imbalance
    # samplers. None by default, so every existing call site is unaffected.
    for proc_fn in (extra_processes or []):
        env.process(proc_fn(env, cores, balancer))

    max_steps = 3_000_000
    steps = 0
    while steps < max_steps:
        env.step()
        steps += 1
        completed = sum(len(c.completed_task_list) for c in cores)
        if completed >= n_tasks:
            break
    else:
        print(f"WARNING: hit max_steps without completing all tasks ({profile_name})")

    for c in cores:
        for t in c.completed_task_list:
            logger.log(t.finish_time, "completion", task_id=t.task_id, core=c.core_id)

    metrics = Metrics(cores, env.now)
    migration_events = logger.filter("migration")
    # `gen.plan` (Fix 3d): the pre-generated task list, exposed so callers
    # (paired_compare.py's run_pair()) can assert two "paired" runs
    # actually saw the identical workload -- see Readme.md.
    return metrics, balancer, gen.ground_truth_bursts, migration_events, logger, gen.plan


def main():
    for profile in PROFILE_NAMES:
        print(f"\n===== Profile: {profile} (intensity=medium) =====")

        print("-- Baseline (wake-affine + periodic + newidle) --")
        m_base, b_base, gt, migs, _, _ = run_simulation(profile, LoadBalancer)
        m_base.print_results(balancer=b_base, ground_truth_bursts=gt, migration_events=migs)

        print("\n-- Burst-aware adaptive (observable signals only) --")
        m_burst, b_burst, gt2, migs2, _, _ = run_simulation(profile, BurstAwareLoadBalancer)
        m_burst.print_results(balancer=b_burst, ground_truth_bursts=gt2, migration_events=migs2)
        print(f"Burst Triggers:         {b_burst.burst_triggers}")


if __name__ == "__main__":
    main()