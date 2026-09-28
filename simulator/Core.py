"""
EEVDF-accurate analogs of what we verified in fair.c:

  - avg_vruntime() / eligibility / virtual deadline -> real EEVDF pick,
    replacing the earlier "just pick min(vruntime)" CFS-era simplification.
    Verified against place_entity() (avg_vruntime baseline, not min_vruntime)
    and the general eligibility/deadline shape confirmed via vruntime_eligible()
    and cross-checked against an external EEVDF writeup whose function names
    (pick_eevdf, update_deadline, calc_delta_fair) we independently confirmed
    exist in the real fair.c.
  - fixed TIME_SLICE -> stand-in for EEVDF's per-task requestable slice
    (real Linux lets a task ask for a different slice; we use one constant
    for every task -- note this in your limitations section)
  - tick_load()      -> stand-in for PELT, using the exact decay law
    verified in pelt.c: y^32 ~= 0.5, period = 1 (sim-ms)

  - UPDATED 2026-09-24: self.rq is now a TrackedRunQueue (EevdfTree.py)
    -- a real augmented rbtree, ported from kernel/sched/fair.c's
    pick_eevdf(), sitting alongside the plain list every other caller
    in this codebase already used (matching real Linux's own dual
    cfs_rq->tasks_timeline / cfs_rq->tasks design). See pick_next()
    and avg_vruntime() below, and EevdfTree.py's module docstring for
    what this does and does not buy us: real O(log n) DECISIONS,
    with zero simulated time charged for making them, same as before
    -- see the comment in run() at the pick_next() call site.
"""

from Task import NICE_0_WEIGHT
from EevdfTree import TrackedRunQueue

TIME_SLICE = 4  # ms, fixed quantum standing in for EEVDF's per-task slice

PELT_HALFLIFE_PERIODS = 32
PELT_DECAY_PER_TICK = 0.5 ** (1 / PELT_HALFLIFE_PERIODS)  # ~0.9781, verified from pelt.c


