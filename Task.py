NICE_0_WEIGHT = 1024  # matches Linux's weight for nice=0


class Task:
    """
    Mirrors the fields of struct sched_entity that we actually verified
    against fair.c: vruntime, weight, and the previous-CPU anchor used
    by wake-affine placement.
    """

    __slots__ = (
        "task_id", "arrival_time", "cpu_time", "remaining_time",
        "vruntime", "weight", "prev_core", "util_avg",
        "start_time", "finish_time", "migrations", "deadline", "sched_deadline",
    )

    def __init__(self, task_id, arrival_time, cpu_time, weight=NICE_0_WEIGHT, deadline=None):
        self.task_id = task_id
        self.arrival_time = arrival_time
        self.cpu_time = cpu_time
        self.remaining_time = cpu_time
        self.vruntime = 0.0
        self.weight = weight
        # FIX C (2026-09-27, see Readme.md): set once, at this task's
        # FIRST enqueue only (Core.enqueue()), via a port of real Linux's
        # post_init_entity_util_avg() (fair.c:1315-1351, v7.2) -- a
        # brand-new task's util_avg is extrapolated from the destination
        # core's CURRENT util_avg, not zero. Migrations/preemption
        # re-queues bypass Core.enqueue() (they append to rq directly),
        # so this is never overwritten after the first placement, same
        # as real Linux only calling post_init_entity_util_avg() once,
        # at fork/exec.
        self.util_avg = 0.0

        self.prev_core = None      # id of core last ran on; None = brand new (fork-like)
        self.start_time = None
        self.finish_time = None
        self.migrations = 0
        self.deadline = deadline   # APPLICATION deadline, only for the deadline-driven profile
        self.sched_deadline = 0.0  # EEVDF's internal VIRTUAL scheduling deadline (unrelated to above)