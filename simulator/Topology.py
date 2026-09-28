"""
Mirrors kernel/sched/topology.c's sched_domain hierarchy, matching the
Lozi et al. Figure 1 machine: 32 cores, 4 nodes of 8 cores each, SMT
pairs, and (per node) 3 nodes reachable in one hop with the 4th needing
two hops.

    pair    (SMT, SD_SHARE_CPUCAPACITY)      -> imbalance_pct = 110
    node    (LLC, SD_SHARE_LLC, 8 cores)     -> imbalance_pct = 117
    onehop  (3 nodes reachable in 1 hop)     -> imbalance_pct = 117, NUMA
    machine (all 4 nodes, 4th needs 2 hops)  -> imbalance_pct = 117, NUMA

Both onehop and machine are NUMA-flagged (SD_SERIALIZE, the two-gate
adjust_numa_imbalance logic) since both represent inter-node distance,
not just a bigger local group.

FIXED: the "Scheduling Group Construction" bug (Lozi et al., Section 3.2)
-------------------------------------------------------------------------
Real Linux builds this hierarchy separately FOR EACH CPU: the outer loop
in sched_init_domains() runs for_each_possible_cpu(cpu) and, for that
cpu, walks for_each_cpu_wrap(i, span, cpu) -- i.e. group construction is
anchored at the cpu actually being built for, not always CPU 0. Lozi et
al. document a real (now-fixed) kernel bug where groups were instead
built once from a single reference CPU's viewpoint and then reused for
everyone, silently giving asymmetric machines a wrong hierarchy for
every CPU except the reference one.

Our previous implementation had exactly that bug: ONE onehop domain was
built from node 0's neighbors and shared by the whole machine, so node
3's cores skipped the onehop tier entirely (they went straight from
their 8-core node to the 32-core machine domain, never seeing a
group of 3 nodes at all).

The fix, matching the kernel's real remedy (build_overlap_sched_groups()
+ find_descended_sibling()):

  1. Node adjacency is a RING (matching Lozi et al.'s own Figure 4:
     node0-node1-node2-node3-node0), so "one hop away" is genuinely
     relative to each node: node i's one-hop neighbors are the
     `nodes_in_one_hop - 1` nearest nodes going around the ring in both
     directions, not a fixed set borrowed from node 0.

  2. EVERY node gets its OWN onehop domain, anchored on itself
     (onehop_i spans {node i} + node i's own ring neighbors), and its
     OWN machine domain on top (spanning onehop_i + whatever node is
     still more than one hop away). This is the direct analog of
     for_each_cpu_wrap(i, span, cpu) always anchoring construction at
     the cpu doing the walk.

  3. Because the ring's neighbor sets overlap (node 1 is a neighbor of
     both node 0 and node 2), a node's cores show up in the numeric
     SPAN of more than one onehop domain -- exactly like real Linux's
     overlapping sched_groups. We keep that distinction explicit:
     `Domain.children` is the SPAN (everyone counted in this group's
     aggregate load, for classify()/imbalance comparisons), while a
     separate `home_children` subset says who this domain is the
     genuine PARENT of for climbing (`domain_chain()`/`core.parent`).
     Each node/onehop domain is `home_children` of exactly one parent
     -- the one anchored on it -- so `c.parent` is only ever set once,
     never overwritten by a domain that merely counts it in a wider
     span.

  4. The "still farther than one hop" group at the machine level is
     built the same way topology.c's find_descended_sibling() does it:
     we don't invent a lone singleton group for the leftover node, we
     REUSE that node's own onehop domain (its own child-level group,
     however big it already is). For our default ring this means node
     0's machine domain has groups {onehop0 = node0,1,3} and
     {onehop2 = node1,2,3} -- deliberately overlapping on nodes 1 and 3,
     exactly like the node-0 column of the ring example in topology.c's
     own NUMA comment block ("NUMA-2 groups: {0-1,3},{1-3}").

  5. Because of that deliberate overlap, `Domain.cores()` de-duplicates
     by core id: a domain's own weight/min_interval/max_interval must
     reflect its true union span (sd->span is a cpumask, never a
     multiset) even though the two GROUPS inside it legitimately double
     count a couple of nodes between them -- same as real Linux, where
     sd_weight is the domain's cpumask weight, never the sum of its
     groups' individual (possibly-overlapping) weights.

  6. Real Linux needs group_balance_cpu()/build_balance_mask() because
     multiple physical CPUs concurrently, independently compute stats
     for the same shared/overlapping sched_group (like onehop2, reused
     inside both machine0 and machine2) and something must elect one
     canonical owner to avoid racing or duplicated updates. We had a
     `group_balance_owner()` implementing that exact election, but
     removed it (2026-09-14): this simulator is single-threaded and
     `classify_group()` is a pure function of `.cores()`/`.load()`/
     `.is_idle()` -- calling it once from machine0's descent and once
     from machine2's descent, both touching onehop2's shared span,
     always returns the identical, correct result. There's no
     concurrency to arbitrate, so there was no correctness gap an
     owner-election would have closed -- it was a faithful structural
     analog with no actual call site or need.
"""


