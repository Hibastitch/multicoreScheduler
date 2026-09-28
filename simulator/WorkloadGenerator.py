
"""
Five named profiles, each parameterized by the four burst-intensity
knobs the spec requires: burst_size, burst_duration,
arrival_rate_during_burst, inter_burst_interval. INTENSITY_LEVELS gives
low/medium/high presets; pass overrides to vary one parameter at a
time for the RQ3 sweep.

Ground truth: `self.ground_truth_bursts` records (start, end, n_tasks)
for every burst actually emitted. This is for the EVALUATION HARNESS
ONLY (burst response latency) -- never pass it to a scheduler/detector.

RNG ISOLATION (Fix 3d, 2026-09-26): a same-seed "paired" baseline vs
burst-aware comparison is only valid if both runs see the IDENTICAL
task stream. Investigation found that when task generation and
scheduling code shared Python's global `random` module, a scheduler-
dependent call elsewhere (the legacy newidle gate) could consume a
different number of draws between the two runs, silently diverging the
"same seed" workload partway through -- see Readme.md's Fix 3d entry
for the full story and LoadBalancer.py for the scheduling-side half of
the fix. The fix here has two layers, either of which would suffice on
its own, kept together for robustness:
  1. This class owns a PRIVATE `random.Random` instance (`self.rng`),
     never the shared `random` module -- so nothing outside this class
     can perturb its draws, regardless of what future code does.
  2. `generate_plan()` computes the ENTIRE task arrival stream (times,
     sizes, weights, entry cores, deadlines) in one synchronous pass
     BEFORE any simpy process (in particular, any Core) has run at all.
     `run_profile()` then just plays that fixed plan back through simpy
     timeouts. This makes it structurally impossible for scheduling
     timing to affect what gets generated, not just statistically
     unlikely -- generation is finished before there's anything for it
     to be perturbed BY.
"""

import random
from Task import Task

INTENSITY_LEVELS = {
    "low":    dict(burst_size=4,  burst_duration=8, arrival_rate_during_burst=0.5,  inter_burst_interval=60),
    "medium": dict(burst_size=12, burst_duration=8, arrival_rate_during_burst=1.5,  inter_burst_interval=35),
    "high":   dict(burst_size=30, burst_duration=8, arrival_rate_during_burst=3.75, inter_burst_interval=15),
}

PROFILE_NAMES = ["uniform", "bursty", "heavy_tail", "mixed", "deadline_driven"]

# Task 5 (2026-09-26), opt-in: kept OUT of PROFILE_NAMES deliberately, so
# Experiment.py's default full-grid run (`for profile in PROFILE_NAMES`)
# is completely unaffected and every existing results*.csv stays valid --
# stacked_burst is only reachable by a caller that names it explicitly
# (see generate_plan()'s validation below).
EXTRA_PROFILE_NAMES = ["stacked_burst"]

# Real kernel sched_prio_to_weight[] (kernel/sched/core.c), index = nice + 20.
# The literal 40-entry table, not a 1.25^nice approximation -- the real
# ratios aren't perfectly geometric at the tails, so this is the verified
# source of truth for nice -> weight, matching NICE_0_WEIGHT=1024 at nice=0.
SCHED_PRIO_TO_WEIGHT = [
    88761, 71755, 56483, 46273, 36291,  # nice -20..-16
    29154, 23254, 18705, 14949, 11916,  # nice -15..-11
     9548,  7620,  6100,  4904,  3906,  # nice -10..-6
     3121,  2501,  1991,  1586,  1277,  # nice  -5..-1
     1024,   820,   655,   526,   423,  # nice   0..4
      335,   272,   215,   172,   137,  # nice   5..9
      110,    87,    70,    56,    45,  # nice  10..14
       36,    29,    23,    18,    15,  # nice  15..19
]


def nice_to_weight(nice):
    return SCHED_PRIO_TO_WEIGHT[nice + 20]


