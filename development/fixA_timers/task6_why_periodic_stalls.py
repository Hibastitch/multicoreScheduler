"""
Why does periodic balancing stop correcting core 30's pair/node after
t=32? Monkey-patch (log-only, restored after, no behavior change) both
is_designated_checker() and _balance_domain() to record every call
touching n3-pair3 (core 30's pair) or node3 (its node), whether it
returns 0 or not, and why.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
from Main import run_simulation
from LoadBalancer import LoadBalancer
import LoadBalancer as LB_module

SEED = 5200
WATCH_DOMAIN_NAMES = {"n3-pair3", "node3"}


def main():
    checker_log = []
    balance_log = []

    orig_checker = LB_module.LoadBalancer.is_designated_checker
    orig_balance = LB_module.LoadBalancer._balance_domain

    def traced_checker(self, domain, core):
        result = orig_checker(self, domain, core)
        if domain.name in WATCH_DOMAIN_NAMES:
            checker_log.append((self._current_now, domain.name, core.core_id, result))
        return result

    def traced_balance(self, domain, local_core, now, tag="periodic"):
        n = orig_balance(self, domain, local_core, now, tag)
        if domain.name in WATCH_DOMAIN_NAMES:
            balance_log.append((now, domain.name, local_core.core_id, tag, n))
        return n

    # is_designated_checker doesn't receive `now` as a param -- patch
    # periodic_balance too, just to stash `now` on self for the checker
    # trace above to read. Purely a logging side channel.
    orig_periodic = LB_module.LoadBalancer.periodic_balance

    def traced_periodic(self, core, now):
        self._current_now = now
        return orig_periodic(self, core, now)

    LB_module.LoadBalancer.is_designated_checker = traced_checker
    LB_module.LoadBalancer._balance_domain = traced_balance
    LB_module.LoadBalancer.periodic_balance = traced_periodic
    try:
        m, b, gt, migs, logger, plan = run_simulation(
            "stacked_burst", LoadBalancer, intensity_level="high", seed=SEED,
            balancer_kwargs={"migration_penalty": 0.0, "newidle_mode": "transition"},
        )
    finally:
        LB_module.LoadBalancer.is_designated_checker = orig_checker
        LB_module.LoadBalancer._balance_domain = orig_balance
        LB_module.LoadBalancer.periodic_balance = orig_periodic

    start, end, n = gt[0]
    print(f"=== is_designated_checker() calls for {WATCH_DOMAIN_NAMES} "
          f"during [{start:.0f}, {start + 60:.0f}] ===")
    for t, dname, core_id, result in checker_log:
        if start <= t <= start + 60:
            print(f"  t={t:6.2f}  domain={dname:10}  asked_core={core_id:3d}  is_checker={result}")

    print(f"\n=== _balance_domain() calls (any result) for {WATCH_DOMAIN_NAMES} "
          f"during [{start:.0f}, {start + 60:.0f}] ===")
    for t, dname, core_id, tag, n_mig in balance_log:
        if start <= t <= start + 60:
            print(f"  t={t:6.2f}  domain={dname:10}  checker_core={core_id:3d}  tag={tag:9}  n_migrated={n_mig}")

    print(f"\ntotal _balance_domain calls for these 2 domains across the WHOLE run: {len(balance_log)}")
    print(f"total is_designated_checker calls for these 2 domains across the WHOLE run: {len(checker_log)}")
    non_checker = sum(1 for _, _, _, result in checker_log if not result)
    print(f"of those, asked-but-NOT-the-checker: {non_checker}  asked-and-IS-checker: {len(checker_log) - non_checker}")


if __name__ == "__main__":
    main()