class Domain:
    LEVEL_PAIR = "pair"
    LEVEL_NODE = "node"
    LEVEL_ONEHOP = "onehop"
    LEVEL_MACHINE = "machine"

    IMBALANCE_PCT = {
        LEVEL_PAIR: 110,
        LEVEL_NODE: 117,
        LEVEL_ONEHOP: 117,
        LEVEL_MACHINE: 117,
    }

    # verified: SD_SERIALIZE only on SD_NUMA domains. Both onehop and
    # machine represent inter-node distance here, so both carry it.
    NUMA_LEVELS = {LEVEL_ONEHOP, LEVEL_MACHINE}

    def __init__(self, level, name, children, home_children=None):
        """
        `children` is this domain's SPAN: every Domain/Core counted in
        its aggregate load for classify()/_balance_domain() purposes.

        `home_children` is the subset of `children` that this domain is
        the genuine climbing PARENT of (defaults to all of `children`,
        which is correct for pair/node levels -- those never overlap,
        every pair belongs to exactly one node and vice versa). For the
        overlapping ring-based onehop/machine levels, callers pass a
        strict subset (just the anchor) so a neighbor merely counted in
        this domain's span doesn't have its `.parent` hijacked -- it
        keeps the parent already set by ITS OWN anchored domain.
        """
        self.level = level
        self.name = name
        self.children = children       # list[Domain] or list[Core] -- the SPAN
        self.parent = None

        home = children if home_children is None else home_children
        for c in home:
            assert c in children, "home_children must be a subset of children"
            c.parent = self

        weight = len(self.cores())
        # mirrors sd_init(): min_interval = sd_weight, max_interval = 2*sd_weight
        self.min_interval = max(weight, 1)
        self.max_interval = 2 * self.min_interval
        # ADDED 2026-09-24: verified sd_init()/busy_factor backoff analog --
        # max_interval used to be computed and never read (see
        # LoadBalancer.periodic_balance()); this is the mutable interval
        # actually checked against, starting at min_interval and adapting
        # based on whether balancing keeps finding anything to do.
        self.balance_interval = self.min_interval
        self.last_balance = 0
        self.imbalance_pct = self.IMBALANCE_PCT[level]
        self.is_numa = level in self.NUMA_LEVELS
        # FIX C (2026-09-27, see Readme.md): verified against sd_init()/
        # default_topology[] (kernel/sched/topology.c, v7.2, lines
        # ~2032-2103): SMT (this sim's "pair") gets SD_SHARE_CPUCAPACITY
        # | SD_SHARE_LLC; MC/core ("node" here, one LLC/socket) gets
        # SD_SHARE_LLC only; PKG and NUMA-distance levels beyond it
        # ("onehop"/"machine" here) get neither -- confirmed, matches
        # the mapping given for this fix rather than guessed at.
        self.share_llc = level in (self.LEVEL_PAIR, self.LEVEL_NODE)

    def cores(self):
        """
        Flat list of leaf Core objects under this domain, de-duplicated
        by core_id. Dedup matters once a domain's groups legitimately
        overlap (the reused far-onehop group at the machine level, see
        module docstring point 4) -- sd->span in real Linux is a cpumask
        (a set), so a domain's own span is never a multiset even though
        two of its groups can double-count a couple of member nodes
        between them. For every non-overlapping domain (pair, node, a
        single onehop) this is a plain flatten with no duplicates to
        remove, so behavior there is unchanged.
        """
        out = []
        seen_ids = set()
        for c in self.children:
            members = c.cores() if isinstance(c, Domain) else [c]
            for m in members:
                if m.core_id not in seen_ids:
                    seen_ids.add(m.core_id)
                    out.append(m)
        return out

    def groups(self):
        """The sched_group entities balanced at this level = direct children (span)."""
        return self.children


