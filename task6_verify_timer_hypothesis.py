"""
Item 1: verify the "burst path resets last_balance and postpones the
bigger periodic pass" hypothesis with actual numbers, for both worst
seeds. Also check whether the burst path uses the SAME Fix C sizing as
periodic (it calls the same _balance_domain(), so the FORMULA is
identical -- the question is whether the STATE at call time differs,
i.e. is it called at a moment with less accumulated backlog).

Report only -- no logic changed.
"""

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
import LoadBalancer as LB_module

SEEDS = [5004, 5027]
BALANCER_KWARGS = {"newidle_mode": "transition", "per_cpu_last_balance": True,
                    "imbalance_model": "kernel", "migration_penalty": 0.0}


def trace_run(cls, seed):
    """Returns (gt, migs, plan, domain_pass_log) where domain_pass_log is
    a list of dicts, one per EFFECTIVE _balance_domain call (n>0):
    t, domain_name, tag, checker_core, n_migrated, imbalance_state
    (local_util/local_cap/busiest info at call time, when available)."""
    passes = []
    orig = LB_module.LoadBalancer._balance_domain
    orig_migrate_util = LB_module.LoadBalancer._migrate_util

    call_ctx = {}

    def traced_migrate_util(self, busiest_group, local_group, dst_core, now, tag):
        local_cores = local_group.cores() if hasattr(local_group, "cores") else [local_group]
        local_util = sum(c.util_avg for c in local_cores)
        local_cap = len(local_cores) * 1024
        call_ctx["migrate_util_imbalance"] = max(local_cap, local_util) - local_util
        call_ctx["migrate_util_local_util"] = local_util
        n = orig_migrate_util(self, busiest_group, local_group, dst_core, now, tag)
        return n

    def traced(self, domain, local_core, now, tag="periodic"):
        call_ctx.clear()
        n = orig(self, domain, local_core, now, tag)
        if n:
            passes.append(dict(
                t=now, domain=domain.name, level=domain.level, tag=tag,
                checker=local_core.core_id, n=n,
                migrate_util_imbalance=call_ctx.get("migrate_util_imbalance"),
                migrate_util_local_util=call_ctx.get("migrate_util_local_util"),
            ))
        return n

    LB_module.LoadBalancer._balance_domain = traced
    LB_module.LoadBalancer._migrate_util = traced_migrate_util
    try:
        m, b, gt, migs, logger, plan = run_simulation(
            "stacked_burst", cls, intensity_level="low", seed=seed,
            balancer_kwargs=BALANCER_KWARGS, load_model="runnable",
        )
    finally:
        LB_module.LoadBalancer._balance_domain = orig
        LB_module.LoadBalancer._migrate_util = orig_migrate_util

    return gt, migs, plan, passes


def main():
    for seed in SEEDS:
        print(f"\n{'#' * 90}\nSEED {seed}\n{'#' * 90}")

        gt_b, migs_b, plan_b, passes_b = trace_run(LoadBalancer, seed)
        gt_a, migs_a, plan_a, passes_a = trace_run(BurstAwareLoadBalancer, seed)
        assert gt_b == gt_a, "ground truth differs!"

        delays_burst_aware = []
        delays_baseline = []

        for bi, (start, end, n) in enumerate(gt_a[:10]):
            # burst-aware: find the burst-tagged pass in this window, then
            # the NEXT pass on the SAME domain (any tag) after it.
            burst_pass = next((p for p in passes_a if p["tag"] == "burst" and start - 1 <= p["t"] <= end + 20), None)
            if burst_pass is None:
                continue
            same_domain_later = [p for p in passes_a
                                  if p["domain"] == burst_pass["domain"] and p["t"] > burst_pass["t"]]
            next_pass = min(same_domain_later, key=lambda p: p["t"]) if same_domain_later else None
            gap_aware = (next_pass["t"] - burst_pass["t"]) if next_pass else None

            # baseline: first periodic pass on the SAME domain name at/after
            # burst start, then the NEXT one after THAT (to compare gap
            # between consecutive passes on that domain, apples to apples).
            base_domain_passes = sorted([p for p in passes_b if p["domain"] == burst_pass["domain"]
                                          and p["t"] >= start - 1], key=lambda p: p["t"])
            gap_baseline = None
            if len(base_domain_passes) >= 2:
                gap_baseline = base_domain_passes[1]["t"] - base_domain_passes[0]["t"]

            print(f"\nburst {bi} (start={start:.1f}): domain={burst_pass['domain']}  "
                  f"burst-tagged pass at t={burst_pass['t']:.1f} moved n={burst_pass['n']}  "
                  f"migrate_util_imbalance={burst_pass.get('migrate_util_imbalance')}")
            print(f"  burst-aware: next pass on same domain at "
                  f"t={next_pass['t'] if next_pass else None} "
                  f"(tag={next_pass['tag'] if next_pass else None}, n={next_pass['n'] if next_pass else None})  "
                  f"-> gap={gap_aware}")
            if base_domain_passes:
                print(f"  baseline: passes on same domain from burst start: "
                      f"{[(round(p['t'],1), p['tag'], p['n']) for p in base_domain_passes[:4]]}  "
                      f"-> gap between 1st and 2nd={gap_baseline}")
            else:
                print("  baseline: NO passes recorded on this domain name in this window")

            if gap_aware is not None:
                delays_burst_aware.append(gap_aware)
            if gap_baseline is not None:
                delays_baseline.append(gap_baseline)

        if delays_burst_aware:
            print(f"\nSEED {seed} summary (first 10 bursts): "
                  f"burst-aware post-burst-migration gap to next same-domain pass: {delays_burst_aware}  "
                  f"mean={sum(delays_burst_aware)/len(delays_burst_aware):.2f}")
        if delays_baseline:
            print(f"SEED {seed} summary: baseline gap between consecutive same-domain periodic passes: "
                  f"{delays_baseline}  mean={sum(delays_baseline)/len(delays_baseline):.2f}")


if __name__ == "__main__":
    main()
