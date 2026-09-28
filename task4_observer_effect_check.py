"""
Observer-effect check: do diagnostics.py's live samplers (registered via
Main.run_simulation's extra_processes hook) perturb the simulation they're
observing? They shouldn't -- they only READ core state (is_idle(), len(rq))
and never touch task/core/balancer fields -- but simpy process scheduling
order at a shared timestamp can in principle matter, so this is checked
directly rather than assumed. If any of makespan/avg_wait/p95_wait/
migrations/detector_fires differ, the samplers need fixing (e.g. compute
from the event log post-hoc instead of live sampling).
"""

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
import diagnostics

PROFILE = "bursty"
INTENSITY = "high"
SEED = 1200


def run_one(cls, with_samplers):
    extra = None
    if with_samplers:
        extra = [
            diagnostics.idle_while_waiting_sampler({}),
            diagnostics.queue_imbalance_sampler({}),
        ]
    m, b, gt, migs, logger, plan = run_simulation(
        PROFILE, cls, intensity_level=INTENSITY, seed=SEED,
        balancer_kwargs={"migration_penalty": 0.0, "newidle_mode": "transition"},
        extra_processes=extra,
    )
    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    fires = b.detector_fires if hasattr(b, "detector_fires") else None
    return dict(makespan=s["makespan"], avg_wait=s["avg_wait"], p95_wait=s["p95_wait"],
                migrations=b.migrations, detector_fires=fires)


def main():
    for name, cls in [("baseline", LoadBalancer), ("burst_aware", BurstAwareLoadBalancer)]:
        without = run_one(cls, with_samplers=False)
        with_s = run_one(cls, with_samplers=True)
        print(f"--- {name} ---")
        print(f"  without samplers: {without}")
        print(f"  with samplers:    {with_s}")
        if without == with_s:
            print("  IDENTICAL -- no observer effect")
        else:
            diffs = {k: (without[k], with_s[k]) for k in without if without[k] != with_s[k]}
            print(f"  *** DIFFERS: {diffs} ***")


if __name__ == "__main__":
    main()