# Real processes are overwhelmingly nice=0 -- that should stay the
# plurality outcome, not one option among many equally likely ones.
# ~50% mass on nice=0, modest +/-2 and +/-5 making up the rest; no
# extreme nice values, which are rare in practice and would make weight
# the dominant signal in every experiment instead of a secondary,
# realistic one.
DEFAULT_NICE_DISTRIBUTION = [-5, -2, 0, 0, 0, 0, 2, 5]


class WorkloadGenerator:

    def __init__(self, env, place_fn, n_tasks=200, cpu_time_range=(4, 40),
                 uniform_rate=0.5, deadline_slack=3.0, nice_distribution=None, cores=None,
                 rng=None):
        self.env = env
        self.place_fn = place_fn
        self.n_tasks = n_tasks
        self.cpu_time_range = cpu_time_range
        self.uniform_rate = uniform_rate          # tasks/ms, for uniform + mixed's background trickle
        self.deadline_slack = deadline_slack
        # Only used for burst entry-core selection, to give every task IN
        # ONE BURST the same entry core -- real bursts (make -j30, a
        # prefork server spawning workers) come from ONE parent process on
        # one CPU, not from 30 independently-random wakeup contexts.
        # Steady trickles (run_uniform, run_mixed's background spawns)
        # each still get their own independently-drawn entry core (see
        # _make_task_entry) -- the realistic model for arrivals that
        # genuinely don't share a common parent.
        self.cores = cores
        # On by default (not an opt-in flag): real processes aren't all
        # nice=0, and leaving weight uniform meant the EEVDF weighted-
        # fairness machinery and _migrate_load()'s "heaviest task" pick
        # were never actually exercised. See DEFAULT_NICE_DISTRIBUTION.
        self.nice_distribution = (
            nice_distribution if nice_distribution is not None else DEFAULT_NICE_DISTRIBUTION
        )
        # RNG ISOLATION (Fix 3d) -- see module docstring. Never falls back
        # to the shared `random` module; an unseeded private instance if
        # the caller doesn't supply one, which is still isolated from
        # everything else, just not reproducible run-to-run on its own.
        self.rng = rng if rng is not None else random.Random()
        self.next_id = 0
        self.ground_truth_bursts = []              # [(start, end, n)] -- EVALUATION ONLY
        self.plan = None                            # populated by generate_plan()

    # ---------------- task creation (planning-time: no simpy, no shared state) ----------------

    def _sample_cpu_time(self, heavy_tail=False):
        if not heavy_tail:
            return self.rng.randint(*self.cpu_time_range)
        lo, hi = self.cpu_time_range
        # Pareto-shaped service times: mostly short, occasional very long tasks
        alpha = 1.5
        val = lo + int(self.rng.paretovariate(alpha))
        return min(val, hi * 6)

    def _sample_weight(self):
        nice = self.rng.choice(self.nice_distribution)
        return nice_to_weight(nice)

    def _make_task_entry(self, now, heavy_tail=False, with_deadline=False, entry_core=None,
                          direct_core=None):
        """Draws one task's cpu_time/weight/(deadline)/(entry_core if not
        already fixed by the caller, e.g. for a shared burst entry core).
        Returns a plain dict -- the plan's unit -- not a Task (Task
        objects are only constructed at placement time, in run_profile()).

        `direct_core`, if set (stacked_burst -- see _plan_burst_stacked),
        means "enqueue directly on this core, bypassing placement
        entirely" -- a stronger instruction than `entry_core`, which is
        only the starting CPU for the fork-path top-down descent
        (hierarchical_new_task_placement) that still goes through
        select_core_for_task() -- NOT a wake-affine hint; wake-affine is
        dead code in this simulator (see Placement.py). Always present
        (default None) so every plan entry has a uniform shape for
        run_profile()/place_fn()."""
        cpu_time = self._sample_cpu_time(heavy_tail)
        weight = self._sample_weight()
        deadline = (now + self.deadline_slack * cpu_time) if with_deadline else None
        if entry_core is None and self.cores:
            entry_core = self.rng.choice(self.cores)
        entry = dict(task_id=self.next_id, arrival_time=now, cpu_time=cpu_time,
                     weight=weight, deadline=deadline, entry_core=entry_core,
                     direct_core=direct_core)
        self.next_id += 1
        return entry

    # ---------------- arrival shapes (planning-time: pure computation, `now` threaded explicitly) ----------------

    def _plan_burst(self, now, size, duration, rate, heavy_tail=False, with_deadline=False):
        """Computes one burst's arrival timing/entries synchronously with
        an explicit `now` argument (no simpy involved -- see module
        docstring's RNG-isolation section). Returns (entries, end_time)."""
        start = now
        entries = []
        emitted = 0
        gap = (1.0 / rate) if rate > 0 else (duration / max(size, 1))
        # One shared entry core for every task in THIS burst -- matches a
        # real fork storm (make -j30, a prefork server spawning workers),
        # where every child starts from the same parent process's CPU.
        burst_entry_core = self.rng.choice(self.cores) if self.cores else None
        while emitted < size and (now - start) < duration and self.next_id < self.n_tasks:
            entries.append(self._make_task_entry(
                now, heavy_tail=heavy_tail, with_deadline=with_deadline, entry_core=burst_entry_core,
            ))
            emitted += 1
            if emitted < size:
                now += gap
        end = now
        if emitted > 0:
            self.ground_truth_bursts.append((start, end, emitted))
        return entries, end

    def _plan_burst_stacked(self, now, size, duration, rate, heavy_tail=False, with_deadline=False):
        """Task 5 (`stacked_burst`, opt-in -- see docs/NOTEBOOK.md): like
        _plan_burst(), but sets `direct_core` (bypasses fork-path
        placement's top-down descent ENTIRELY, straight onto one core)
        instead of `entry_core` (a placement HINT that still goes
        through select_core_for_task()). `direct_core` is chosen at
        RANDOM, once per burst (self.rng.choice(self.cores)) -- not a
        single core fixed for the whole run, and not per task. Models
        wakeup stacking / CPU affinity -- every task in ONE burst lands
        on that burst's SAME (randomly chosen) core regardless of what
        placement would have chosen, the deliberately worst-case
        scenario for a load balancer to have to fix after the fact,
        since placement itself does nothing to spread this load."""
        start = now
        entries = []
        emitted = 0
        gap = (1.0 / rate) if rate > 0 else (duration / max(size, 1))
        stack_core = self.rng.choice(self.cores) if self.cores else None
        while emitted < size and (now - start) < duration and self.next_id < self.n_tasks:
            entries.append(self._make_task_entry(
                now, heavy_tail=heavy_tail, with_deadline=with_deadline,
                entry_core=stack_core, direct_core=stack_core,
            ))
            emitted += 1
            if emitted < size:
                now += gap
        end = now
        if emitted > 0:
            self.ground_truth_bursts.append((start, end, emitted))
        return entries, end

    def _plan_stacked_burst(self, intensity):
        """Same burst timing as _plan_bursty() (inter_burst_interval/
        burst_size/burst_duration/arrival_rate_during_burst), but each
        burst is planned via _plan_burst_stacked() instead of
        _plan_burst()."""
        entries = []
        now = 0.0
        while self.next_id < self.n_tasks:
            now += intensity["inter_burst_interval"]
            if self.next_id >= self.n_tasks:
                break
            burst_entries, now = self._plan_burst_stacked(
                now, intensity["burst_size"], intensity["burst_duration"],
                intensity["arrival_rate_during_burst"],
            )
            entries.extend(burst_entries)
        return entries

    def _plan_uniform(self):
        entries = []
        now = 0.0
        interval = 1.0 / self.uniform_rate
        while self.next_id < self.n_tasks:
            entries.append(self._make_task_entry(now))
            now += interval
        return entries

    def _plan_bursty(self, intensity, heavy_tail=False, with_deadline=False):
        entries = []
        now = 0.0
        while self.next_id < self.n_tasks:
            now += intensity["inter_burst_interval"]
            if self.next_id >= self.n_tasks:
                break
            burst_entries, now = self._plan_burst(
                now, intensity["burst_size"], intensity["burst_duration"],
                intensity["arrival_rate_during_burst"],
                heavy_tail=heavy_tail, with_deadline=with_deadline,
            )
            entries.extend(burst_entries)
        return entries

    def _plan_mixed(self, intensity):
        """Background uniform trickle, occasionally interrupted by a burst."""
        entries = []
        now = 0.0
        bg_interval = 1.0 / (self.uniform_rate * 0.5)
        next_burst_at = now + intensity["inter_burst_interval"] * 2
        while self.next_id < self.n_tasks:
            if now >= next_burst_at:
                burst_entries, now = self._plan_burst(
                    now, intensity["burst_size"], intensity["burst_duration"],
                    intensity["arrival_rate_during_burst"],
                )
                entries.extend(burst_entries)
                next_burst_at = now + intensity["inter_burst_interval"] * 2
                continue
            if self.next_id < self.n_tasks:
                entries.append(self._make_task_entry(now))
            now += bg_interval
        return entries

    # ---------------- entry point ----------------

    def generate_plan(self, name, intensity_level="medium", intensity_overrides=None):
        """Pre-computes the ENTIRE task arrival stream, synchronously,
        before run_profile() ever yields to simpy. See module docstring
        (Fix 3d) for why this is the robustness layer on top of the
        private-RNG isolation."""
        if name not in PROFILE_NAMES and name not in EXTRA_PROFILE_NAMES:
            raise ValueError(
                f"unknown profile {name!r}, expected one of {PROFILE_NAMES + EXTRA_PROFILE_NAMES}"
            )

        intensity = dict(INTENSITY_LEVELS[intensity_level])
        if intensity_overrides:
            intensity.update(intensity_overrides)

        if name == "uniform":
            entries = self._plan_uniform()
        elif name == "bursty":
            entries = self._plan_bursty(intensity)
        elif name == "heavy_tail":
            entries = self._plan_bursty(intensity, heavy_tail=True)
        elif name == "mixed":
            entries = self._plan_mixed(intensity)
        elif name == "deadline_driven":
            entries = self._plan_bursty(intensity, with_deadline=True)
        elif name == "stacked_burst":
            entries = self._plan_stacked_burst(intensity)

        self.plan = entries
        return entries

    def run_profile(self, name, intensity_level="medium", intensity_overrides=None):
        """Plays generate_plan()'s fixed plan back through simpy timeouts
        -- no randomness and no task-generation logic happens in here,
        only timing playback and Task construction/placement.

        If a caller already called generate_plan() itself (e.g. to read
        ground_truth_bursts before simpy starts, for a burst-onset
        watcher process), that plan is reused as-is rather than generated
        a second time -- generate_plan() is not idempotent on its own
        (it keeps advancing self.next_id/self.rng), so calling it twice
        would silently produce an empty second plan instead of the
        intended replay."""
        plan = self.plan if self.plan is not None else self.generate_plan(
            name, intensity_level, intensity_overrides,
        )
        now = 0.0
        for entry in plan:
            wait = entry["arrival_time"] - now
            if wait > 0:
                yield self.env.timeout(wait)
                now = entry["arrival_time"]
            task = Task(entry["task_id"], entry["arrival_time"], entry["cpu_time"],
                        weight=entry["weight"], deadline=entry["deadline"])
            self.place_fn(task, entry_core=entry["entry_core"], direct_core=entry["direct_core"])
