"""
Check: A+B+C's max=28-tasks-per-pass batch on stacked_burst/high seed
5200, baseline -- did it just relocate the pile onto ONE other core
instead of spreading it? Trace the 3 largest _balance_domain passes:
time, domain/level, src core, dst core, branch taken, imbalance,
n moved, dst core's queue length right after.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
from Main import run_simulation
from LoadBalancer import LoadBalancer
import LoadBalancer as LB_module
from Topology import GROUP_HAS_SPARE, GROUP_FULLY_BUSY, GROUP_OVERLOADED

SEED = 5200
TYPE_NAME = {GROUP_HAS_SPARE: "HAS_SPARE", GROUP_FULLY_BUSY: "FULLY_BUSY", GROUP_OVERLOADED: "OVERLOADED"}


def main():
    events = []

    orig_balance_domain = LB_module.LoadBalancer._balance_domain
    orig_migrate_util = LB_module.LoadBalancer._migrate_util
    orig_migrate_load = LB_module.LoadBalancer._migrate_load
    orig_migrate_tasks = LB_module.LoadBalancer._migrate_tasks

    call_info = {}  # filled by the migrate_* wrappers, read by the _balance_domain wrapper

    def traced_migrate_util(self, busiest_group, local_group, dst_core, now, tag):
        busiest_cores = busiest_group.cores() if hasattr(busiest_group, "cores") else [busiest_group]
        busiest_cores = [c for c in busiest_cores if c.rq]
        src_core = max(busiest_cores, key=lambda c: c.load()) if busiest_cores else None
        n = orig_migrate_util(self, busiest_group, local_group, dst_core, now, tag)
        call_info["branch"] = "migrate_util"
        call_info["src"] = src_core.core_id if src_core else None
        call_info["dst"] = dst_core.core_id
        call_info["n"] = n
        call_info["dst_rq_after"] = len(dst_core.rq)
        return n

    def traced_migrate_load(self, busiest_group, dst_core, imbalance, now, tag):
        n = orig_migrate_load(self, busiest_group, dst_core, imbalance, now, tag)
        call_info["branch"] = "migrate_load (generic min-trick)"
        call_info["imbalance"] = round(imbalance, 2)
        call_info["dst"] = dst_core.core_id
        call_info["n"] = n
        call_info["dst_rq_after"] = len(dst_core.rq)
        return n

    def traced_migrate_tasks(self, busiest_group, dst_core, n_tasks, now, tag):
        n = orig_migrate_tasks(self, busiest_group, dst_core, n_tasks, now, tag)
        call_info["branch"] = call_info.get("branch_hint", "migrate_task (sibling/idle-diff)")
        call_info["imbalance"] = n_tasks
        call_info["dst"] = dst_core.core_id
        call_info["n"] = n
        call_info["dst_rq_after"] = len(dst_core.rq)
        return n

    def traced_balance_domain(self, domain, local_core, now, tag="periodic"):
        call_info.clear()
        groups = domain.groups()
        if len(groups) >= 2:
            stats = {id(g): self.classify(g) for g in groups}
            local_group = self._find_group_containing(groups, local_core)
            candidates = [g for g in groups if g is not local_group]
            busiest = max(candidates, key=lambda g: stats[id(g)][1])
            b_type = stats[id(busiest)][0]
            busiest_weight_one = not hasattr(busiest, "cores")
            prefer_sibling = not getattr(busiest, "is_numa", False)
            if b_type == GROUP_OVERLOADED and not domain.share_llc:
                call_info["branch_hint"] = "migrate_util"
            elif busiest_weight_one or prefer_sibling:
                call_info["branch_hint"] = "sibling nr_running diff"
            else:
                call_info["branch_hint"] = "idle-cpu diff"

        n = orig_balance_domain(self, domain, local_core, now, tag)
        if n:
            extra = {k: v for k, v in call_info.items() if k not in ("branch_hint", "n")}
            events.append(dict(
                t=now, domain=domain.name, level=domain.level, checker=local_core.core_id,
                tag=tag, n=n, **extra,
            ))
        return n

    LB_module.LoadBalancer._balance_domain = traced_balance_domain
    LB_module.LoadBalancer._migrate_util = traced_migrate_util
    LB_module.LoadBalancer._migrate_load = traced_migrate_load
    LB_module.LoadBalancer._migrate_tasks = traced_migrate_tasks
    try:
        m, b, gt, migs, logger, plan = run_simulation(
            "stacked_burst", LoadBalancer, intensity_level="high", seed=SEED,
            balancer_kwargs={"newidle_mode": "transition", "per_cpu_last_balance": True,
                              "imbalance_model": "kernel"},
            load_model="runnable",
        )
    finally:
        LB_module.LoadBalancer._balance_domain = orig_balance_domain
        LB_module.LoadBalancer._migrate_util = orig_migrate_util
        LB_module.LoadBalancer._migrate_load = orig_migrate_load
        LB_module.LoadBalancer._migrate_tasks = orig_migrate_tasks

    start, end, n0 = gt[0]
    top3 = sorted(events, key=lambda e: e["n"], reverse=True)[:3]
    print(f"=== stacked_burst/high seed={SEED} A+B+C baseline -- top 3 largest passes ===\n")
    for e in top3:
        print(f"t={e['t']:.2f}  domain={e['domain']} level={e['level']}  checker_core={e['checker']}  "
              f"tag={e['tag']}")
        print(f"  branch={e.get('branch')}  src={e.get('src')}  dst={e.get('dst')}  "
              f"imbalance={e.get('imbalance')}  n_moved={e['n']}  dst_rq_after={e.get('dst_rq_after')}")
        print()


if __name__ == "__main__":
    main()
