"""
Step 1 diagnosis (report only): with Fix A+B on, why does every periodic
pass off core 30 move exactly 1 task? Wraps _balance_domain to replicate
its own internal classify_group()/branch-selection logic for logging
(read-only -- calls the REAL _balance_domain for actual behavior, just
recomputes the same intermediate values alongside it to report them).
Also wraps _migrate_load/_migrate_tasks to log the computed imbalance
and why the loop actually stopped.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
from Main import run_simulation
from LoadBalancer import LoadBalancer
import LoadBalancer as LB_module
from Topology import GROUP_HAS_SPARE, GROUP_FULLY_BUSY, GROUP_OVERLOADED, CAPACITY_SCALE

SEED = 5200
TYPE_NAME = {GROUP_HAS_SPARE: "HAS_SPARE", GROUP_FULLY_BUSY: "FULLY_BUSY", GROUP_OVERLOADED: "OVERLOADED"}


def main():
    pass_log = []
    migrate_log = []

    orig_balance_domain = LB_module.LoadBalancer._balance_domain
    orig_migrate_load = LB_module.LoadBalancer._migrate_load
    orig_migrate_tasks = LB_module.LoadBalancer._migrate_tasks

    def traced_balance_domain(self, domain, local_core, now, tag="periodic"):
        groups = domain.groups()
        info = {"t": now, "domain": domain.name, "level": domain.level, "tag": tag}
        if len(groups) >= 2:
            stats = {id(g): self.classify(g) for g in groups}
            local_group = self._find_group_containing(groups, local_core)
            local_type, local_load, local_cap, local_idle, local_run = stats[id(local_group)]
            candidates = [g for g in groups if g is not local_group]
            busiest = max(candidates, key=lambda g: stats[id(g)][1])
            b_type, b_load, b_cap, b_idle, b_run = stats[id(busiest)]
            info.update(
                local_type=TYPE_NAME[local_type], busiest_type=TYPE_NAME[b_type],
                local_idle=local_idle, busiest_idle=b_idle,
                local_run=local_run, busiest_run=b_run,
                local_load=round(local_load, 1), busiest_load=round(b_load, 1),
            )
            if local_type == GROUP_HAS_SPARE and b_type != GROUP_OVERLOADED:
                info["branch"] = "has_spare_idle_diff (_migrate_tasks)"
            elif b_type < local_type:
                info["branch"] = "busiest_not_worse -> return 0"
            else:
                info["branch"] = "generic_min_trick (_migrate_load) -- includes has_spare-vs-overloaded case"
        n = orig_balance_domain(self, domain, local_core, now, tag)
        info["n_migrated"] = n
        pass_log.append(info)
        return n

    def traced_migrate_load(self, busiest_group, dst_core, imbalance, now, tag):
        src_cores_before = getattr(busiest_group, "cores", lambda: [busiest_group])()
        rq_len_before = {c.core_id: len(c.rq) for c in src_cores_before}
        n = orig_migrate_load(self, busiest_group, dst_core, imbalance, now, tag)
        migrate_log.append(dict(fn="_migrate_load", imbalance=round(imbalance, 2), n=n,
                                 rq_len_before=rq_len_before))
        return n

    def traced_migrate_tasks(self, busiest_group, dst_core, n_tasks, now, tag):
        n = orig_migrate_tasks(self, busiest_group, dst_core, n_tasks, now, tag)
        migrate_log.append(dict(fn="_migrate_tasks", imbalance=n_tasks, n=n))
        return n

    LB_module.LoadBalancer._balance_domain = traced_balance_domain
    LB_module.LoadBalancer._migrate_load = traced_migrate_load
    LB_module.LoadBalancer._migrate_tasks = traced_migrate_tasks
    try:
        m, b, gt, migs, logger, plan = run_simulation(
            "stacked_burst", LoadBalancer, intensity_level="high", seed=SEED,
            balancer_kwargs={"newidle_mode": "transition", "per_cpu_last_balance": True},
            load_model="runnable",
        )
    finally:
        LB_module.LoadBalancer._balance_domain = orig_balance_domain
        LB_module.LoadBalancer._migrate_load = orig_migrate_load
        LB_module.LoadBalancer._migrate_tasks = orig_migrate_tasks

    start, end, n = gt[0]
    stack_core_id = next(e["direct_core"].core_id for e in plan if start <= e["arrival_time"] <= end)
    print(f"=== stacked on core {stack_core_id}, burst=[{start:.2f},{end:.2f}] n={n} (A+B on) ===\n")

    effective_passes = [p for p in pass_log if p.get("n_migrated", 0) > 0]
    print(f"total effective _balance_domain passes (any domain): {len(effective_passes)}\n")

    for i, p in enumerate(effective_passes[:5]):
        print(f"--- pass {i+1} at t={p['t']:.2f} ---")
        print(f"  domain={p['domain']} level={p['level']} tag={p['tag']}")
        print(f"  local_type={p.get('local_type')}  busiest_type={p.get('busiest_type')}")
        print(f"  local_idle={p.get('local_idle')} busiest_idle={p.get('busiest_idle')}  "
              f"local_run={p.get('local_run')} busiest_run={p.get('busiest_run')}")
        print(f"  local_load={p.get('local_load')} busiest_load={p.get('busiest_load')}")
        print(f"  branch: {p.get('branch')}")
        print(f"  n_migrated={p['n_migrated']}")
        print()

    print("=== corresponding _migrate_load/_migrate_tasks calls (first 5 with n>0) ===")
    shown = 0
    for m_entry in migrate_log:
        if m_entry["n"] > 0:
            print(f"  {m_entry}")
            shown += 1
        if shown >= 5:
            break


if __name__ == "__main__":
    main()
