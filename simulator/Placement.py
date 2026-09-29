"""
Direct analog of what we traced in select_task_rq_fair() / wake_affine_idle()
/ wake_affine_weight() in fair.c.

Simplification: real Linux distinguishes `this_cpu` (the CPU handling the
wakeup) from `prev_cpu` (the task's last CPU). Our simulator has no
separate "waker" process, so `waker_core` is a stand-in for "the CPU
context placement is being evaluated from" -- for a brand-new task
(prev_core is None) we skip wake-affine entirely, matching the fork path.
"""

from Topology import domain_chain, Domain, CAPACITY_SCALE, classify_group, STATS

WA_BIAS = True  # mirrors CONFIG default: WA_BIAS scheduler feature on


def hierarchical_new_task_placement(machine, entry_core=None):
    """
    Analog of sched_balance_find_dst_group() + _group_cpu(): at each
    domain level, compare the LOCAL child (containing entry_core)
    against the best of the others. Stay local unless local is
    genuinely worse (by type) or the difference clears the domain's
    imbalance_pct tolerance -- matches the verified "bias to stay
    local" behavior, not a pure global least-loaded search.
    """
    node = machine
    STATS.placement_calls += 1
    while isinstance(node, Domain):
        STATS.placement_levels_walked += 1
        children = node.groups()
        if len(children) == 1:
            node = children[0]
            continue

        local = None
        if entry_core is not None:
            for c in children:
                members = c.cores() if hasattr(c, "cores") else [c]
                if entry_core in members:
                    local = c
                    break

        if local is None:
            node = min(children, key=lambda c: classify_group(c)[:2])
            continue

        others = [c for c in children if c is not local]
        idlest = min(others, key=lambda c: classify_group(c)[:2])

        local_type, local_load, local_cap, _, _ = classify_group(local)
        idlest_type, idlest_load, idlest_cap, _, _ = classify_group(idlest)

        if local_type < idlest_type:
            stay_local = True
        elif local_type > idlest_type:
            stay_local = False
        else:
            local_avg = local_load * CAPACITY_SCALE / local_cap if local_cap else 0
            idlest_avg = idlest_load * CAPACITY_SCALE / idlest_cap if idlest_cap else 0
            if idlest_avg >= local_avg:
                stay_local = True
            elif 100 * local_avg <= node.imbalance_pct * idlest_avg:
                stay_local = True
            else:
                stay_local = False

        node = local if stay_local else idlest
    return node


def wake_affine_idle(this_core, prev_core):
    if this_core.is_idle():
        return prev_core if prev_core.is_idle() else this_core
    if prev_core.is_idle():
        return prev_core
    return None  # no answer -> fall through to weight-based check


def wake_affine_weight(this_core, prev_core, task, imbalance_pct):
    this_eff = this_core.load() + task.weight
    if WA_BIAS:
        this_eff *= 100

    prev_eff = prev_core.load() - task.weight
    if WA_BIAS:
        prev_eff *= 100 + (imbalance_pct - 100) / 2

    return this_core if this_eff < prev_eff else None


def wake_affine(this_core, prev_core, task, imbalance_pct):
    target = wake_affine_idle(this_core, prev_core)
    if target is None:
        target = wake_affine_weight(this_core, prev_core, task, imbalance_pct)
    return target if target is not None else prev_core


def select_idle_sibling(candidate_core):
    """Search progressively wider domains (pair -> group -> machine) for an idle core."""
    for domain in domain_chain(candidate_core):
        for c in domain.cores():
            if c.is_idle():
                return c
    return candidate_core


def select_core_for_task(task, waker_core, cores_by_id, machine=None, placement_root="fixed"):
    if task.prev_core is None:
        # brand-new task (fork-like path, per the kernel comment: "usually
        # only true for WF_EXEC and WF_FORK") -- descend the hierarchy
        # with a local-vs-idlest bias, same pattern as
        # sched_balance_find_dst_group()/_group_cpu()
        if machine is not None:
            # TASK 8 FIX 3 (2026-09-29, docs/FIDELITY_AUDIT.md §11,
            # NOTEBOOK.md 2026-09-29b's STEP 1 retraction-of-a-retraction):
            # "fixed" (default) is the pre-existing, byte-identical
            # behavior -- always descend from the single `machine` object
            # every caller was handed (Topology.build_topology()'s
            # machines[0], the fixed root). "own" ignores that shared
            # object and descends from `waker_core`'s OWN top-level
            # domain instead (domain_chain(waker_core)[-1] -- the last
            # entry in a core's bottom-up domain chain is always its own
            # per-node machine domain, since Domain.home_children
            # correctly anchors every onehop/machine to its own node
            # regardless of which node's machine build_topology()
            # happened to return, verified in the STEP 1 correction
            # above). Fixes the node1/node3 asymmetry where
            # machines[0].children==[onehop0,onehop2] made those nodes'
            # forked tasks always resolve their top-level "local" to
            # onehop0, never their own onehop1/onehop3.
            root = machine
            if placement_root == "own":
                chain = domain_chain(waker_core)
                if chain:
                    root = chain[-1]
            return hierarchical_new_task_placement(root, entry_core=waker_core)
        idle = [c for c in cores_by_id.values() if c.is_idle()]
        if idle:
            return idle[0]
        return min(cores_by_id.values(), key=lambda c: c.load())

    prev = cores_by_id[task.prev_core]
    imbalance_pct = waker_core.parent.imbalance_pct if waker_core.parent else 117

    target = wake_affine(waker_core, prev, task, imbalance_pct)
    return select_idle_sibling(target)