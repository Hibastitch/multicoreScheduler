"""
Task 8 pre-audit: cheap impact measurements for the UNDOCUMENTED MISMATCH
rows in docs/FIDELITY_AUDIT.md that are togglable WITHOUT editing any
simulator file. Each toggle below is a monkeypatch applied only for the
duration of one run; simulator/*.py is never modified by this script.

Baseline scheduler only (LoadBalancer, not BurstAwareLoadBalancer -- the
audit is about the verified pipeline's own fidelity, which the burst path
inherits wholesale, see FIDELITY_AUDIT.md item 13). Fresh seeds (50000+,
never used in any calibration/confirmation run), n=10, paired by seed,
two workloads: stacked_high (sustained heavy load -- exercises the
periodic path hardest) and bursty_high_s64 (exercises burst arrivals on
top of periodic/newidle -- the scenario most relevant to "does periodic
already look artificially reactive").

Three toggles, each measured independently against the SAME current-
default baseline:

  A. busy_factor: get_sd_balance_interval(sd, cpu_busy) multiplies the
     periodic-check interval by sd->busy_factor=16 when the checking CPU
     is busy (fair.c:13566-13586, sd_init() busy_factor=16 at
     topology.c:1958). Our periodic_balance() never applies this scaling
     -- every check uses the unscaled interval regardless of whether the
     checking core is idle or busy. Toggle: wrap periodic_balance with an
     identical copy that multiplies the check-time (not the stored,
     persisted) interval by 16 whenever `core.is_idle()` is False, exactly
     mirroring get_sd_balance_interval's parameterization (busy_factor is
     applied fresh at comparison time in real Linux, never baked into the
     persisted sd->balance_interval doubling/reset state -- see
     fair.c:13800-13816).

  B. out_balanced !idle gate: sched_balance_find_src_group() (fair.c:
     12871-12881) bails out entirely -- "if (!env->idle) goto
     out_balanced" -- when busiest is not group_overloaded and the
     checking CPU is not idle. Our _balance_domain() has no equivalent:
     it proceeds into GROUP_OVERLOADED/GROUP_HAS_SPARE handling
     regardless of whether local_core is idle. Toggle: wrap
     _balance_domain with an identical copy that returns 0 immediately
     when b_type != GROUP_OVERLOADED and not local_core.is_idle(), right
     after busiest is classified -- a first-order approximation of the
     real gate (the full gate also has SMT/idle_cpus-diff and
     sum_h_nr_running==1 carve-outs, fair.c:12881-12905, not replicated
     here since they only ADD extra bail-out conditions, so this
     approximation is conservative -- it can only under-count how often
     real Linux would bail out, never over-count).

  C. NUMA_IMBALANCE_MIN: real adjust_numa_imbalance() forgives NUMA
     imbalances <= 2 raw tasks (fair.c:2177-2178, hardcoded
     "#define NUMA_IMBALANCE_MIN 2"), gated on dst_running <= imb_numa_nr
     where imb_numa_nr is computed per-topology by
     topology.c:adjust_numa_imbalance() (2870-2934) -- for THIS sim's
     4-node/8-core-per-node/3-node-onehop topology, nr_llcs=3 at the
     onehop level so imb = nr_llcs = 3, and the upper-level factor is 1
     at both onehop and machine (imb_span==32==machine span), giving
     imb_numa_nr=3 at both NUMA levels. Our LoadBalancer.py instead uses
     NUMA_IMBALANCE_MIN=32 (explicitly commented as an unverified stand-
     in, in CAPACITY-SCALE-flavored units but actually compared directly
     against a raw task-count `imbalance`/`raw` value in both call sites)
     and NUMA_DST_BUSY_THRESHOLD=2 (real: imb_numa_nr=3 for this
     topology). Toggle: monkeypatch the two module-level constants
     directly (both are plain module globals referenced by name inside
     _adjust_numa_imbalance, so this needs no method override at all).

Each toggle is measured ON ITS OWN against the current default (all
three OFF) -- not combined -- so its individual effect is isolable.
Metrics: p95_wait, total_migrations (baseline-only; no burst-specific
metrics apply). No simulator file is edited. No default is changed.
"""

