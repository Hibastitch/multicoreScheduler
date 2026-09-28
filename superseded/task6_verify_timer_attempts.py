"""
Re-measurement of the timer-postponement hypothesis, done properly this
time: log ALL periodic ATTEMPTS (every time the interval gate passes and
is_designated_checker() is asked -- regardless of the outcome or of
whether _balance_domain then finds anything to move) on the burst's own
pair domain, for the burst's own checker core specifically, from burst
start to +15ms, in both schedulers. The previous measurement compared
gaps between EFFECTIVE passes (n>0), which pulled in unrelated later
bursts on the same domain (e.g. 594ms, 1518ms gaps) instead of showing
the checker's own attempt cadence right after the burst.

Report only -- no logic changed.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "simulator"))
from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
import LoadBalancer as LB_module

SEEDS = [5004, 5027]
BALANCER_KWARGS = {"newidle_mode": "transition", "per_cpu_last_balance": True,
                    "imbalance_model": "kernel", "migration_penalty": 0.0}
WINDOW = 15  # ms after burst start to trace


def trace_run(cls, seed):
    """Returns (gt, migs, plan, attempts) where attempts is a list of
    dicts: t, core, domain, is_checker -- one entry per
    is_designated_checker() call (== one periodic ATTEMPT, since that
    function is only reached once the interval gate has passed)."""
    attempts = []
    orig = LB_module.LoadBalancer.is_designated_checker

    def traced(self, domain, core):
        result = orig(self, domain, core)
        attempts.append(dict(t=self._trace_now, core=core.core_id, domain=domain.name, is_checker=result))
        return result

    orig_periodic = LB_module.LoadBalancer.periodic_balance

    def traced_periodic(self, core, now):
        self._trace_now = now
        return orig_periodic(self, core, now)

    LB_module.LoadBalancer.is_designated_checker = traced
    LB_module.LoadBalancer.periodic_balance = traced_periodic
    try:
        m, b, gt, migs, logger, plan = run_simulation(
            "stacked_burst", cls, intensity_level="low", seed=seed,
            balancer_kwargs=BALANCER_KWARGS, load_model="runnable",
        )
    finally:
        LB_module.LoadBalancer.is_designated_checker = orig
        LB_module.LoadBalancer.periodic_balance = orig_periodic

    return gt, migs, plan, attempts


def main():
    for seed in SEEDS:
        print(f"\n{'#' * 90}\nSEED {seed}\n{'#' * 90}")

        gt_b, migs_b, plan_b, att_b = trace_run(LoadBalancer, seed)
        gt_a, migs_a, plan_a, att_a = trace_run(BurstAwareLoadBalancer, seed)
        assert gt_b == gt_a, "ground truth differs!"

        for bi, (start, end, n) in enumerate(gt_a[:6]):
            # identify the burst-tagged pass's domain + checker core in burst-aware
            burst_mig = next((e for e in migs_a if e["trigger"] == "burst" and start - 1 <= e["t"] <= end + 5), None)
            if burst_mig is None:
                continue
            # find which domain/checker this migration's _balance_domain call used --
            # approximate via the attempt log: the checker asked closest in time to
            # the migration, for a domain whose name we can cross-reference by
            # checking which (core, domain) pair was asked exactly at burst_mig['t'].
            t_trigger = burst_mig["t"]
            candidates = [a for a in att_a if a["t"] == t_trigger and a["is_checker"]]
            if not candidates:
                print(f"\nburst {bi}: couldn't identify checker/domain for the burst-tagged migration at t={t_trigger}")
                continue
            checker_core = candidates[0]["core"]
            domain_name = candidates[0]["domain"]

            print(f"\n--- burst {bi} (start={start:.1f}): burst-tagged migration at t={t_trigger}, "
                  f"checker_core={checker_core}, domain={domain_name} ---")

            aware_attempts = sorted(
                {a["t"] for a in att_a if a["core"] == checker_core and a["domain"] == domain_name
                 and start - 1 <= a["t"] <= start + WINDOW}
            )
            base_attempts = sorted(
                {a["t"] for a in att_b if a["core"] == checker_core and a["domain"] == domain_name
                 and start - 1 <= a["t"] <= start + WINDOW}
            )
            print(f"  burst-aware periodic ATTEMPTS on (core={checker_core}, domain={domain_name}): {aware_attempts}")
            print(f"  baseline    periodic ATTEMPTS on (core={checker_core}, domain={domain_name}): {base_attempts}")

            first_after_trigger_aware = next((t for t in aware_attempts if t > t_trigger), None)
            first_after_trigger_base = next((t for t in base_attempts if t > t_trigger), None)
            print(f"  first attempt strictly AFTER t={t_trigger}: burst-aware={first_after_trigger_aware}  "
                  f"baseline={first_after_trigger_base}")


if __name__ == "__main__":
    main()