GROUP_HAS_SPARE = 0
GROUP_FULLY_BUSY = 1
GROUP_OVERLOADED = 2

CAPACITY_SCALE = 1024


class WorkStats:
    """
    Instrumentation, not time -- same honest limitation as EevdfTree.py's
    node-visit counters (see that file's module docstring for the full
    reasoning): these count real, language-independent units of
    scheduler work at every site that, like pick_next()/try_newidle(),
    runs with ZERO simulated time charged for it (see the comment at
    Core.run()'s pick_next() call site). What's counted here is
    everything OUTSIDE that one tree -- periodic balancing's full-
    machine sweep every tick, newly-idle's domain-chain classify()
    scans, new-task placement's hierarchy descent, and burst-triggered
    balancing's own domain walk. None of it converts to CPU cycles for
    the same reason the tree's counters don't: cycles depend on cache
    behavior, branch prediction, and compiled code on real silicon, not
    on the shape of this algorithm or how long this Python takes to
    run it.

    Module-level singleton (STATS below), RESET at the start of every
    Main.run_simulation() call -- Experiment.py runs many simulations
    sequentially in one process, so this must not accumulate across
    runs the way a per-object counter naturally would have.
    """

    def __init__(self):
        self.classify_calls = 0
        self.classify_cores_scanned = 0
        self.checker_calls = 0
        self.checker_cores_scanned = 0
        self.periodic_calls = 0
        self.periodic_levels_walked = 0
        self.newidle_calls = 0
        self.newidle_levels_walked = 0
        # NOTE (found while sanity-checking a flat "800" across every row
        # of every experiment config, 2026-09-26): placement_calls increments
        # once per NEW task (every task is born with prev_core=None, so
        # every one takes hierarchical_new_task_placement()'s fork path
        # exactly once -- there is no re-placement path for already-run
        # tasks). placement_levels_walked is the SUM, across all those
        # calls in one run, of Domain-tree levels iterated -- and that
        # per-call depth is fixed by build_topology()'s static params, not
        # by load or scheduler choice. So this total is the deterministic
        # product placement_calls * topology_depth (200 * 4 = 800 with
        # this repo's defaults) for EVERY profile/intensity/scheduler --
        # unlike periodic_levels_walked/newidle_levels_walked below, it
        # carries no information about runtime behavior at all unless
        # n_tasks or the topology's depth is varied between runs, which
        # nothing in Experiment.py currently does. See Metrics.summary()'s
        # placement_levels_per_call for the derived, self-documenting form.
        self.placement_calls = 0
        self.placement_levels_walked = 0
        self.burst_check_calls = 0
        self.burst_balance_levels_walked = 0

    def reset(self):
        self.__init__()


STATS = WorkStats()