import sys, pathlib, math, csv
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))

from Main import run_simulation
import LoadBalancer as LB_mod
from LoadBalancer import LoadBalancer
from Topology import GROUP_OVERLOADED, STATS
from paired_compare import wilcoxon_signed_rank, format_p, TIE_TOLERANCE

N_SEEDS = 10
SEED_BASE = 50000
PENALTY = 0.0

WORKLOADS = {
    "stacked_high":    dict(profile="stacked_burst", intensity="high", overrides=None, n_tasks=200),
    "bursty_high_s64": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 64, "burst_duration": 64 / 3.75}, n_tasks=640),
}


def sign_p(wins, harms, n):
    if n == 0:
        return 1.0
    k = max(wins, harms)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


# ---------------- Toggle A: busy_factor ----------------

BUSY_FACTOR = 16  # sd_init(), topology.c:1958


def periodic_balance_with_busy_factor(self, core, now):
    STATS.periodic_calls += 1
    d = core.parent
    while d is not None:
        STATS.periodic_levels_walked += 1
        if self.per_cpu_last_balance:
            state = self._balance_state(core, d)
            last_balance, interval = state["last_balance"], state["balance_interval"]
        else:
            last_balance, interval = d.last_balance, d.balance_interval

        # TOGGLE A: get_sd_balance_interval(sd, cpu_busy) scales the
        # CHECK-TIME interval by busy_factor when the checking core is
        # busy -- not stored back into the persisted backoff state.
        check_interval = interval * BUSY_FACTOR if not core.is_idle() else interval

        if now - last_balance >= check_interval:
            if self.per_cpu_last_balance:
                state["last_balance"] = now
            else:
                d.last_balance = now
            if self.is_designated_checker(d, core):
                if d.is_numa:
                    if self._numa_balance_locked:
                        d = d.parent
                        continue
                    self._numa_balance_locked = True
                    try:
                        n = self._balance_domain(d, core, now)
                    finally:
                        self._numa_balance_locked = False
                else:
                    n = self._balance_domain(d, core, now)
                new_interval = d.min_interval if n else min(interval * 2, d.max_interval)
                if self.per_cpu_last_balance:
                    state["balance_interval"] = new_interval
                else:
                    d.balance_interval = new_interval
        d = d.parent


# ---------------- Toggle B: out_balanced !idle gate ----------------

_orig_balance_domain = LoadBalancer._balance_domain


def balance_domain_with_idle_gate(self, domain, local_core, now, tag="periodic"):
    groups = domain.groups()
    if len(groups) < 2:
        return 0
    stats = {id(g): self.classify(g) for g in groups}
    local_group = self._find_group_containing(groups, local_core)
    local_type, local_load, local_cap, local_idle, local_run = stats[id(local_group)]
    candidates = [g for g in groups if g is not local_group]
    busiest = max(candidates, key=lambda g: stats[id(g)][1])
    b_type, b_load, b_cap, b_idle, b_run = stats[id(busiest)]

    if b_type < local_type:
        return 0

    # TOGGLE B: sched_balance_find_src_group()'s out_balanced gate
    # (fair.c:12871-12881, approximated) -- if busiest isn't overloaded
    # and the checking core is busy, real Linux bails out here.
    if b_type != GROUP_OVERLOADED and not local_core.is_idle():
        return 0

    return _orig_balance_domain(self, domain, local_core, now, tag)


# ---------------- measurement harness ----------------

