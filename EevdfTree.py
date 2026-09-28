"""
A faithful port of Linux's EEVDF run-queue data structure
(kernel/sched/fair.c, cfs_rq->tasks_timeline), so Core.pick_next() can
walk a real augmented rbtree instead of the O(n) list scan it used
before 2026-09-24. Sourced verbatim from kernel/sched/fair.c (v6.11):

  - __entity_less()/entity_before(): the tree is keyed by DEADLINE, not
    vruntime. (The ORIGINAL 6.6 EEVDF merge kept it vruntime-keyed with
    a min_deadline augmentation instead; a later patch, "sched/eevdf:
    Sort the rbtree by virtual deadline," swapped the key and the
    augmented field to what's ported here. Both existed in real kernel
    history -- this ports the current one.)
  - min_vruntime_update()/__min_vruntime_update(): each node is
    augmented with min_vruntime = min(own vruntime, left.min_vruntime,
    right.min_vruntime) -- a vruntime-ordered "heap" layered on the
    deadline-ordered tree, used to prune subtrees during picking.
  - pick_eevdf(): check the cached leftmost node (smallest deadline)
    first; if it's eligible, done. Otherwise walk from the root, using
    the left child's min_vruntime to decide whether descending left
    could still find an eligible node, before ever visiting it.
  - avg_vruntime_add()/avg_vruntime_sub(): V (avg_vruntime) is tracked
    incrementally, O(1) per enqueue/dequeue -- not recomputed by
    summing every task on every read, which is what this sim's
    Core.avg_vruntime() did before this file existed.

Two deliberate, documented departures from the real C implementation --
NOT fidelity gaps, just kernel-performance tricks that don't apply
outside fixed-width, hot-path C:
  1. Real Linux offsets every vruntime by cfs_rq->min_vruntime (a
     monotonic floor) before summing, purely to keep 64-bit fixed-point
     arithmetic from overflowing. Python's arbitrary-precision numbers
     don't need that; V is tracked directly here, which is
     mathematically identical (offsetting by a constant changes neither
     a comparison nor an average).
  2. Real Linux's eligibility check cross-multiplies
     (avg >= (vruntime - min_vruntime) * load) specifically to avoid a
     division on every check. This just divides -- Python division
     isn't the bottleneck a scheduler hot path in C would be, and it's
     the same comparison after algebraic rearrangement.

LIMITATION -- the reason this measures WORK, not TIME: this module
counts exactly how many nodes pick_eevdf() visited, how deep the walk
went, how many rotations insert/remove triggered -- real, countable,
language-independent facts about the algorithm's shape. It cannot tell
you how many CPU CYCLES that cost. Cycles depend on cache behavior,
branch prediction, and machine code layout -- properties of compiled C
running on real silicon, not of this algorithm's shape, and not
something Python execution time can stand in for (interpreted Python
doing "compare two numbers" is not a fixed multiple slower than
compiled C doing the same thing; the ratio varies with whatever
surrounds it). No simulated time is charged for any of this, same as
before this file existed -- only the SHAPE of the work is now real.
"""

RED = 0
BLACK = 1


class _Node:
    __slots__ = ("task", "left", "right", "parent", "color", "min_vruntime")

    def __init__(self, task):
        self.task = task
        self.left = None
        self.right = None
        self.parent = None
        self.color = RED
        self.min_vruntime = task.vruntime if task is not None else float("inf")


