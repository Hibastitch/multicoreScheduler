"""
Fixed re-measurement: the first attempt-trace (task6_verify_timer_
attempts.py) identified the burst's domain/checker by matching
is_designated_checker() calls at the exact trigger timestamp -- but
MANY domains get checked at any given tick, so it silently picked
whichever one happened first in iteration order (core 0 / n0-pair0)
instead of the burst's own pair domain. Confirmed wrong: bursts stacked
on cores 21, 11, 13, 7 were all misattributed to core0/n0-pair0.

Fixed here by capturing the domain/checker DIRECTLY from the
burst-tagged _balance_domain() call itself (same technique as the
original, correct domain identification in
task6_low_intensity_reversal.py), not by guessing from timestamps.
"""

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
import LoadBalancer as LB_module

SEEDS = [5004, 5027]
BALANCER_KWARGS = {"newidle_mode": "transition", "per_cpu_last_balance": True,
                    "imbalance_model": "kernel", "migration_penalty": 0.0}
WINDOW = 15


def trace_run(cls, seed):
    attempts = []
    burst_calls = []  # (t, domain_name, checker_core) for every tag="burst" call, whether or not n>0

    orig_checker = LB_module.LoadBalancer.is_designated_checker
    orig_periodic = LB_module.LoadBalancer.periodic_balance
    orig_balance_domain = LB_module.LoadBalancer._balance_domain

    def traced_checker(self, domain, core):
        result = orig_checker(self, domain, core)
        attempts.append(dict(t=self._trace_now, core=core.core_id, domain=domain.name, is_checker=result))
        return result

    def traced_periodic(self, core, now):
        self._trace_now = now
        return orig_periodic(self, core, now)

    def traced_balance_domain(self, domain, local_core, now, tag="periodic"):
        n = orig_balance_domain(self, domain, local_core, now, tag)
        if tag == "burst":
            burst_calls.append(dict(t=now, domain=domain.name, checker=local_core.core_id, n=n))
        return n

    LB_module.LoadBalancer.is_designated_checker = traced_checker
    LB_module.LoadBalancer.periodic_balance = traced_periodic
    LB_module.LoadBalancer._balance_domain = traced_balance_domain
    try:
        m, b, gt, migs, logger, plan = run_simulation(
            "stacked_burst", cls, intensity_level="low", seed=seed,
            balancer_kwargs=BALANCER_KWARGS, load_model="runnable",
        )
    finally:
        LB_module.LoadBalancer.is_designated_checker = orig_checker
        LB_module.LoadBalancer.periodic_balance = orig_periodic
        LB_module.LoadBalancer._balance_domain = orig_balance_domain

    return gt, migs, plan, attempts, burst_calls


def main():
    for seed in SEEDS:
        print(f"\n{'#' * 90}\nSEED {seed}\n{'#' * 90}")

        gt_b, migs_b, plan_b, att_b, _ = trace_run(LoadBalancer, seed)
        gt_a, migs_a, plan_a, att_a, burst_calls_a = trace_run(BurstAwareLoadBalancer, seed)
        assert gt_b == gt_a, "ground truth differs!"

        for bi, (start, end, n) in enumerate(gt_a[:8]):
            # Correctly identify the domain+checker: the burst-tagged
            # _balance_domain() call that actually MOVED something
            # (n>0) in this burst's window -- captured directly, not
            # guessed from timestamp collisions.
            call = next((c for c in burst_calls_a if c["n"] > 0 and start - 1 <= c["t"] <= end + 5), None)
            if call is None:
                print(f"\nburst {bi}: no effective burst-tagged call in window -- skipping")
                continue
            t_trigger, domain_name, checker_core = call["t"], call["domain"], call["checker"]

            print(f"\n--- burst {bi} (start={start:.1f}): burst-tagged call at t={t_trigger}, "
                  f"domain={domain_name}, checker_core={checker_core}, n_moved={call['n']} ---")

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

            first_after_aware = next((t for t in aware_attempts if t > t_trigger), None)
            first_after_base = next((t for t in base_attempts if t > t_trigger), None)
            print(f"  first attempt strictly AFTER t={t_trigger}: burst-aware={first_after_aware}  "
                  f"baseline={first_after_base}")


if __name__ == "__main__":
    main()
