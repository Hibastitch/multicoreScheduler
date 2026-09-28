"""
Task 3: is newidle_cost_avg (LoadBalancer.py's try_newidle gate -- an EMA of
"did the last newidle attempt actually pull a task," used as
`if self._newidle_rng.random() < core.newidle_cost_avg` to decide whether to
even TRY) decayed near zero by the time a real burst lands? Idle cores poll
this gate every tick; during any quiet stretch with nothing to pull, each
failed/skipped tick nudges it toward 0 (0.9*avg + 0.1*0), and a low avg also
makes the NEXT attempt less likely -- a potential self-reinforcing
suppression that could cripple baseline's only reactive (non-periodic)
balancing path right when a burst starts.

Report only -- this does not modify try_newidle/newidle_cost_avg or any
balancing logic, per the ground rules for this diagnosis phase. Captures
the live newidle_cost_avg of every IDLE core at the exact instant each
ground-truth burst starts.

UPDATED 2026-09-26 (Fix 3d): WorkloadGenerator now pre-generates its whole
plan synchronously before any simpy process runs (see its module
docstring), so there's no longer a live "burst is starting now" moment
inside generation to subclass/hook. Ground-truth burst start times are
known immediately after generate_plan() (called directly, before simpy
starts) -- a small watcher process is scheduled per burst to sleep until
that exact time and take the snapshot then. Also picked up Fix 3d's RNG
isolation (own random.Random(seed) for the generator, seed threaded to
the balancer for its own newidle RNG) instead of seeding the shared
`random` module.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
import random
import statistics

import simpy

from Topology import build_topology, STATS
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from Placement import select_core_for_task
from WorkloadGenerator import WorkloadGenerator
from Eventlog import EventLog
from paired_compare import seed_base

N_REPS = 30
PROFILES_INTENSITIES = [("bursty", "high"), ("bursty", "medium"),
                         ("heavy_tail", "high"), ("heavy_tail", "medium")]


def _burst_onset_watcher(env, cores, start_time, snapshots):
    if env.now < start_time:
        yield env.timeout(start_time - env.now)
    idle_snapshot = [(c.core_id, c.newidle_cost_avg) for c in cores if c.is_idle()]
    snapshots.append((env.now, idle_snapshot))


def run_traced(profile, balancer_cls, intensity_level, seed, n_tasks=200, num_cores=32):
    workload_rng = random.Random(seed)  # Fix 3d: isolated, never the shared `random` module
    STATS.reset()
    env = simpy.Environment()
    logger = EventLog()

    machine, cores = build_topology(num_cores, cores_per_pair=2, pairs_per_node=4, nodes_in_one_hop=3)
    cores_by_id = {c.core_id: c for c in cores}
    balancer = balancer_cls(machine, cores_by_id, logger=logger, seed=seed)
    for c in cores:
        c.balancer = balancer
        env.process(c.run(env))

    def place(task, entry_core=None, direct_core=None):
        if direct_core is not None:
            target = direct_core
        else:
            assert entry_core is not None, "WorkloadGenerator must always supply an entry_core (Fix 3d)"
            target = select_core_for_task(task, entry_core, cores_by_id, machine=machine)
        target.enqueue(task, vruntime_baseline=target.avg_vruntime())
        logger.log(env.now, "arrival", task_id=task.task_id, core=target.core_id)
        balancer.on_task_placed(target, env.now)

    gen = WorkloadGenerator(env, place, n_tasks=n_tasks, cores=cores, rng=workload_rng)
    gen.generate_plan(profile, intensity_level)  # synchronous, no simpy needed -- ground_truth_bursts known now

    snapshots = []
    for start, end, n in gen.ground_truth_bursts:
        env.process(_burst_onset_watcher(env, cores, start, snapshots))

    env.process(gen.run_profile(profile, intensity_level))  # reuses the plan just generated, doesn't regenerate

    def periodic_ticker():
        while True:
            yield env.timeout(1)
            balancer.sample_queues(cores, env.now)
            for c in cores:
                balancer.periodic_balance(c, env.now)

    env.process(periodic_ticker())

    max_steps = 3_000_000
    steps = 0
    while steps < max_steps:
        env.step()
        steps += 1
        completed = sum(len(c.completed_task_list) for c in cores)
        if completed >= n_tasks:
            break

    return snapshots


def main():
    for profile, intensity in PROFILES_INTENSITIES:
        base = seed_base(profile, intensity)
        for name, cls in [("baseline", LoadBalancer), ("burst_aware", BurstAwareLoadBalancer)]:
            pooled = []
            n_onsets = 0
            n_onsets_with_idle_core = 0
            for rep in range(N_REPS):
                snapshots = run_traced(profile, cls, intensity, base + rep)
                for t, idle_list in snapshots:
                    n_onsets += 1
                    if idle_list:
                        n_onsets_with_idle_core += 1
                    pooled.extend(v for _, v in idle_list)

            print(f"\n=== {profile}/{intensity} scheduler={name} "
                  f"({N_REPS} seeds, {n_onsets} burst onsets, "
                  f"{n_onsets_with_idle_core} had >=1 idle core) ===")
            if not pooled:
                print("  no idle cores present at any burst onset -- gate is moot here")
                continue
            pooled_sorted = sorted(pooled)
            n = len(pooled_sorted)
            print(f"  idle-core newidle_cost_avg samples: n={n}")
            print(f"  min={pooled_sorted[0]:.4f}  "
                  f"p10={pooled_sorted[int(0.10*n)]:.4f}  "
                  f"median={statistics.median(pooled_sorted):.4f}  "
                  f"mean={statistics.mean(pooled_sorted):.4f}  "
                  f"p90={pooled_sorted[min(n-1,int(0.90*n))]:.4f}  "
                  f"max={pooled_sorted[-1]:.4f}")
            for thresh in [0.05, 0.1, 0.2, 0.5]:
                frac = sum(1 for v in pooled_sorted if v < thresh) / n
                print(f"  fraction < {thresh}: {frac:.2%}")


if __name__ == "__main__":
    main()