class EevdfTree:
    """
    Augmented red-black tree keyed by task.sched_deadline, each node
    additionally tracking the minimum task.vruntime in its own subtree.
    Tracks the leftmost node for O(1) "smallest deadline" access, same
    as real Linux's rb_root_cached.

    One structural departure, noted here rather than buried: real Linux
    embeds the rb_node directly inside struct sched_entity. This wraps
    each Task in a separate _Node instead, to avoid bolting tree-
    internal fields (left/right/parent/color) onto Task.py, which
    exists specifically to mirror sched_entity's own verified fields,
    not rbtree plumbing. Behaviorally equivalent; different only in
    where the pointers live.
    """

    def __init__(self):
        self.NIL = _Node(None)
        self.NIL.color = BLACK
        self.NIL.min_vruntime = float("inf")
        self.NIL.left = self.NIL.right = self.NIL.parent = self.NIL
        self.root = self.NIL
        self.leftmost = self.NIL
        self._count = 0
        self._nodes = {}  # id(task) -> _Node, for O(1) removal by task identity

        self.avg_vruntime_sum = 0.0   # sum(task.vruntime * task.weight), verified: avg_vruntime_add/sub
        self.avg_load = 0.0           # sum(task.weight)

        # Instrumentation: real, countable "work done", not time.
        self.total_nodes_visited = 0
        self.total_rotations = 0
        self.total_picks = 0
        self.last_pick_nodes_visited = 0
        self.last_pick_depth = 0

    def __len__(self):
        return self._count

    def __bool__(self):
        return self._count > 0

    # ---------------- augmentation ----------------

    def _refresh(self, node):
        """Recompute one node's own min_vruntime from itself + both children."""
        m = node.task.vruntime
        if node.left is not self.NIL and node.left.min_vruntime < m:
            m = node.left.min_vruntime
        if node.right is not self.NIL and node.right.min_vruntime < m:
            m = node.right.min_vruntime
        node.min_vruntime = m

    def _refresh_up(self, node):
        while node is not self.NIL:
            self._refresh(node)
            node = node.parent

    # ---------------- rotations (augmentation-preserving) ----------------

    def _rotate_left(self, x):
        y = x.right
        x.right = y.left
        if y.left is not self.NIL:
            y.left.parent = x
        y.parent = x.parent
        if x.parent is self.NIL:
            self.root = y
        elif x is x.parent.left:
            x.parent.left = y
        else:
            x.parent.right = y
        y.left = x
        x.parent = y
        self._refresh(x)   # x moved down -- refresh it first
        self._refresh(y)   # y now depends on x's refreshed value
        self.total_rotations += 1

    def _rotate_right(self, x):
        y = x.left
        x.left = y.right
        if y.right is not self.NIL:
            y.right.parent = x
        y.parent = x.parent
        if x.parent is self.NIL:
            self.root = y
        elif x is x.parent.right:
            x.parent.right = y
        else:
            x.parent.left = y
        y.right = x
        x.parent = y
        self._refresh(x)
        self._refresh(y)
        self.total_rotations += 1

    # ---------------- insert ----------------

    def insert(self, task):
        node = _Node(task)
        node.left = node.right = node.parent = self.NIL

        y = self.NIL
        x = self.root
        while x is not self.NIL:
            y = x
            x = x.left if task.sched_deadline < x.task.sched_deadline else x.right
        node.parent = y
        if y is self.NIL:
            self.root = node
        elif task.sched_deadline < y.task.sched_deadline:
            y.left = node
        else:
            y.right = node

        self._count += 1
        self._nodes[id(task)] = node
        self.avg_vruntime_sum += task.vruntime * task.weight
        self.avg_load += task.weight

        if self.leftmost is self.NIL or task.sched_deadline < self.leftmost.task.sched_deadline:
            self.leftmost = node

        self._refresh_up(node)
        self._insert_fixup(node)
        return node

    def _insert_fixup(self, z):
        while z.parent.color == RED:
            if z.parent is z.parent.parent.left:
                y = z.parent.parent.right
                if y.color == RED:
                    z.parent.color = BLACK
                    y.color = BLACK
                    z.parent.parent.color = RED
                    z = z.parent.parent
                else:
                    if z is z.parent.right:
                        z = z.parent
                        self._rotate_left(z)
                    z.parent.color = BLACK
                    z.parent.parent.color = RED
                    self._rotate_right(z.parent.parent)
            else:
                y = z.parent.parent.left
                if y.color == RED:
                    z.parent.color = BLACK
                    y.color = BLACK
                    z.parent.parent.color = RED
                    z = z.parent.parent
                else:
                    if z is z.parent.left:
                        z = z.parent
                        self._rotate_right(z)
                    z.parent.color = BLACK
                    z.parent.parent.color = RED
                    self._rotate_left(z.parent.parent)
        self.root.color = BLACK

    # ---------------- remove ----------------

    def _transplant(self, u, v):
        if u.parent is self.NIL:
            self.root = v
        elif u is u.parent.left:
            u.parent.left = v
        else:
            u.parent.right = v
        v.parent = u.parent

    def _minimum(self, node):
        while node.left is not self.NIL:
            node = node.left
        return node

    def remove(self, task):
        node = self._nodes.pop(id(task), None)
        if node is None:
            raise KeyError("task not in tree")

        self._count -= 1
        self.avg_vruntime_sum -= task.vruntime * task.weight
        self.avg_load -= task.weight

        y = node
        y_original_color = y.color
        if node.left is self.NIL:
            x = node.right
            fixup_parent = node.parent
            self._transplant(node, node.right)
        elif node.right is self.NIL:
            x = node.left
            fixup_parent = node.parent
            self._transplant(node, node.left)
        else:
            y = self._minimum(node.right)
            y_original_color = y.color
            x = y.right
            if y.parent is node:
                x.parent = y
                fixup_parent = y
            else:
                fixup_parent = y.parent
                self._transplant(y, y.right)
                y.right = node.right
                y.right.parent = y
            self._transplant(node, y)
            y.left = node.left
            y.left.parent = y
            y.color = node.color

        self._refresh_up(fixup_parent)

        if self.leftmost is node:
            self.leftmost = self._minimum(self.root) if self.root is not self.NIL else self.NIL

        if y_original_color == BLACK:
            self._delete_fixup(x)
        self.NIL.parent = self.NIL
        self.NIL.left = self.NIL.right = self.NIL

    def _delete_fixup(self, x):
        while x is not self.root and x.color == BLACK:
            if x is x.parent.left:
                w = x.parent.right
                if w.color == RED:
                    w.color = BLACK
                    x.parent.color = RED
                    self._rotate_left(x.parent)
                    w = x.parent.right
                if w.left.color == BLACK and w.right.color == BLACK:
                    w.color = RED
                    x = x.parent
                else:
                    if w.right.color == BLACK:
                        w.left.color = BLACK
                        w.color = RED
                        self._rotate_right(w)
                        w = x.parent.right
                    w.color = x.parent.color
                    x.parent.color = BLACK
                    w.right.color = BLACK
                    self._rotate_left(x.parent)
                    x = self.root
            else:
                w = x.parent.left
                if w.color == RED:
                    w.color = BLACK
                    x.parent.color = RED
                    self._rotate_right(x.parent)
                    w = x.parent.left
                if w.right.color == BLACK and w.left.color == BLACK:
                    w.color = RED
                    x = x.parent
                else:
                    if w.left.color == BLACK:
                        w.right.color = BLACK
                        w.color = RED
                        self._rotate_left(w)
                        w = x.parent.left
                    w.color = x.parent.color
                    x.parent.color = BLACK
                    w.left.color = BLACK
                    self._rotate_right(x.parent)
                    x = self.root
        x.color = BLACK

    # ---------------- eligibility / V ----------------

    def avg_vruntime(self, curr=None):
        """
        V = weighted average vruntime across the tree, plus `curr` if
        it's running but not in the tree -- real Linux: avg_vruntime(),
        curr folded in because it's still part of the fair-share
        population even though it isn't a tree node right now.
        """
        total_w = self.avg_load
        total_sum = self.avg_vruntime_sum
        if curr is not None:
            total_w += curr.weight
            total_sum += curr.vruntime * curr.weight
        if total_w == 0:
            return 0.0
        return total_sum / total_w

    @staticmethod
    def _eligible(vruntime, v):
        return vruntime <= v

    # ---------------- pick_eevdf ----------------

    def pick_eevdf(self, curr=None):
        """
        Faithful port of kernel/sched/fair.c's pick_eevdf(): cached-
        leftmost fast path, then a pruned walk using each node's
        min_vruntime to skip subtrees that can't contain an eligible
        (vruntime <= V) task -- O(depth), not O(n).

        `curr` is accepted for structural completeness (real Linux
        compares the walk's result against the currently-running,
        not-in-tree entity at the end, since curr might still deserve
        to keep running). This simulator's Core.run() only ever calls
        this with curr=None, since it only asks "what's next" once a
        core has already gone idle -- unlike real Linux, which
        re-evaluates this every tick even while curr keeps running, via
        a dynamic per-task slice. Our fixed TIME_SLICE quantum (see
        Core.py's own documented simplification) never needs that
        branch, so it's implemented faithfully but never exercised.
        """
        self.total_picks += 1
        nodes_visited = 0
        depth = 0

        if self.root is self.NIL:
            self.last_pick_nodes_visited = 0
            self.last_pick_depth = 0
            return curr

        v = self.avg_vruntime(curr)

        # Cached-leftmost fast path -- O(1), the common case in real Linux.
        nodes_visited += 1
        leftmost_task = self.leftmost.task
        if self._eligible(leftmost_task.vruntime, v):
            picked = leftmost_task
        else:
            # Pruned walk from the root -- O(depth), not O(n).
            node = self.root
            best = None
            while node is not self.NIL:
                depth += 1
                nodes_visited += 1
                left = node.left
                if left is not self.NIL and self._eligible(left.min_vruntime, v):
                    node = left
                    continue
                if self._eligible(node.task.vruntime, v):
                    best = node
                    break
                node = node.right
            # best should always be found -- the average can never be
            # below its own minimum, so the leftmost-by-vruntime task in
            # any non-empty tree is always eligible. This fallback
            # exists only as a defensive net against floating-point
            # edge cases, same spirit as the pre-tree pick_next()'s own
            # "shouldn't normally happen" comment.
            picked = best.task if best is not None else leftmost_task

        self.last_pick_nodes_visited = nodes_visited
        self.last_pick_depth = depth
        self.total_nodes_visited += nodes_visited

        if curr is not None and curr.sched_deadline < picked.sched_deadline:
            return curr
        return picked

    # ---------------- debug/self-check (not used by the simulator) ----------------

    def _black_height_ok(self):
        """Verify the black-height invariant holds everywhere -- test-only."""
        def check(node):
            if node is self.NIL:
                return 1
            left_bh = check(node.left)
            right_bh = check(node.right)
            if left_bh != right_bh or left_bh is None:
                return None
            if node.color == RED and (node.left.color == RED or node.right.color == RED):
                return None
            return left_bh + (1 if node.color == BLACK else 0)
        return check(self.root) is not None

    def _augmentation_ok(self):
        """Verify every node's min_vruntime matches its true subtree min -- test-only."""
        def check(node):
            if node is self.NIL:
                return True
            expected = node.task.vruntime
            if node.left is not self.NIL:
                expected = min(expected, node.left.min_vruntime)
            if node.right is not self.NIL:
                expected = min(expected, node.right.min_vruntime)
            if node.min_vruntime != expected:
                return False
            return check(node.left) and check(node.right)
        return check(self.root)


class TrackedRunQueue(list):
    """
    A plain list that also mirrors every append/remove into an
    EevdfTree -- matching real Linux's own dual-structure design:
    cfs_rq->tasks_timeline (the rbtree, used by pick_eevdf()) and
    cfs_rq->tasks (a plain list_head, used by migration/iteration code
    that has no use for tree ordering).

    Subclassing list means every existing caller elsewhere in this
    codebase (LoadBalancer.py's `src_core.rq[0]`, `src.rq[-1]`,
    `[t for t in core.rq if ...]`, `max(candidates, key=...)`,
    BurstDetector.py's `core.running_count()` via `len(self.rq)`, etc.)
    keeps working completely unchanged -- they were already the right
    shape, matching real Linux's own plain-list half of this picture.
    Only Core.pick_next() reaches into `.tree` for the part that
    actually needs tree structure.
    """

    def __init__(self):
        super().__init__()
        self.tree = EevdfTree()

    def append(self, task):
        super().append(task)
        self.tree.insert(task)

    def remove(self, task):
        super().remove(task)
        self.tree.remove(task)
