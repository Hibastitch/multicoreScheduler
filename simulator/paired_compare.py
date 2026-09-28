"""
Shared paired-comparison harness for baseline vs burst-aware experiments.

Added after Readme.md's "2026-09-26b" correction, which found that mean-only
comparisons (no paired test) had produced a false "robust advantage" claim.
Every new experiment in this file's callers uses the same seed scheme as
Experiment.py (1000*profile_id + 100*intensity_id + rep) so results are
comparable across scripts, and reports a paired sign test + 95% CI on the
mean difference -- never just a mean.

This module does not change any scheduling/detector logic. It is read-only
instrumentation around run_simulation().
"""

import math
import statistics

from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
from WorkloadGenerator import PROFILE_NAMES

INTENSITY_LEVELS = ["low", "medium", "high"]

# TIE TOLERANCE FIX (2026-09-27, see Readme.md): found via bursty
# size=24/32 showing mean_diff=+0.0% (CI +-0.0%) yet sign_p<0.0001 --
# every sign/Wilcoxon test in this module (and several task scripts)
# compared diffs to EXACT 0, but per-seed metric values are computed
# from arrival times that are multiples of 1/1.5ms etc., which aren't
# exactly representable in floating point -- two runs that are
# CONCEPTUALLY identical for a given seed can differ by ~1e-13, never
# landing on an exact tie even when nothing meaningfully differs. Any
# |diff| below this treated as a tie everywhere in this module.
# Equivalent to rounding each raw value to ~1e-6 before diffing.
TIE_TOLERANCE = 1e-6

# Two-tailed t critical values at alpha=0.05, indexed by degrees of freedom
# (n-1). Avoids a scipy dependency (not installed in this env) for the CI
# on paired mean differences used throughout these experiments (n=30 fixed
# per the task's ground rules, so df=29 covers every call site here).
T_CRIT_95 = {29: 2.045, 27: 2.052, 23: 2.069, 19: 2.093, 9: 2.262}


def seed_base(profile, intensity):
    return 1000 * PROFILE_NAMES.index(profile) + 100 * INTENSITY_LEVELS.index(intensity)




