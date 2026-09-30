"""
Task 10a -- mechanism check for the burst gate. DIAGNOSTIC ONLY: no
simulator file is edited, no default/selection/rule changes. Answers
"how often would a machine-wide gap/idle check have blocked a burst
trigger, and how many of that trigger's burst migrations would that
have removed" -- for four candidate checks, evaluated at the exact
moment a burst trigger passes its cooldown (before the domain_chain
walk that trigger causes), on the REAL machine state at that instant.

This is NOT Task 9b's burst_gap_gate (a per-DOMAIN gate comparing the
burst core to the least-loaded OTHER core in THAT domain, using
Core.running_count()). This is a hypothetical per-TRIGGER gate,
evaluated once against the WHOLE machine's core states, never actually
installed -- burst_gap_gate=False for every run here, so nothing in
this script's runs is gated at all; the checks are computed and
recorded, not applied.

  C1: exists a core with nr_running <= nr_running(burst core) - 2
      (equivalently: max_gap = nr_running(burst core) - min(nr_running
      over all cores) >= 2)
  C2: same with -3 (max_gap >= 3)
  C3: at least one core is idle (idle_cores >= 1)
  C4: C3 OR C2

A check "blocks" a trigger when it evaluates False (no core meets the
condition) -- exactly Task 9b's logic direction (the gate only lets a
migration proceed when its condition holds).

HOW THE SNAPSHOT IS TAKEN WITHOUT EDITING BurstScheduler.py: on_task_
placed() is replaced, for the duration of each run only, with a
line-for-line copy of the current method (verified against
simulator/BurstScheduler.py at the time this script was written) with
exactly two additive insertions -- a read-only state snapshot right
after `self.burst_triggers += 1` and before the `for domain in
domain_chain(core):` loop (i.e. exactly where the real
docs/NOTEBOOK.md 2026-09-29h burst_gap_gate would splice in, one level
up: machine-wide instead of per-domain), and a read-only record append
after that same loop ends. Every other line, in the same order, with
the same conditions, is unchanged -- nothing about what migrations
happen, when, or to which core, is altered. The class method is
restored immediately after each run_simulation() call.

Workloads: bursty_high_s64, stacked_high, stacked_medium, rate1.5_s12,
rate3.0_s12 (matches final_results/2_confirmation/task6_confirmation_
run.py's WORKLOADS definitions for these five keys). 5 seeds each,
96000-96004/96100-96104/96200-96204/96300-96304/96400-96404 -- kept
100 apart per workload, this repo's standing convention, all within
the "96000+" block the user asked for and never used elsewhere.

Setup for every run: BurstAwareLoadBalancer, q4_a1.5_and (the v3
selected config), penalty_model="ran_only", burst_gap_gate=False,
migration_penalty=0.

Reports ONLY decision counts and machine state -- no p95_wait,
avg_wait, slowdown, or harm statistic appears anywhere in this script
or its output, by design (this is a mechanism check, not a
performance comparison).
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
import csv
import json
from collections import Counter

from Main import run_simulation
from BurstScheduler import BurstAwareLoadBalancer
from Topology import domain_chain, STATS

N_SEEDS = 5

WORKLOADS = {
    "bursty_high_s64": dict(profile="bursty", intensity="high",
                             overrides={"burst_size": 64, "burst_duration": 64 / 3.75},
                             n_tasks=640, seed_base=96000),
    "stacked_high":    dict(profile="stacked_burst", intensity="high", overrides=None,
                             n_tasks=200, seed_base=96100),
    "stacked_medium":  dict(profile="stacked_burst", intensity="medium", overrides=None,
                             n_tasks=200, seed_base=96200),
    "rate1.5_s12":     dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 12, "arrival_rate_during_burst": 1.5,
                                        "burst_duration": 12 / 1.5, "inter_burst_interval": 60},
                             n_tasks=120, seed_base=96300),
    "rate3.0_s12":     dict(profile="stacked_burst", intensity="medium",
                             overrides={"burst_size": 12, "arrival_rate_during_burst": 3.0,
                                        "burst_duration": 12 / 3.0, "inter_burst_interval": 60},
                             n_tasks=120, seed_base=96400),
}

_SELECTED_CONFIG_PATH = (pathlib.Path(__file__).resolve().parents[2] / "final_results"
                          / "1_calibration_grid" / "selected_config_v3.json")
with open(_SELECTED_CONFIG_PATH) as f:
    _sel = json.load(f)["selected"]
assert (_sel["queue_growth_threshold"], _sel["arrival_rate_threshold"], _sel["combine"]) == (4, 1.5, "and"), \
    f"selected_config_v3.json changed since this script was written: {_sel}"
SELECTED_KWARGS = dict(queue_growth_threshold=4, arrival_rate_threshold=1.5, combine="and")


def make_traced_on_task_placed(records):
    """Line-for-line copy of BurstScheduler.BurstAwareLoadBalancer.
    on_task_placed (as of 2026-09-30), with exactly two additive,
    read-only insertions marked below. See module docstring."""

    def traced(self, core, now):
        pair_domain = core.parent
        if pair_domain is None:
            return

        STATS.burst_check_calls += 1
        self.detector.record_arrival(pair_domain, now)
        triggered, rate, growth = self.detector.is_burst(pair_domain, core)

        if self.logger:
            self.logger.log(now, "burst_check", core=core.core_id,
                             arrival_rate=rate, queue_growth=growth, triggered=triggered)

        if not triggered:
            return

        self.detector_fires += 1
        self.detector_fire_times.append(now)

        last = self._cooldown.get(id(pair_domain), -1e9)
        if now - last < self.detector.arrival_window:
            return  # already handled this burst, don't re-trigger every arrival
        self._cooldown[id(pair_domain)] = now

        self.burst_triggers += 1
        if self.logger:
            self.logger.log(now, "burst_trigger", core=core.core_id,
                             arrival_rate=rate, queue_growth=growth)

        # ============ Task 10a snapshot (INSERTED, read-only) ============
        all_cores = list(self.cores_by_id.values())
        nr_burst = core.running_count()
        nr_list = [c.running_count() for c in all_cores]
        idle_cores = sum(1 for c in all_cores if c.is_idle())
        min_nr = min(nr_list)
        max_gap = nr_burst - min_nr
        pre_burst_migrations = self.burst_triggered_migrations
        # ============ end inserted block ============

        for domain in domain_chain(core):
            STATS.burst_balance_levels_walked += 1
            if self.burst_gap_gate:
                others = [c for c in domain.cores() if c is not core]
                if not others:
                    continue
                dst = min(others, key=lambda c: (c.running_count(), c.core_id))
                gap = core.running_count() - dst.running_count()
                if gap < 2:
                    self.gate_skipped_domains += 1
                    continue
                checker = dst
            else:
                checker = self._find_checker(domain)
            n = self._balance_domain(domain, checker, now, tag="burst")
            if n:
                self.burst_triggered_migrations += n
                if self.burst_resets_timer:
                    if self.per_cpu_last_balance:
                        self._balance_state(checker, domain)["last_balance"] = now
                    else:
                        domain.last_balance = now
                if self.logger:
                    self.logger.log(now, "burst_migration_effective", domain=domain.name, n=n)

        # ============ Task 10a record (INSERTED, read-only) ============
        burst_migrations = self.burst_triggered_migrations - pre_burst_migrations
        records.append(dict(
            t=now, burst_core=core.core_id, nr_running_burst=nr_burst,
            idle_cores=idle_cores, min_nr_running=min_nr, max_gap=max_gap,
            burst_migrations=burst_migrations,
            c1_block=max_gap < 2, c2_block=max_gap < 3, c3_block=idle_cores < 1,
            c4_block=(idle_cores < 1) and (max_gap < 3),
        ))
        # ============ end inserted block ============

    return traced


def run_one(wl_cfg, seed, records):
    original = BurstAwareLoadBalancer.on_task_placed
    BurstAwareLoadBalancer.on_task_placed = make_traced_on_task_placed(records)
    try:
        run_simulation(
            wl_cfg["profile"], BurstAwareLoadBalancer, intensity_level=wl_cfg["intensity"], seed=seed,
            balancer_kwargs=dict(SELECTED_KWARGS, penalty_model="ran_only",
                                  burst_gap_gate=False, migration_penalty=0),
            intensity_overrides=wl_cfg["overrides"], n_tasks=wl_cfg["n_tasks"],
        )
    finally:
        BurstAwareLoadBalancer.on_task_placed = original


CHECKS = ["c1", "c2", "c3", "c4"]


def summarize(workload, records):
    n = len(records)
    row = dict(workload=workload, n_triggers=n)
    total_migrations = sum(r["burst_migrations"] for r in records)
    row["total_burst_migrations"] = total_migrations
    for chk in CHECKS:
        blocked = [r for r in records if r[f"{chk}_block"]]
        n_blocked = len(blocked)
        migs_removed = sum(r["burst_migrations"] for r in blocked)
        row[f"{chk}_block_pct"] = round(100 * n_blocked / n, 1) if n else None
        row[f"{chk}_migs_removed"] = migs_removed
        row[f"{chk}_migs_removed_pct"] = (round(100 * migs_removed / total_migrations, 1)
                                           if total_migrations else None)
    idle_buckets = Counter(min(r["idle_cores"], 3) for r in records)
    for b in (0, 1, 2, 3):
        label = "3+" if b == 3 else str(b)
        row[f"idle_{label}_pct"] = round(100 * idle_buckets.get(b, 0) / n, 1) if n else None
    gap_buckets = Counter(min(r["max_gap"], 5) for r in records)
    for b in range(6):
        label = "5+" if b == 5 else str(b)
        row[f"gap_{label}_pct"] = round(100 * gap_buckets.get(b, 0) / n, 1) if n else None
    return row


def main():
    all_records = []
    summary_rows = []
    for wl, wl_cfg in WORKLOADS.items():
        seeds = [wl_cfg["seed_base"] + i for i in range(N_SEEDS)]
        wl_records = []
        for seed in seeds:
            per_seed_records = []
            run_one(wl_cfg, seed, per_seed_records)
            for r in per_seed_records:
                r2 = dict(workload=wl, seed=seed, **r)
                all_records.append(r2)
                wl_records.append(r2)
        summary_rows.append(summarize(wl, wl_records))
        print(f"{wl:16} seeds={seeds}  n_triggers={len(wl_records)}  "
              f"total_burst_migrations={sum(r['burst_migrations'] for r in wl_records)}")

    with open("results_task10_check_mechanism_perseed.csv", "w", newline="") as f:
        cols = ["workload", "seed", "t", "burst_core", "nr_running_burst", "idle_cores",
                "min_nr_running", "max_gap", "burst_migrations",
                "c1_block", "c2_block", "c3_block", "c4_block"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_records)
    print("\nWrote results_task10_check_mechanism_perseed.csv "
          f"({len(all_records)} per-trigger records)")

    with open("results_task10_check_mechanism_summary.csv", "w", newline="") as f:
        cols = ["workload", "n_triggers", "total_burst_migrations"]
        for chk in CHECKS:
            cols += [f"{chk}_block_pct", f"{chk}_migs_removed", f"{chk}_migs_removed_pct"]
        cols += ["idle_0_pct", "idle_1_pct", "idle_2_pct", "idle_3+_pct"]
        cols += [f"gap_{b if b < 5 else '5+'}_pct" for b in range(6)]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(summary_rows)
    print("Wrote results_task10_check_mechanism_summary.csv")

    print("\n" + "=" * 150)
    print(f"{'workload':16} {'n_trig':>7} {'tot_mig':>8} | "
          f"{'C1 blk%':>8} {'C1 rm':>6} | {'C2 blk%':>8} {'C2 rm':>6} | "
          f"{'C3 blk%':>8} {'C3 rm':>6} | {'C4 blk%':>8} {'C4 rm':>6} | "
          f"{'idle0%':>7} {'idle1%':>7} {'idle2%':>7} {'idle3+%':>8}")
    for row in summary_rows:
        print(f"{row['workload']:16} {row['n_triggers']:7} {row['total_burst_migrations']:8} | "
              f"{row['c1_block_pct']:8.1f} {row['c1_migs_removed']:6} | "
              f"{row['c2_block_pct']:8.1f} {row['c2_migs_removed']:6} | "
              f"{row['c3_block_pct']:8.1f} {row['c3_migs_removed']:6} | "
              f"{row['c4_block_pct']:8.1f} {row['c4_migs_removed']:6} | "
              f"{row['idle_0_pct']:7.1f} {row['idle_1_pct']:7.1f} {row['idle_2_pct']:7.1f} {row['idle_3+_pct']:8.1f}")

    gap_cols = [f"gap_{b if b < 5 else '5+'}_pct" for b in range(6)]
    print("\nmax_gap distribution (%):")
    print(f"{'workload':16} " + " ".join(f"{('gap='+str(b) if b < 5 else 'gap>=5'):>8}" for b in range(6)))
    for row in summary_rows:
        print(f"{row['workload']:16} " + " ".join(f"{row[col]:8.1f}" for col in gap_cols))


if __name__ == "__main__":
    main()