def classify_group(group):
    """
    Shared group_classify()-lite: type, load, capacity, idle core count,
    running count. Used by both placement.py (initial task landing) and
    load_balancer.py (periodic/newidle/burst balancing) so the two stay
    consistent about what counts as has-spare/fully-busy/overloaded --
    previously duplicated independently in each file. Also the single
    chokepoint all four of those callers scan cores through, which is
    why instrumenting it here (rather than separately in each caller)
    covers placement + periodic + newidle + burst balancing at once.
    """
    cores = group.cores() if hasattr(group, "cores") else [group]
    STATS.classify_calls += 1
    STATS.classify_cores_scanned += len(cores)

    capacity = len(cores) * CAPACITY_SCALE
    load = sum(c.load() for c in cores)
    idle = sum(1 for c in cores if c.is_idle())
    running = sum(c.running_count() for c in cores)

    if idle > 0 and running <= len(cores):
        gtype = GROUP_HAS_SPARE
    elif load * CAPACITY_SCALE / capacity > 100 and running > len(cores):
        gtype = GROUP_OVERLOADED
    else:
        gtype = GROUP_FULLY_BUSY

    return gtype, load, capacity, idle, running


def domain_chain(core):
    """Bottom-up list of domains this core belongs to (pair -> node -> onehop -> machine).

    Climbs `core.parent` -> `.parent` -> ...; since each domain only sets
    `.parent` on its `home_children`, this always follows the ONE chain
    anchored on `core`'s own node, never a neighbor's.
    """
    chain = []
    d = core.parent
    while d is not None:
        chain.append(d)
        d = d.parent
    return chain


def ring_neighbors(node_id, num_nodes, count):
    """
    The `count` nearest OTHER nodes to `node_id` around a ring of
    `num_nodes` (matching Lozi et al. Figure 4's node0-node1-node2-node3-
    node0 diagram), stepping outward alternately on each side.

    For the default 4-node ring with count=2 this gives exactly:
      node0 -> {node1, node3}, node1 -> {node0, node2},
      node2 -> {node1, node3}, node3 -> {node0, node2}
    i.e. genuinely relative to each node, not borrowed from node 0.
    """
    neighbors = []
    step = 1
    while len(neighbors) < count and step <= num_nodes // 2:
        right = (node_id + step) % num_nodes
        left = (node_id - step) % num_nodes
        if right != node_id and right not in neighbors:
            neighbors.append(right)
        if len(neighbors) < count and left != node_id and left not in neighbors:
            neighbors.append(left)
        step += 1
    return neighbors[:count]


def build_node(node_id, start_core_id, cores_per_pair=2, pairs_per_node=4, load_model="legacy"):
    """One 8-core node: 4 SMT pairs, matching Figure 1's 'eight cores per node'."""
    from Core import Core

    n_cores = cores_per_pair * pairs_per_node
    cores = [Core(core_id=start_core_id + i, load_model=load_model) for i in range(n_cores)]

    pairs = []
    for i in range(0, n_cores, cores_per_pair):
        pair_cores = cores[i:i + cores_per_pair]
        pairs.append(Domain(Domain.LEVEL_PAIR, f"n{node_id}-pair{i // cores_per_pair}", pair_cores))

    # pair/node never overlap across nodes, so home_children defaults to
    # "all children" here -- no anchoring subtlety needed at this level.
    node = Domain(Domain.LEVEL_NODE, f"node{node_id}", pairs)
    return node, cores