def sign_test_p(wins, n):
    """Exact two-sided binomial sign test against p=0.5. Ties excluded from n."""
    if n == 0:
        return 1.0
    k = max(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def wilcoxon_signed_rank(diffs):
    """
    Two-sided Wilcoxon signed-rank test, normal approximation with the
    standard tie-in-|diff| variance correction, continuity-corrected. No
    scipy in this env. Zero differences are dropped before ranking (the
    conventional treatment) -- their count is reported separately since a
    large drop count (many exact ties between schedulers) is itself a
    finding, not just a nuisance to discard silently.

    Caveat: the normal approximation is a rough guide once n (after
    dropping zeros) gets much below ~15-20 -- fine for a sanity check
    here, not a substitute for an exact-distribution table at small n.
    """
    d = [x for x in diffs if abs(x) > TIE_TOLERANCE]  # tie tolerance fix, see module docstring constant
    n = len(d)
    n_zeros = len(diffs) - n
    if n == 0:
        return {"n": 0, "n_zeros_dropped": n_zeros, "W_pos": 0.0, "W_neg": 0.0, "z": 0.0, "p": 1.0}

    abs_d = [abs(x) for x in d]
    order = sorted(range(n), key=lambda i: abs_d[i])
    ranks = [0.0] * n
    tie_group_sizes = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs_d[order[j + 1]] == abs_d[order[i]]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        tie_group_sizes.append(j - i + 1)
        i = j + 1

    w_pos = sum(r for x, r in zip(d, ranks) if x > 0)
    w_neg = sum(r for x, r in zip(d, ranks) if x < 0)
    mean_w = n * (n + 1) / 4.0
    tie_correction = sum(t ** 3 - t for t in tie_group_sizes) / 48.0
    var_w = n * (n + 1) * (2 * n + 1) / 24.0 - tie_correction

    dev = w_pos - mean_w
    if var_w <= 0:
        z = 0.0
    else:
        cc = 0.5 if dev > 0 else (-0.5 if dev < 0 else 0.0)
        z = (dev - cc) / math.sqrt(var_w)
    p = min(1.0, 2 * (1.0 - _norm_cdf(abs(z))))

    return {"n": n, "n_zeros_dropped": n_zeros, "W_pos": w_pos, "W_neg": w_neg, "z": z, "p": p}


def format_p(p):
    return "<0.0001" if p < 0.0001 else f"{p:.4f}"


def paired_ci95(diffs):
    """Mean paired difference + 95% CI (normal approx to t if df not tabulated)."""
    n = len(diffs)
    mean = statistics.mean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    if sd == 0 or n < 2:
        return mean, (mean, mean)
    t_crit = T_CRIT_95.get(n - 1, 2.045)  # 2.045 (df=29) as fallback -- close enough for n>=20
    half = t_crit * sd / math.sqrt(n)
    return mean, (mean - half, mean + half)


def _plan_signature(plan):
    """Comparable snapshot of a generated workload: entry_core/direct_core
    are stored as actual Core objects (needed at placement time), but two
    runs build SEPARATE topologies, so the objects are never `==` even
    for a matching workload -- compare by core_id instead. direct_core
    included (Task 5, stacked_burst) since it's the field placement
    actually uses when set -- entry_core alone wouldn't catch a
    divergence there."""
    return [
        (e["task_id"], e["arrival_time"], e["cpu_time"], e["weight"],
         e["entry_core"].core_id if e["entry_core"] is not None else None,
         e.get("direct_core").core_id if e.get("direct_core") is not None else None,
         e["deadline"])
        for e in plan
    ]


def assert_same_workload(plan_a, plan_b, label_a="baseline", label_b="burst_aware"):
    """Fix 3d (see Readme.md): a same-seed paired comparison is only
    meaningful if both runs saw the IDENTICAL task stream. Investigation
    found this silently broken under the legacy newidle gate, sharing
    Python's global `random` module with the workload generator. Both
    sides are now RNG-isolated (WorkloadGenerator.py, LoadBalancer.py),
    but this assertion is the standing regression check for that
    invariant -- fails loudly, naming the first divergent task, rather
    than silently comparing two different workloads again."""
    sig_a, sig_b = _plan_signature(plan_a), _plan_signature(plan_b)
    if sig_a == sig_b:
        return
    for i, (a, b) in enumerate(zip(sig_a, sig_b)):
        if a != b:
            raise AssertionError(
                f"workload divergence between {label_a} and {label_b} at task index {i}: "
                f"{label_a}={a} vs {label_b}={b} -- RNG isolation broken, see Readme.md Fix 3d"
            )
    raise AssertionError(
        f"workload length mismatch: {label_a} has {len(sig_a)} tasks, "
        f"{label_b} has {len(sig_b)} -- RNG isolation broken, see Readme.md Fix 3d"
    )


def run_pair(profile, intensity, seed, balancer_kwargs=None, workload_kwargs=None,
             extra_processes=None, extra_processes_factory=None):
    """Run baseline then burst_aware at one seed, return per-scheduler metric dict.
    Asserts both runs saw the identical workload (Fix 3d) before returning.

    `extra_processes`: optional list of `fn(env, cores, balancer) ->
    generator` callables registered as extra simpy processes on BOTH
    runs -- fine for STATELESS samplers, but a stateful one (a
    diagnostics.py sampler closing over a dict it mutates) would have
    both runs writing into the SAME dict if passed this way.

    `extra_processes_factory`: optional nullary callable `() ->
    (extra_processes_list, state_dict)`, called ONCE PER SIDE so baseline
    and burst_aware each get their OWN fresh sampler state instead of
    sharing one. `state_dict`'s keys are merged into that side's result
    row (e.g. {"idle_state": {...}, "imbalance_state": {...}})."""
    balancer_kwargs = dict(balancer_kwargs or {})
    workload_kwargs = workload_kwargs or {}
    out = {}
    for name, cls in [("baseline", LoadBalancer), ("burst_aware", BurstAwareLoadBalancer)]:
        side_extra_processes = extra_processes
        side_state = {}
        if extra_processes_factory is not None:
            side_extra_processes, side_state = extra_processes_factory()

        m, b, gt, migs, logger, plan = run_simulation(
            profile, cls, intensity_level=intensity, seed=seed,
            balancer_kwargs=dict(balancer_kwargs), extra_processes=side_extra_processes,
            **workload_kwargs,
        )
        s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
        out[name] = {
            "summary": s,
            "makespan": s["makespan"],
            "avg_wait": s["avg_wait"],
            "p95_wait": s["p95_wait"],
            "p99_wait": s["p99_wait"],
            "migrations": b.migrations,
            "balancer": b,
            "ground_truth_bursts": gt,
            "migration_events": migs,
            "metrics": m,
            "plan": plan,
            "logger": logger,
            **side_state,
        }
    assert_same_workload(out["baseline"]["plan"], out["burst_aware"]["plan"])
    return out


def summarize_metric(rows, metric_key, lower_is_better=True):
    """rows: list of {"baseline": {...}, "burst_aware": {...}} from run_pair.
    Returns a dict with means, stdevs, paired mean diff + CI, wins/ties, p-value."""
    base_vals = [r["baseline"][metric_key] for r in rows]
    burst_vals = [r["burst_aware"][metric_key] for r in rows]
    diffs = [b - a for a, b in zip(base_vals, burst_vals)]  # burst_aware - baseline

    n = len(rows)
    if lower_is_better:
        wins = sum(1 for d in diffs if d < -TIE_TOLERANCE)
        ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
    else:
        wins = sum(1 for d in diffs if d > TIE_TOLERANCE)
        ties = sum(1 for d in diffs if abs(d) <= TIE_TOLERANCE)
    n_eff = n - ties
    p_sign = sign_test_p(wins, n_eff)
    mean_d, ci = paired_ci95(diffs)

    return {
        "metric": metric_key,
        "n": n,
        "baseline_mean": statistics.mean(base_vals),
        "baseline_stdev": statistics.stdev(base_vals) if n > 1 else 0.0,
        "burst_aware_mean": statistics.mean(burst_vals),
        "burst_aware_stdev": statistics.stdev(burst_vals) if n > 1 else 0.0,
        "mean_diff": mean_d,
        "ci95": ci,
        "wins": wins,
        "ties": ties,
        "n_eff": n_eff,
        "p_sign": p_sign,
    }


def print_summary(label, summary):
    s = summary
    lo, hi = s["ci95"]
    print(f"{label} [{s['metric']}] n={s['n']}: "
          f"baseline={s['baseline_mean']:.3f}+-{s['baseline_stdev']:.3f}  "
          f"burst_aware={s['burst_aware_mean']:.3f}+-{s['burst_aware_stdev']:.3f}  "
          f"mean_diff={s['mean_diff']:+.3f} (95% CI [{lo:+.3f}, {hi:+.3f}])  "
          f"wins={s['wins']}/{s['n_eff']} (ties={s['ties']})  p={format_p(s['p_sign'])}")
