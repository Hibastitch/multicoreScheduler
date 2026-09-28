import math

from Topology import STATS


def _percentile(vals, pct):
    """Same convention as paired_compare.py's p95()/p99() -- ceil-based
    index, so both modules agree on what "p95" means for the same list."""
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, math.ceil(pct * len(s)) - 1))
    return s[idx]


class Metrics:

    def __init__(self, cores, simulation_time):
        self.cores = cores
        self.simulation_time = simulation_time

    def burst_response_latency(self, ground_truth_bursts, migration_events):
        """
        For each ground-truth burst (start, end, n), find the first
        migration event at or after `start`. Latency = that time - start.
        Ground truth is used HERE ONLY, for evaluation -- never fed to
        the scheduler/detector during the run.
        """
        if not ground_truth_bursts:
            return None, 0

        mig_times = sorted(e["t"] for e in migration_events)
        latencies = []
        unresponded = 0

        for start, end, n in ground_truth_bursts:
            responded = None
            for t in mig_times:
                if t >= start:
                    responded = t
                    break
            if responded is None:
                unresponded += 1
            else:
                latencies.append(responded - start)

        avg = sum(latencies) / len(latencies) if latencies else None
        return avg, unresponded

    def summary(self, balancer=None, ground_truth_bursts=None, migration_events=None):
        completed_tasks = []
        for core in self.cores:
            completed_tasks.extend(core.completed_task_list)

        total_tasks = len(completed_tasks)
        if total_tasks == 0:
            return {"completed": 0}

        waits = [t.start_time - t.arrival_time for t in completed_tasks]
        avg_wait = sum(waits) / total_tasks
        p95_wait = _percentile(waits, 0.95)
        p99_wait = _percentile(waits, 0.99)
        avg_turnaround = sum(t.finish_time - t.arrival_time for t in completed_tasks) / total_tasks
        makespan = max(t.finish_time for t in completed_tasks)
        avg_migrations = sum(t.migrations for t in completed_tasks) / total_tasks

        # Makespan floor (added 2026-09-26, see Readme.md): the earliest
        # ANY scheduler could possibly finish, if the single task that
        # arrives latest relative to its own length ran with zero wait
        # and zero preemption -- a per-task bound (arrival + cpu_time),
        # maxed over tasks, not a whole-machine capacity bound. makespan
        # can never beat this. makespan_excess is how much scheduling
        # overhead (queueing, preemption, migration) added on top of that
        # floor -- the part actually attributable to scheduling decisions,
        # as opposed to the floor itself, which is fixed by the workload's
        # last arrival and isn't something any scheduler could change.
        makespan_lower_bound = max(t.arrival_time + t.cpu_time for t in completed_tasks)
        makespan_excess = makespan - makespan_lower_bound

        slowdowns = [(t.finish_time - t.arrival_time) / t.cpu_time for t in completed_tasks]
        avg_slowdown = sum(slowdowns) / total_tasks
        p95_slowdown = _percentile(slowdowns, 0.95)

        utils = [
            (core.total_busy_time / self.simulation_time) * 100 if self.simulation_time else 0
            for core in self.cores
        ]

        total_migrations = balancer.migrations if balancer else None
        migration_rate = (total_migrations / self.simulation_time) if (total_migrations and self.simulation_time) else 0.0

        brl, unresponded = (None, 0)
        if ground_truth_bursts is not None and migration_events is not None:
            brl, unresponded = self.burst_response_latency(ground_truth_bursts, migration_events)

        deadline_tasks = [t for t in completed_tasks if t.deadline is not None]
        deadline_misses = [t for t in deadline_tasks if t.finish_time > t.deadline]
        n_deadline_tasks = len(deadline_tasks)
        deadline_miss_rate = (len(deadline_misses) / n_deadline_tasks) if n_deadline_tasks else None

        # EevdfTree instrumentation (added 2026-09-24): real, countable
        # "work done" by pick_eevdf() -- node visits, walk depth, and
        # rotations are language-independent facts about the algorithm's
        # shape. Deliberately NOT a cycle/time cost -- see EevdfTree.py's
        # module docstring for why that number can't be derived from this.
        tree_picks = sum(c.rq.tree.total_picks for c in self.cores)
        tree_nodes_visited = sum(c.rq.tree.total_nodes_visited for c in self.cores)
        tree_rotations = sum(c.rq.tree.total_rotations for c in self.cores)
        avg_nodes_per_pick = (tree_nodes_visited / tree_picks) if tree_picks else 0.0
        avg_lateness = (
            sum(t.finish_time - t.deadline for t in deadline_tasks) / n_deadline_tasks
            if n_deadline_tasks else None
        )

        # Topology.WorkStats instrumentation (added 2026-09-24): the same
        # kind of real, countable "work done" as the tree counters above,
        # but for every OTHER site that also runs with zero simulated
        # time charged -- periodic balancing's full-machine sweep every
        # tick, newly-idle's domain-chain classify() scans, new-task
        # placement's hierarchy descent, and burst-triggered balancing's
        # own domain walk. See Topology.WorkStats's docstring for why
        # none of this converts to a cycle/time cost either.
        sched_cores_scanned = STATS.classify_cores_scanned + STATS.checker_cores_scanned

        return {
            "completed": total_tasks,
            "avg_wait": avg_wait,
            "p95_wait": p95_wait,
            "p99_wait": p99_wait,
            "avg_turnaround": avg_turnaround,
            "makespan": makespan,
            "makespan_lower_bound": makespan_lower_bound,
            "makespan_excess": makespan_excess,
            "avg_slowdown": avg_slowdown,
            "p95_slowdown": p95_slowdown,
            "avg_migrations_per_task": avg_migrations,
            "total_migrations": total_migrations,
            "migration_rate_per_ms": migration_rate,
            "burst_response_latency": brl,
            "unresponded_bursts": unresponded,
            "n_ground_truth_bursts": len(ground_truth_bursts) if ground_truth_bursts else 0,
            "n_deadline_tasks": n_deadline_tasks,
            "deadline_miss_rate": deadline_miss_rate,
            "avg_lateness": avg_lateness,
            "tree_picks": tree_picks,
            "tree_nodes_visited": tree_nodes_visited,
            "tree_avg_nodes_per_pick": avg_nodes_per_pick,
            "tree_rotations": tree_rotations,
            "sched_cores_scanned": sched_cores_scanned,
            "periodic_calls": STATS.periodic_calls,
            "periodic_levels_walked": STATS.periodic_levels_walked,
            "newidle_calls": STATS.newidle_calls,
            "newidle_levels_walked": STATS.newidle_levels_walked,
            "placement_calls": STATS.placement_calls,
            # Renamed from "placement_levels_walked" (2026-09-26): it's a
            # per-run TOTAL, not a per-task average despite Experiment.py's
            # "_mean" column suffix -- see the comment on WorkStats's
            # placement_calls/placement_levels_walked for why it's
            # currently a deterministic constant (200*4=800) across every
            # row of every config, carrying no runtime-behavior signal.
            "placement_levels_walked_total": STATS.placement_levels_walked,
            "placement_levels_per_call": (
                STATS.placement_levels_walked / STATS.placement_calls
                if STATS.placement_calls else 0.0
            ),
            "burst_check_calls": STATS.burst_check_calls,
            "burst_balance_levels_walked": STATS.burst_balance_levels_walked,
            "classify_calls": STATS.classify_calls,
            "classify_cores_scanned": STATS.classify_cores_scanned,
            "checker_calls": STATS.checker_calls,
            "checker_cores_scanned": STATS.checker_cores_scanned,
            "cpu_util_mean": sum(utils) / len(utils) if utils else 0,
            "cpu_util_min": min(utils) if utils else 0,
            "cpu_util_max": max(utils) if utils else 0,
        }

    def print_results(self, balancer=None, ground_truth_bursts=None, migration_events=None):
        s = self.summary(balancer, ground_truth_bursts, migration_events)
        if s["completed"] == 0:
            print("No completed tasks.")
            return

        print(f"Completed Tasks:        {s['completed']}")
        print(f"Avg Wait Time:          {s['avg_wait']:.2f} ms")
        print(f"Avg Turnaround Time:    {s['avg_turnaround']:.2f} ms")
        print(f"Makespan:               {s['makespan']:.1f} ms  "
              f"(lower bound {s['makespan_lower_bound']:.1f}, excess {s['makespan_excess']:.1f})")
        print(f"Slowdown (turnaround/cpu_time): mean={s['avg_slowdown']:.2f}  p95={s['p95_slowdown']:.2f}")
        print(f"Avg Migrations/Task:    {s['avg_migrations_per_task']:.3f}")
        if s["total_migrations"] is not None:
            print(f"Total Migrations:      {s['total_migrations']}")
            print(f"Migration Rate:        {s['migration_rate_per_ms']:.4f} /ms")
        if s["n_ground_truth_bursts"]:
            brl = s["burst_response_latency"]
            brl_str = f"{brl:.2f} ms" if brl is not None else "N/A"
            print(f"Burst Response Latency: {brl_str}  "
                  f"({s['n_ground_truth_bursts']} bursts, {s['unresponded_bursts']} unresponded)")
        if s["n_deadline_tasks"]:
            print(f"Deadline Miss Rate:     {s['deadline_miss_rate'] * 100:.1f}%  "
                  f"({s['n_deadline_tasks']} tasks with a deadline)")
            print(f"Avg Lateness:           {s['avg_lateness']:.2f} ms  "
                  f"(negative = finished before deadline)")
        if s["tree_picks"]:
            print(f"EEVDF Tree Work:        {s['tree_avg_nodes_per_pick']:.2f} nodes/pick avg  "
                  f"({s['tree_picks']} picks, {s['tree_rotations']} rotations -- "
                  f"work done, not time; see EevdfTree.py)")
        print(f"Sched Scanning Work:     {s['sched_cores_scanned']} cores scanned total  "
              f"(periodic: {s['periodic_calls']} calls/{s['periodic_levels_walked']} levels, "
              f"newidle: {s['newidle_calls']}/{s['newidle_levels_walked']}, "
              f"placement: {s['placement_calls']}/{s['placement_levels_walked_total']}, "
              f"burst: {s['burst_check_calls']} checks/{s['burst_balance_levels_walked']} balance-levels "
              f"-- work done, not time; see Topology.WorkStats)")
        print(f"CPU Util (mean/min/max): {s['cpu_util_mean']:.1f}% / "
              f"{s['cpu_util_min']:.1f}% / {s['cpu_util_max']:.1f}%")