class Core:
    def __init__(self, core_id, load_model="legacy"):
        self.core_id = core_id
        self.env = None
        self.parent = None          # set by Domain

        self.rq = TrackedRunQueue()  # plain list + a real augmented rbtree kept in sync (EevdfTree.py)
        self.current_task = None

        # FIX B (2026-09-26, see Readme.md): "legacy" (default, every
        # existing result stays valid) treats the PELT instantaneous
        # signal as pure running/not-running (0 or NICE_0_WEIGHT),
        # queue-blind -- Task 6 prep found a core with 27 queued tasks
        # has the SAME load() trajectory as one with 0 queued, since
        # only current_task is checked. "runnable" sums the weight of
        # EVERY runnable task (running + queued), matching real Linux's
        # cfs_rq->avg.load_avg / runnable_avg (which accumulate over all
        # entities on the runqueue, not just curr) -- see tick_load()
        # below.
        self.load_model = load_model
        self.load_avg = 0.0         # PELT-style decaying load, verified decay law
        # FIX C (2026-09-27, see Readme.md): SEPARATE from load_avg --
        # real Linux tracks util_avg (RUNNING-only) and load_avg/
        # runnable_avg (running+queued) as genuinely distinct PELT sums
        # (cfs_rq->avg.util_avg vs .load_avg/.runnable_avg). Fix B's
        # load_avg (when load_model="runnable") answers "how much
        # runnable weight is on this core"; util_avg answers "how much
        # of this core's capacity is actually being consumed by
        # execution" -- migrate_util (Fix C) needs the latter
        # specifically, not the former. Always tracked (cheap, and nothing
        # reads it unless imbalance_model="kernel" is also on).
        self.util_avg = 0.0
        self.total_busy_time = 0
        self.completed_task_list = []

        # cost/success-rate tracking, stand-in for sd->newidle_ratio /
        # sd->max_newidle_lb_cost gating we verified in sched_balance_newidle()
        self.newidle_cost_avg = 0.5

        # ADDED 2026-09-26 (LoadBalancer newidle_mode="transition"): tracks
        # whether this core has already made its one newidle attempt for
        # the CURRENT idle spell. Only consulted when the attached
        # balancer's newidle_mode is "transition" -- see run() below and
        # Readme.md's Task 3b entry for why (legacy_ema's every-idle-tick
        # polling turned out to self-suppress; this models the real
        # busy->idle transition trigger instead).
        self._newidle_tried_this_spell = False

        self.balancer = None

    # ---------------- rq management ----------------

    def avg_vruntime(self):
        """
        V, the weighted-average vruntime -- verified as the real placement
        baseline in place_entity() (avg_vruntime(cfs_rq), not min_vruntime).

        UPDATED 2026-09-24: now delegates to EevdfTree.avg_vruntime(),
        which tracks the weighted sum/load INCREMENTALLY (O(1) per
        enqueue/dequeue, updated inside TrackedRunQueue.append/remove)
        instead of resumming every runnable task from scratch on every
        call, the way this method used to (previously O(n) here; real
        Linux's own avg_vruntime(cfs_rq) is incremental for the same
        reason -- see avg_vruntime_add()/avg_vruntime_sub() in
        EevdfTree.py's module docstring). `current_task` is passed in
        explicitly because it's not IN the tree while it's running --
        same as real Linux's curr, folded in at read time because it's
        still part of the fair-share population even though it isn't
        a tree node right now (previously handled here by
        `_runnable()`, now removed since this was its only caller).
        """
        return self.rq.tree.avg_vruntime(curr=self.current_task)

    def _set_deadline(self, task):
        """deadline = vruntime + slice/weight   """
        vslice = TIME_SLICE * (NICE_0_WEIGHT / task.weight)
        task.sched_deadline = task.vruntime + vslice


    def enqueue(self, task, vruntime_baseline=None):
        """
        Place a new/waking/migrated task. Baseline defaults to this core's
        own avg_vruntime (V) -- matches place_entity()'s verified behavior.

        In the current call graph this is only ever invoked for a
        BRAND-NEW task's initial placement (Main.place()) -- migrations
        and preemption-requeues append to `rq` directly, bypassing this
        method -- so the util_avg initialization below runs exactly
        once per task, matching real Linux calling
        post_init_entity_util_avg() only at fork/exec, never again.
        """
        baseline = vruntime_baseline if vruntime_baseline is not None else self.avg_vruntime()
        task.vruntime = max(task.vruntime, baseline)
        self._set_deadline(task)
        task.util_avg = self._initial_util_avg(task)
        self.rq.append(task)

    def _initial_util_avg(self, task):
        """
        FIX C (2026-09-27, see Readme.md): port of
        post_init_entity_util_avg() (fair.c:1315-1351, kernel v7.2).
        A brand-new task's util_avg is extrapolated from THIS core's
        current util_avg, capped at half the remaining capacity budget
        -- not zero. cpu_scale = NICE_0_WEIGHT (this sim's capacity-scale
        constant, numerically 1024, same value Topology.CAPACITY_SCALE
        uses -- not imported here to avoid a Core<->Topology import
        cycle; Topology.py already imports Core lazily for the same
        reason).

            cap = (cpu_scale - util_avg) / 2
            if cap <= 0: return 0          # this core already saturated
            if util_avg != 0:
                return min(util_avg * task.weight / (load_avg + 1), cap)
            return cap                     # core truly empty -> half of full capacity
        """
        cap = (NICE_0_WEIGHT - self.util_avg) / 2.0
        if cap <= 0:
            return 0.0
        if self.util_avg != 0:
            extrapolated = self.util_avg * task.weight / (self.load_avg + 1)
            return min(extrapolated, cap)
        return cap


    def pick_next(self):
        """
        Real EEVDF pick: among ELIGIBLE tasks (vruntime <= V, i.e. not ahead
        of their fair share), pick the one with the EARLIEST virtual deadline.

        UPDATED 2026-09-24: this now walks a real augmented rbtree
        (EevdfTree.pick_eevdf(), ported verbatim from kernel/sched/
        fair.c's pick_eevdf() -- see that file's module docstring for
        the sourced algorithm and what's faithfully ported vs. what's a
        documented, justified simplification). 

        DISPATCH LATENCY is still not modeled: see the comment in
        run() at this method's call site. The tree nw lets you COUNT
        the real work done per pick (self.rq.tree.last_pick_nodes_visited,
        .last_pick_depth, cumulative .total_rotations/.total_picks) --
        genuine, language-independent facts about the algorithm's
        shape. It still can't tell  CPU cycles: that depends on
        cache behavior, branch prediction, and compiled-code layout on
        real silicon, none of which a node-visit count (or Python's own
        execution time, which is not a fixed multiple of C's) can
        stand in for. See EevdfTree.py's module docstring for the full
        reasoning -- this is a documented, deliberate limitation, not
        an oversight.
        """
        if not self.rq:
            return None
        task = self.rq.tree.pick_eevdf()
        self.rq.remove(task)
        return task

    def load(self):
        return self.load_avg

    def is_idle(self):
        return self.current_task is None and not self.rq

    def running_count(self):
        return len(self.rq) + (1 if self.current_task is not None else 0)

    # ---------------- PELT-style load ----------------

    def tick_load(self, dt):
        # FIX B (see __init__ comment, Readme.md): "runnable" sums the
        # weight of every runnable task -- current_task plus everything
        # still queued -- matching real Linux's cfs_rq->avg.load_avg
        # (accumulated over cfs_rq->avg.runnable_avg's population, not
        # just curr). "legacy" keeps the queue-blind 0/NICE_0_WEIGHT
        # signal exactly as before.
        if self.load_model == "runnable":
            instant = sum(t.weight for t in self.rq)
            if self.current_task is not None:
                instant += self.current_task.weight
        else:
            instant = (1.0 if self.current_task is not None else 0.0) * NICE_0_WEIGHT
        decay = PELT_DECAY_PER_TICK ** dt
        self.load_avg = self.load_avg * decay + (1 - decay) * instant

        # FIX C (see __init__ comment): util_avg is RUNNING-only, always
        # 0 or NICE_0_WEIGHT regardless of load_model -- a task's own
        # weight doesn't change how much of the CPU IT ALONE consumes
        # while running unopposed (weight affects scheduling FREQUENCY
        # relative to competitors, not per-slice capacity usage), so
        # this deliberately does NOT use load_model="runnable"'s
        # weighted-sum logic even when that's active.
        util_instant = NICE_0_WEIGHT if self.current_task is not None else 0.0
        self.util_avg = self.util_avg * decay + (1 - decay) * util_instant

    # ---------------- main loop ----------------

    def run(self, env):
        self.env = env
        while True:
            if self.current_task is None:
                # NOTE: pick_next() and try_newidle() below run with no
                # env.timeout() around them -- they cost zero SIMULATED
                # time, no matter how much real wall-clock computation
                # they involve. SimPy's clock only advances on an
                # explicit timeout (see the two yield env.timeout(...)
                # calls in this method), so a core never accrues
                # "idle while the scheduler decides" time here. STILL
                # true after 2026-09-24's real rbtree (EevdfTree.py):
                # pick_next() now does real O(log n) work instead of
                # O(n), and that work is genuinely countable (see
                # self.rq.tree.last_pick_nodes_visited/.last_pick_depth)
                # -- but none of it is charged any simulated time here,
                # by design (see EevdfTree.py's module docstring for
                # why "how much work" and "how long that takes" are
                # deliberately different, unanswered questions).
                task = self.pick_next()

                if task is None:
                    pulled = False
                    if self.balancer is not None:
                        if self.balancer.newidle_mode == "transition":
                            # Attempt exactly once per busy->idle transition
                            # (real Linux's trigger), not on every idle tick
                            # (legacy_ema's polling, which self-suppresses --
                            # see Readme.md's 2026-09-26f/3b entries). A core
                            # that stays idle after this one attempt relies
                            # on periodic balancing, same as real Linux's
                            # nohz idle balancing kicks rather than repolling.
                            if not self._newidle_tried_this_spell:
                                pulled = self.balancer.try_newidle(self, env.now)
                                self._newidle_tried_this_spell = True
                        else:
                            pulled = self.balancer.try_newidle(self, env.now)
                    if pulled:
                        task = self.pick_next()

                if task is None:
                    self.tick_load(1)
                    yield env.timeout(1)
                    continue

                self.current_task = task
                self._newidle_tried_this_spell = False  # ends this idle spell; next one gets its own attempt
                if task.start_time is None:
                    task.start_time = env.now

            task = self.current_task
            slice_len = min(TIME_SLICE, task.remaining_time)

            self.tick_load(slice_len)
            yield env.timeout(slice_len)

            task.remaining_time -= slice_len
            task.vruntime += slice_len * (NICE_0_WEIGHT / task.weight)
            self.total_busy_time += slice_len

            if task.remaining_time <= 1e-9:
                task.finish_time = env.now
                self.completed_task_list.append(task)
                self.current_task = None
            else:
                # preempted at slice boundary -> back into rq
                self._set_deadline(task)  # verified: deadline recomputed each dispatch
                self.rq.append(task)
                self.current_task = None