def run_one(wl, seed, checker_model="kernel", periodic_patch=None, balance_domain_patch=None,
            numa_const_override=None):
    orig_periodic = LoadBalancer.periodic_balance
    orig_balance_domain = LoadBalancer._balance_domain
    orig_numa_min = LB_mod.NUMA_IMBALANCE_MIN
    orig_numa_busy = LB_mod.NUMA_DST_BUSY_THRESHOLD
    try:
        if periodic_patch:
            LoadBalancer.periodic_balance = periodic_patch
        if balance_domain_patch:
            LoadBalancer._balance_domain = balance_domain_patch
        if numa_const_override:
            LB_mod.NUMA_IMBALANCE_MIN, LB_mod.NUMA_DST_BUSY_THRESHOLD = numa_const_override
        m, b, gt, migs, logger, plan = run_simulation(
            wl["profile"], LoadBalancer, intensity_level=wl["intensity"], seed=seed,
            balancer_kwargs=dict(migration_penalty=PENALTY, checker_model=checker_model),
            intensity_overrides=wl["overrides"], n_tasks=wl["n_tasks"],
        )
        s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
        return s["p95_wait"], s["total_migrations"]
    finally:
        LoadBalancer.periodic_balance = orig_periodic
        LoadBalancer._balance_domain = orig_balance_domain
        LB_mod.NUMA_IMBALANCE_MIN = orig_numa_min
        LB_mod.NUMA_DST_BUSY_THRESHOLD = orig_numa_busy


def measure_toggle(name, **toggle_kwargs):
    print(f"\n=== Toggle {name} ===")
    rows = []
    for wl_name, wl in WORKLOADS.items():
        base_p95, base_migs, tog_p95, tog_migs = [], [], [], []
        for rep in range(N_SEEDS):
            seed = SEED_BASE + rep
            bp95, bmigs = run_one(wl, seed)
            tp95, tmigs = run_one(wl, seed, **toggle_kwargs)
            base_p95.append(bp95); base_migs.append(bmigs)
            tog_p95.append(tp95); tog_migs.append(tmigs)
            rows.append(dict(toggle=name, workload=wl_name, seed=seed,
                              p95_wait_default=bp95, p95_wait_toggled=tp95,
                              migrations_default=bmigs, migrations_toggled=tmigs))
        for metric, base_vals, tog_vals in [("p95_wait", base_p95, tog_p95),
                                             ("migrations", base_migs, tog_migs)]:
            diffs = [t - b for b, t in zip(base_vals, tog_vals)]
            wins = sum(1 for d in diffs if d < -TIE_TOLERANCE)
            harms = sum(1 for d in diffs if d > TIE_TOLERANCE)
            ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
            n_eff = len(diffs) - ties
            p = sign_p(wins, harms, n_eff)
            wr = wilcoxon_signed_rank(diffs)
            mean_b = sum(base_vals) / len(base_vals)
            mean_t = sum(tog_vals) / len(tog_vals)
            pct = (mean_t - mean_b) / mean_b * 100 if mean_b else float("nan")
            print(f"  {metric:12} {wl_name:16} default={mean_b:9.2f} toggled={mean_t:9.2f} "
                  f"({pct:+7.2f}%)  wins={wins:2} harms={harms:2} ties={ties:2}  "
                  f"sign_p={format_p(p):>8}  wilcoxon_p={format_p(wr['p']):>8}")
    return rows


def main():
    all_rows = []
    all_rows += measure_toggle("A_busy_factor", periodic_patch=periodic_balance_with_busy_factor)
    all_rows += measure_toggle("B_idle_gate", balance_domain_patch=balance_domain_with_idle_gate)
    all_rows += measure_toggle("C_numa_imbalance_min", numa_const_override=(2, 3))

    with open(pathlib.Path(__file__).resolve().parent / "results_task8_pre_audit_impact.csv", "w", newline="") as f:
        cols = list(all_rows[0].keys())
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_rows)
    print("\nWrote results_task8_pre_audit_impact.csv")


if __name__ == "__main__":
    main()