def build_topology(num_cores=32, cores_per_pair=2, pairs_per_node=4,
                    nodes_in_one_hop=3, load_model="legacy"):
    """
    Builds the Figure-1 shaped machine, now built PER NODE (the fix for
    the Scheduling Group Construction bug -- see module docstring):
    every node gets its own onehop domain, anchored on itself, from a
    ring adjacency; every node gets its own machine domain on top,
    spanning its own onehop plus whatever node is still farther than one
    hop away.

    Defaults (32, 2, 4, 3) reproduce Figure 1's SIZES exactly: 4 nodes of
    8 cores, 3 reachable in one hop (self + 2 ring neighbors), the 4th
    needing two hops -- but now that "3 reachable in one hop" set is
    different for every node, as it should be.

    Returns (machine, all_cores) -- same signature as before. `machine`
    is node 0's own machine domain, used only as the descent root for
    Placement.hierarchical_new_task_placement() (which itself always
    enters from cores[0]'s perspective -- a separate, already-documented
    simplification in placement.py, unrelated to this bug). Every core's
    OWN balancing climb (`domain_chain()`/`core.parent`) goes through its
    OWN node's onehop/machine domains regardless of which object this
    function returns, so periodic/newidle/burst balancing for nodes 1-3
    is unaffected by this choice.
    """
    cores_per_node = cores_per_pair * pairs_per_node
    num_nodes = num_cores // cores_per_node
    if num_nodes < 2:
        raise ValueError("need at least 2 nodes to model onehop/machine levels")

    nodes = []
    all_cores = []
    next_id = 0
    for n in range(num_nodes):
        node, node_cores = build_node(n, next_id, cores_per_pair, pairs_per_node, load_model=load_model)
        nodes.append(node)
        all_cores.extend(node_cores)
        next_id += cores_per_node

    # "3 nodes reachable in one hop" = self + 2 ring neighbors -> degree 2.
    neighbor_count = max(min(nodes_in_one_hop, num_nodes) - 1, 0)

    # Pass 1: one onehop domain PER NODE, anchored on that node alone.
    # home_children=[nodes[i]] means a ring NEIGHBOR merely counted in
    # this onehop's span never has its `.parent` overwritten here --
    # only the anchor node does. Every node is exactly one onehop's
    # anchor, so every node's `.parent` ends up set exactly once,
    # regardless of build order.
    onehops = []
    for i in range(num_nodes):
        neighbor_idx = ring_neighbors(i, num_nodes, neighbor_count)
        span_nodes = [nodes[i]] + [nodes[j] for j in neighbor_idx]
        onehop = Domain(Domain.LEVEL_ONEHOP, f"onehop{i}", span_nodes,
                         home_children=[nodes[i]])
        onehops.append(onehop)

    # Pass 2: one machine domain PER NODE, spanning that node's own
    # onehop plus a group for whichever node(s) are still farther than
    # one hop. Matching find_descended_sibling(): the covering group for
    # a leftover node is NOT a fresh singleton -- it's that node's own
    # onehop domain, REUSED whole, exactly like the real ring example's
    # "NUMA-2 groups: {0-1,3},{1-3}" (the second group there is node 2's
    # own NUMA-1 span, not node 2 alone). We only add as many reused
    # groups as needed to cover every remaining node (real Linux's "only
    # build enough groups to cover the domain"), so a far node already
    # swept in by an earlier reused group's span isn't given its own.
    #
    # home_children=[onehops[i]] means neither the far node NOR its
    # reused onehop domain ever has `.parent` overwritten here -- they
    # keep the parent set by their own home construction (iteration j).
    machines = []
    for i in range(num_nodes):
        neighbor_idx = set(ring_neighbors(i, num_nodes, neighbor_count))
        far_idx = [j for j in range(num_nodes) if j != i and j not in neighbor_idx]

        covered = set(c.core_id for c in onehops[i].cores())
        far_groups = []
        for j in far_idx:
            j_span = set(c.core_id for c in onehops[j].cores())
            if j_span <= covered:
                continue  # already swept in by a previously-added group
            far_groups.append(onehops[j])
            covered |= j_span

        if far_groups:
            machine = Domain(Domain.LEVEL_MACHINE, f"machine{i}",
                              [onehops[i]] + far_groups,
                              home_children=[onehops[i]])
        else:
            # onehop already spans every other node -- degenerate,
            # machine == onehop, same as sd_degenerate() would collapse.
            machine = onehops[i]
        machines.append(machine)

    return machines[0], all_cores
