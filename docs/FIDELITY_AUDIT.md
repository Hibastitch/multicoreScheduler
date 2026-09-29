# Fidelity Audit (Task 8 pre-audit, 2026-09-29)

Full Linux-fidelity audit of the simulator against kernel v7.2 source
(`raw.githubusercontent.com/torvalds/linux/v7.2/kernel/sched/{fair,core,
topology,pelt}.c`, `sched.h`), done BEFORE the Task 8 grid/confirmation
re-run this was requested to precede. **No simulator logic was changed
in this task** -- audit and measurement only, with ONE explicit,
authorized exception (2026-09-29 corrections pass, Step 4): a
TRACKING-ONLY `Task.last_ran_until` field was added to `Task.py`/
`Core.py` to measure §7's cache-hot scope. It is read by nothing but the
audit's own measurement script and was verified to change zero decisions
(byte-identical fingerprint hash before/after, see §7). Every citation below was
fetched fresh this session (cached at `/tmp/fair_v72.c`, `/tmp/core_v72.c`,
`/tmp/topology_v72.c`, `/tmp/pelt_v72.c`, `/tmp/sched_v72.h`); none is
from memory.

Column meanings:
- **status**: MATCH / DOCUMENTED SIMPLIFICATION (the code already says
  so) / UNDOCUMENTED MISMATCH (not previously flagged as a fidelity gap,
  or previously flagged as "unverified" and now confirmed wrong) / NOT
  MODELED.
- **bias**: does the gap make the BASELINE balance more, less, earlier,
  or later than real Linux would -- or is it neutral?
- **affects**: which path(s) the gap touches. Because `BurstScheduler.
  on_task_placed()` calls straight into `_find_checker()` and
  `_balance_domain()` (`BurstScheduler.py:105-106`) -- the SAME methods
  `periodic_balance()` and `try_newidle()` use -- almost every row below
  that says "periodic path" also says "burst path": see §13.

---

## 1. Periodic trigger: interval, busy_factor, doubling/reset

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `Main.py:83-90` `periodic_ticker()`: every core, every 1 sim-ms, calls `periodic_balance()`, which walks `d=core.parent` checking EACH domain's own `now - last_balance >= interval` (`LoadBalancer.py:275-326`) | `scheduler_tick()` checks `jiffies >= rq->next_balance` every tick, raising `SCHED_SOFTIRQ` only when due (`fair.c:14531`); the softirq handler, `sched_balance_domains()`, then walks `for_each_domain(cpu, sd)` checking EACH domain's own `time_after_eq(jiffies, sd->last_balance+interval)` (`fair.c:13798-13816`) -- the SAME per-domain gate our `periodic_balance()` implements | **CORRECTED to MATCH (timing), 2026-09-29** | neutral | periodic, burst |
| `LoadBalancer.periodic_balance` interval check: `now - last_balance >= interval` where `interval` is `Domain.balance_interval` / per-cpu state, no scaling | `get_sd_balance_interval(sd, cpu_busy)` (`fair.c:13565-13586`): `interval = sd->balance_interval; if (cpu_busy) interval *= sd->busy_factor;` (**busy_factor = 16**, `topology.c:1958`), then `msecs_to_jiffies`, `-1` if busy, `clamp(1, max_load_balance_interval)` | UNDOCUMENTED MISMATCH | earlier (checks ~16x too often whenever the checking core is busy) | periodic, burst |
| `Topology.py:139-141` `min_interval = max(weight,1)`, `max_interval = 2*min_interval` | `sd_init()`: `.min_interval = sd_weight, .max_interval = 2*sd_weight` (`topology.c:1956-1957`) | MATCH | neutral | periodic |
| `LoadBalancer.periodic_balance:321` `new_interval = d.min_interval if n else min(interval*2, d.max_interval)` | success: `sd->balance_interval = sd->min_interval` (`fair.c:13507`); failure: `sd->balance_interval *= 2` capped at `max_interval` (`fair.c:13556-13557`), but ONLY reached after `out_one_pinned`, and explicitly SKIPPED for `CPU_NEWLY_IDLE`/misfit (`fair.c:13538-13554`) | DOCUMENTED SIMPLIFICATION | neutral (shape matches; the NEWLY_IDLE/misfit skip and the finer pinned-vs-unpinned distinction aren't ported, but the sim's periodic path never calls this from a NEWLY_IDLE context anyway) | periodic |
| No `max_load_balance_interval` clamp modeled | `max_load_balance_interval = HZ * num_online_cpus() / 10` (`fair.c:13692`) -- an upper ceiling independent of any one domain's `max_interval` | NOT MODELED | neutral (this sim's per-domain `max_interval` values, 2..64 "ms", never approach a realistic `HZ*32/10` ceiling anyway) | periodic |

**Correction, 2026-09-29:** row 1 was originally marked UNDOCUMENTED
MISMATCH ("earlier/more often") on the theory that real Linux only
checks balancing when `rq->next_balance` is due, while this sim checks
every core every tick. Re-examined this session: `rq->next_balance` is
constructed as the MINIMUM, over every domain in the hierarchy, of that
domain's own `last_balance + get_sd_balance_interval(...)` due-time
(`fair.c:13775-13835`'s `next_balance`/`update_next_balance` bookkeeping,
recomputed on every `sched_balance_domains()` call; `sched_balance_
newidle()`'s own `update_next_balance()` calls, `fair.c:14390-14414`,
only ever pull it EARLIER, never later). So `jiffies >= rq->next_balance`
becomes true at EXACTLY the moment the soonest-due domain becomes due --
not later than a naive "check every domain every tick" scheme would find
it, and `sched_balance_domains()`, once it does run, walks every domain
in the chain checking each one's OWN interval (`fair.c:13798-13816`)
exactly like `periodic_balance()`'s `while d is not None` loop does. This
is a pure work-saving fast path (skip the O(levels) walk on ticks where
the cached minimum proves nothing can be due), not a timing difference:
no domain becomes due at a different jiffy/sim-ms under one scheme vs
the other. The only real difference is per-tick WORK: this sim's
`periodic_balance()` does an O(levels) walk on every core on every tick
(interval comparisons cost nothing to compute but still happen); real
Linux does an O(1) pointer comparison on most ticks and only pays the
O(levels) walk on the (rare) ticks something is actually due. Reclassified
MATCH (timing) accordingly. This does NOT affect row 2 below (the
missing `busy_factor` scaling) -- that is a separate, still-real mismatch
in the VALUE of the interval each domain computes, not in whether the
check happens every tick.

**Measured impact of the busy_factor gap (Toggle A, `development/fidelity_audit/task8_pre_audit_impact_measurements.py`, 10 seeds 50000-50009, baseline only, `checker_model="kernel"`):**

| workload | metric | default | +busy_factor | Δ | wins/harms/ties | sign_p | wilcoxon_p |
|---|---|---|---|---|---|---|---|
| stacked_high | p95_wait | 22.30 | 28.13 | **+26.2%** | 1/9/0 | 0.0215 | 0.0080 |
| stacked_high | migrations | 471.1 | 333.4 | -29.2% | 10/0/0 | 0.0020 | 0.0059 |
| bursty_high_s64 | p95_wait | 48.41 | 71.85 | **+48.4%** | 0/10/0 | 0.0020 | 0.0059 |
| bursty_high_s64 | migrations | 1629.5 | 513.1 | -68.5% | 10/0/0 | 0.0020 | 0.0059 |

This is the single largest-magnitude finding in this audit. The current
simulator's periodic path checks busy cores roughly 16x more often than
real Linux would, and that over-checking is currently buying a large
chunk of the baseline's periodic responsiveness (removing it costs 26-48%
p95_wait, at 29-69% fewer migrations). Directly relevant to "burst path
reacts earlier than periodic": with this bug, the simulator's PERIODIC
path is already artificially closer to burst-like reactivity than real
Linux's periodic path would be -- i.e. the current gap between
burst-aware and baseline is very likely an UNDER-estimate of the true
gap in real Linux, not an over-estimate. See the ranked list, §14.

---

## 2. NOHZ / idle load balancer (ILB)

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `periodic_ticker()` calls `periodic_balance()` for every core every tick regardless of idle state -- confirmed directly, `Main.py:87-88` has no idle/active filter | Idle CPUs stop ticking (`nohz_balance_enter_idle`, `fair.c:14095-14132`, sets `nohz.idle_cpus_mask`); a *different* mechanism, the nohz idle load balancer, periodically kicks ONE idle CPU (`nohz_balancer_kick`, `fair.c:13920-13993`) to run `_nohz_idle_balance()` (`fair.c:14158-14254`) on behalf of ALL idle CPUs in `nohz.idle_cpus_mask`, at a *shared* `nohz.next_balance` cadence, not per-CPU per-tick | UNDOCUMENTED MISMATCH | earlier/more often (idle cores in this sim keep participating in full-frequency periodic checks instead of going tickless and being serviced in a single batched ILB pass) | periodic |
| No `nohz.has_blocked_load`/blocked-average decay modeled | `nohz_balancer_kick()` also fires when `nohz.has_blocked_load` is set, independent of `next_balance` (`fair.c:13936-13941`) | NOT MODELED | neutral (this sim's `tick_load()` decays every core's load every tick regardless of idle state -- there's no "blocked," undecayed load concept here at all, so this Linux mechanism has no analog to be biased against) | periodic |

Not independently measured this session: unlike §1's busy_factor gap,
this isn't a single-constant or single-branch toggle -- correctly
modeling NOHZ/ILB would mean adding a wholly new "idle cores don't tick;
one gets kicked periodically to service the rest" mechanism, which is a
simulator-file change, not a monkeypatch. Marked **needs code change to
measure**.

---

## 3. `should_we_balance()` (checker election)

Covered by Task 7 (`checker_model="kernel"`, `LoadBalancer.py:231-268`),
already fully audited and fixed behind an opt-in flag (default stays
`"legacy"`). Confirmed complete this session by re-reading the current
code: `_find_checker_kernel()` implements the local-group + balance-mask
+ 3-tier idle preference exactly as elected against `should_we_balance()`
(`fair.c:13162-13221`, cited in the docstring). **MATCH** (opt-in,
`checker_model="kernel"`) / **UNDOCUMENTED MISMATCH, already fixed but
not yet default** (`checker_model="legacy"`, the current default).
Affects: periodic path only -- `BurstScheduler.on_task_placed()` calls
`self._find_checker(domain)` with no `from_core` argument
(`BurstScheduler.py:105`), which the "kernel" branch's own guard
(`LoadBalancer.py:262`, `from_core is None`) sends straight to the
legacy whole-span election regardless of `checker_model` -- **the burst
path is unaffected by Task 7's fix at all**, a specific instance of §13's
general point. This was flagged in Task 7 as a known follow-up, not
actioned here (no simulator changes in this task).

---

## 4. `sched_balance_find_src_group()`: classification and busiest selection

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `Topology.classify_group()` (`Topology.py:254-279`): 3 types -- `GROUP_HAS_SPARE` / `GROUP_FULLY_BUSY` / `GROUP_OVERLOADED` | `group_classify()` (`fair.c:11524-11550`): 8-way priority order -- `group_overloaded > group_llc_balance > group_imbalanced > group_asym_packing > group_smt_balance > group_misfit_task > group_fully_busy > group_has_spare` | DOCUMENTED SIMPLIFICATION | neutral for this topology (the 5 missing types need mechanisms this sim doesn't model at all: `SD_ASYM_CPUCAPACITY`/misfit needs heterogeneous cores, `group_imbalanced` needs `cpus_ptr` affinity constraints, `asym_packing`/`smt_balance` need `SD_ASYM_PACKING`/multi-thread-per-core SMT groups, `llc_balance` is `CONFIG_SCHED_CACHE`-gated and not the default -- this is a genuinely inapplicable-here simplification, not a hidden bias) | periodic, burst |
| `group_is_overloaded`/`group_has_capacity` (`Topology.classify_group`): flat `load*CAPACITY_SCALE/capacity > 100` threshold, using **load**, and a two-way (not three-way) split | Real: `group_is_overloaded` fires at `group_util/group_capacity > imbalance_pct/100` (**117%**, not 100%) OR a `group_runnable`-based check (`fair.c:11504-11521`); `group_has_capacity` is a SEPARATE, non-complementary predicate (`fair.c:11479-11493`) using **util**, not **load** | UNDOCUMENTED MISMATCH | earlier (100% threshold vs real 117%, i.e. sim calls a group "overloaded" sooner than Linux would) | periodic, burst |
| `_balance_domain`, `busiest = max(candidates, key=lambda g: stats[id(g)][1])` -- picks busiest by **load alone** among non-local groups (`LoadBalancer.py:339`) | `update_sd_pick_busiest()` (`fair.c:11918-11990`): picks by **type first** (`sgs->group_type > busiest->group_type`), and only within the SAME type does it compare a type-specific metric (`avg_load` for overloaded, etc.) | UNDOCUMENTED MISMATCH | mixed -- at domain levels with >2 groups (node/onehop/machine; pair only ever has exactly 2), a high-`load`-but-`has_spare` group can be picked over a lower-load-but-truly-`overloaded` group, which real Linux would never do | periodic, burst (levels with >2 groups only) |

Not independently measured: the busiest-selection mismatch is entangled
with the 3-type classification simplification above it (a faithful
type-first selector needs the fuller type enum to be meaningful), so a
clean monkeypatch that isolates just the ordering bug isn't possible
without also deciding how to rank the two extra sim-only... there are no
extra sim-only types, but re-ordering by only 3 types changes very
little for levels that rarely have >2 non-overloaded, non-local
candidate groups simultaneously. Marked **needs code change to measure**.

---

## 5. `calculate_imbalance()`: remaining branches and out_balanced checks

Fix C (`_balance_has_spare_kernel`, `LoadBalancer.py:395-451`) already
covers the `local->group_type == group_has_spare` branch
(`fair.c:12642-12703`) -- confirmed against source this session, MATCH
for that branch specifically. The rest, freshly read this session
(`fair.c:12572-12753`):

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| No `migrate_misfit`/`migrate_util`(overloaded-case)/`migrate_task`(asym)/`migrate_task`(smt_balance)/`migrate_llc_task` branches | `calculate_imbalance()`'s first ~50 lines (`fair.c:12583-12622`) dispatch on `busiest->group_type` for misfit/asym_packing/smt_balance/llc_balance BEFORE ever reaching the has_spare/overloaded logic our sim ports | NOT MODELED | neutral (same reason as §4: none of these group types exist in this sim's 3-type classification, so there's nothing to dispatch to) | periodic, burst |
| `_balance_domain`'s `local_type == GROUP_OVERLOADED` block (`LoadBalancer.py:351-357`): 3 checks, then falls to the generic `min()`-trick migrate_load formula | Real "both overloaded" path (`fair.c:12718-12753`): same 3 gate checks (`local->avg_load >= busiest->avg_load` / `>= sds.avg_load` / `100*busiest<=imbalance_pct*local`) then the same `min()` migrate_load formula (`fair.c:12748-12752`) -- **MATCH**, already correctly ported | MATCH for the gate checks and formula shape; **see §8's new row** for a NUMA-adjustment step this branch applies that real Linux's overloaded-vs-overloaded path never does | neutral for the checks/formula; the NUMA adjustment (only relevant when `domain.is_numa`) is a separate, additional mismatch, see §8 | periodic, burst |
| No `sched_balance_find_src_group()`-level `out_balanced` pre-filter before `calculate_imbalance()` is even called | `fair.c:12871-12908`: several early bail-outs BEFORE `calculate_imbalance()` runs -- `is_rd_overutilized`/EAS check, `local->group_type > busiest->group_type` re-check, and critically: **`if (busiest->group_type != group_overloaded) { if (!env->idle) goto out_balanced; ... }`** (`fair.c:12871-12881`) -- on a BUSY checking CPU, real Linux refuses to balance at all unless busiest is genuinely overloaded (or the SMT/idle-diff/`sum_h_nr_running==1` carve-outs at `fair.c:12881-12905` apply) | UNDOCUMENTED MISMATCH | earlier/more often (sim balances from busy checkers even when busiest isn't overloaded; real Linux mostly refuses to) | periodic, burst |

**Measured impact of the `!idle` out_balanced gate (Toggle B, same
script, first-order approximation of the gate -- `b_type !=
GROUP_OVERLOADED and not local_core.is_idle(): return 0`, applied right
after `busiest` is classified):**

| workload | metric | default | +idle_gate | Δ |
|---|---|---|---|---|
| stacked_high | p95_wait | 22.30 | 22.30 | **0.00%** (10/10 seeds byte-identical) |
| stacked_high | migrations | 471.1 | 471.1 | 0.00% |
| bursty_high_s64 | p95_wait | 48.41 | 48.41 | 0.00% |
| bursty_high_s64 | migrations | 1629.5 | 1629.5 | 0.00% |

Measured, honestly reported: **zero effect** at these two high-intensity
workloads and seeds -- the condition (`busiest` not overloaded AND the
checking core busy) simply never occurred in any of the 40 runs. Under
`stacked_high`/`bursty_high_s64`, whenever a periodic check actually
reaches `_balance_domain`, `busiest` is essentially always already
`GROUP_OVERLOADED` (heavy sustained/bursty load keeps queues backed up),
so this gate is dormant at these intensities. This does NOT mean the gap
is unreal or unimportant -- it means its effect, if any, is concentrated
at lower intensities (`stacked_medium`/`stacked_low`, where `has_spare`/
`fully_busy` busiest groups are common) or workloads not tested here.
Flagged for follow-up at medium/low intensity rather than ranked as
high-priority on this evidence alone.

---

## 6. `sched_balance_find_src_rq()`: busiest-rq selection per migration_type

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `_migrate_load`: `src_core = max(busiest_cores, key=lambda c: c.load())` | `switch (env->migration_type) { case migrate_load: ... }` (`fair.c:12995`): `load = cpu_load(rq)`, picks the rq maximizing `load*busiest_capacity > busiest_load*capacity` (`fair.c:12995-13022`) | MATCH | neutral | periodic, burst |
| `_migrate_util`: `src_core = max(busiest_cores, key=lambda c: c.load())` (same load-based pick, not util-based) | `case migrate_util:` (`fair.c:13024-13039`): `util = cpu_util_cfs_boost(i)`, skips single-task rqs (`nr_running <= 1: continue`), picks highest **util**, not load | UNDOCUMENTED MISMATCH | neutral-to-minor (both metrics track "which core is busiest" similarly in this homogeneous sim; only matters when load and util diverge, e.g. many newly-queued-but-not-yet-run heavy tasks on one core vs fewer-but-longer-running tasks on another). Also: real Linux explicitly skips single-task rqs for `migrate_util`, not ported | periodic, burst |
| `_migrate_tasks`: `src_core = max(busiest_cores, key=lambda c: len(c.rq))` | `case migrate_task:` (`fair.c:13041-13045`): picks the rq maximizing `nr_running` -- matches `len(c.rq)` | MATCH | neutral | periodic, burst |
| No per-CPU `rt`/`fbq_type` (NUMA-locality) classification filter | `fbq_classify_rq(rq)` / `env->fbq_type` (`fair.c:12939-12947`) skips CPUs whose runnable tasks are "ideally NUMA-placed" before this sim's much simpler topology considers them | NOT MODELED | neutral (this sim has no NUMA task-placement-preference/auto-numa-balancing concept at all -- nothing to be biased against) | periodic, burst |

---

## 7. `detach_tasks()`/`can_migrate_task()`: order, skip-if-too-big, cache-hot

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `_migrate_tasks`/`try_newidle`: oldest-first, `src_core.rq[0]` | `detach_tasks()` walks `list_last_entry(tasks, ..., se.group_node)` -- the CFS runqueue's own list order (tail), not literally FIFO by arrival but functionally close for this sim's append-ordered `rq` list (`fair.c:10937`) | MATCH (documented in-code as an intentional fix, `LoadBalancer.py:626-638`) | neutral | periodic, burst, newidle |
| `_migrate_load`: **heaviest**-first, `max(candidates, key=weight)` | Same `detach_tasks()` list-order walk applies to EVERY `migration_type`, including `migrate_load` -- real Linux does NOT reorder by weight; it walks the list and applies a running `env->imbalance -= load` decrement per candidate in LIST order, skipping (not reordering) ones that don't fit (`fair.c:10942-10960`) | UNDOCUMENTED MISMATCH | more selective/different set migrated (heaviest-first converges to the imbalance target in fewer, larger migrations than Linux's list-order-with-skip would) | periodic, burst |
| `_migrate_load`'s tiny-task skip: `min_task_weight = 16 if failed==0 else max(1, 16>>min(failed,4))`, applied as a pre-filter over ALL candidates at once | `sched_feat(LB_MIN) && load < 16 && !nr_balance_failed: goto next` (`fair.c:10951-10953`) -- same threshold (16), but applied per-candidate DURING the list walk, with `shr_bound(load, nr_balance_failed)` (a bit-shift, not the sim's `16 >> min(failed,4)` approximation) gating the "too big for remaining budget" skip separately (`fair.c:10955-10960`) | DOCUMENTED SIMPLIFICATION | neutral (threshold value matches; only the exact shape of the `nr_balance_failed` relaxation curve differs, already flagged in-code as "verified: load<16 unless nr_balance_failed") | periodic, burst |
| **No cache-hot check anywhere** in `_migrate_load`/`_migrate_tasks`/`_migrate_util`/`_do_migrate` | `can_migrate_task()` calls `task_hot(p, env)` (`fair.c:10291-10329`, `10823`): a task is cache-hot (and normally NOT migrated) if `rq_clock_task(env->src_rq) - p->se.exec_start < sysctl_sched_migration_cost` (**500,000 ns = 0.5 sim-ms**, `fair.c:82`) -- exceptions only for active-balance, NUMA-preferred destination, or `nr_balance_failed>0` (`fair.c:10796+`) | UNDOCUMENTED MISMATCH, now MEASURED (see below) | more migrations than Linux would allow, but on a MUCH SMALLER SLICE of total migrations than initially assumed -- measured at 2.8%-10.1% across 5 workloads, not "every migration" | periodic, burst, newidle |
| No `task_is_ineligible_on_dst_cpu` EEVDF-eligibility soft-limit | `can_migrate_task()` (`fair.c:10736-10739`): non-eligible tasks (see §10) are refused unless `nr_balance_failed != 0` | NOT MODELED | more migrations (no EEVDF-eligibility-based migration refusal at all) | periodic, burst, newidle |

**Scope, verified this session (`fair.c:4568` [`core.c`], `fair.c:2150-
2156`):** `task_hot()`'s exceptions were re-checked before measuring.
`__sched_fork()` sets `p->se.exec_start = 0` at fork time (`core.c:
4568`); only `update_stats_curr_start()` (`fair.c:2150-2156`, called
when a task is actually PICKED to run) overwrites it with a real
timestamp. So `delta = rq_clock_task(...) - p->se.exec_start` is huge
(current clock minus 0) for a task that has been forked but never yet
run -- `task_hot()` returns false, NOT cache-hot, freely migratable.
Real Linux's cache-hot refusal therefore only ever applies to tasks that
have ACTUALLY RUN within the last 0.5ms, never to freshly-queued,
never-run tasks -- which this sim's `_migrate_util` path (§5, sized in
util_avg, "each candidate task costs its OWN util_avg, near-zero for a
task that's never run") specifically targets. This meant the original
framing above ("applies to literally every migration") overstated the
gap's scope before it was measured.

**Measured (Task 8 pre-audit, Step 4, 2026-09-29):** a TRACKING-ONLY
field, `Task.last_ran_until` (`Task.py`, set in `Core.run()` to
`env.now` at the moment a task stops running; `None` if never run since
fork -- the sim's direct analog of `exec_start`), was added and verified
BYTE-IDENTICAL before/after via a throwaway fingerprint script (12 runs,
2 balancers x 2 workloads x 3 seeds, full migration-event sequences +
summary metrics hashed with sha256: `fe3a33c3...` before AND after the
addition -- the field changes no decision). Then
`development/fidelity_audit/task8_step4_cache_hot_scope.py` (read-only
monkeypatch of `_do_migrate`, records but never alters the would-be-hot
verdict) measured, for every migration in baseline + burst-aware, 5
workloads (`stacked_medium`, `stacked_high`, `rate3.0_s12`,
`bursty_high_s24`, `bursty_high_s64`), 10 seeds (50000-50009),
`checker_model="kernel"` held constant, what fraction would have been
refused as cache-hot (< 0.5ms since last ran) by real Linux:

| workload | baseline: total / would-be-hot | burst-aware: total / would-be-hot | never-run-at-migration-time (both, roughly) |
|---|---|---|---|
| stacked_medium | 3861 / 178 (4.6%) | 3744 / 215 (5.7%) | ~55-58% |
| stacked_high | 4711 / 132 (2.8%) | 4334 / 176 (4.1%) | ~62-66% |
| rate3.0_s12 | 3038 / 125 (4.1%) | 3019 / 136 (4.5%) | ~54-60% |
| bursty_high_s24 | 801 / 81 (10.1%) | 801 / 81 (10.1%) | ~70% |
| bursty_high_s64 | 16295 / 890 (5.5%) | 17153 / 933 (5.4%) | ~16-17% |

Full detail (breakdown by `periodic`/`newidle`/`burst` trigger) in
`results_task8_step4_cache_hot_scope_by_trigger.csv` -- notably,
burst-triggered migrations are 88-99.8% never-run tasks with correspondingly
near-zero cache-hot exposure (0-5.6%), since the burst path's `_migrate_
util` mechanism specifically targets freshly-queued tasks (§5/§6). This
is an UPPER BOUND on real Linux's refusal rate, not the true rate: it
doesn't account for `task_hot()`'s own exceptions (active-balance,
NUMA-preferred destination, `nr_balance_failed>0`, `fair.c:10796+`) that
let real Linux migrate a hot task anyway in some of these cases -- the
true fraction real Linux would additionally refuse (beyond what those
exceptions already permit) is `<= these percentages`. Real, but modest
-- see the revised §14 ranking.

---

## 8. `adjust_numa_imbalance()` / `imb_numa_nr`

Previously marked in-code as "our own stand-ins ... never opened this
session" (`LoadBalancer.py:36-41`). Now opened and verified:

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `NUMA_IMBALANCE_MIN = 32` (compared directly against a raw task-count `imbalance`/`raw` value in both `_balance_domain` and `_balance_has_spare_kernel`) | `#define NUMA_IMBALANCE_MIN 2` (`fair.c:2177`), compared against the SAME raw pre-halving task-count `imbalance` (`fair.c:2195-2196`) -- **the sim's threshold is 16x too large** | UNDOCUMENTED MISMATCH (was flagged "unverified"; now confirmed wrong by a factor of 16) | less balancing at NUMA levels (sim forgives NUMA imbalances up to 32 excess tasks; real Linux forgives at most 2) | periodic, burst (onehop/machine levels only) |
| `_balance_domain`'s GENERIC migrate_load path (`LoadBalancer.py:386-387`) also calls `self._adjust_numa_imbalance(imbalance, local_run)` whenever `domain.is_numa`, on the `min()`-trick's LOAD-unit `imbalance` value -- reached both from the "both overloaded" case (§5, local_type==GROUP_OVERLOADED) and from the legacy `imbalance_model="legacy"` has-spare-fallthrough case | **Checked directly this session: within `calculate_imbalance()` (`fair.c:12577-12753`), there is EXACTLY ONE call to `adjust_numa_imbalance()`** (`fair.c:12693`, verified by `awk`-restricting the grep to the function's own line range), and it is INSIDE the `local->group_type == group_has_spare` branch only (`fair.c:12636-12703`). The "both overloaded" migrate_load branch (`fair.c:12718-12753`) never calls `adjust_numa_imbalance()` at all -- real Linux does not forgive small NUMA imbalances in that case, ever | UNDOCUMENTED MISMATCH (missed in this file's first pass; added per direct instruction to re-check) | less balancing at NUMA levels specifically in the overloaded-vs-overloaded case -- an ADDITIONAL bias on top of §5's already-flagged `!idle` out_balanced gap and this section's `NUMA_IMBALANCE_MIN` magnitude error, all three pulling the "both overloaded, NUMA domain" scenario toward less balancing than real Linux | periodic, burst (onehop/machine, `local_type==GROUP_OVERLOADED` specifically -- and also the non-default `imbalance_model="legacy"` has-spare-fallthrough case) |
| `NUMA_DST_BUSY_THRESHOLD = 2` | `imb_numa_nr` is NOT a constant -- computed per-topology by `topology.c:adjust_numa_imbalance()` (`topology.c:2870-2934`). For THIS sim's topology (4 nodes x 8 cores, 3-node onehop, single LLC per node): `nr_llcs = onehop_span/node_span = 24/8 = 3` (not 1, so `imb = nr_llcs = 3`, `topology.c:2903-2906`); both onehop's and machine's upward-propagation `factor = max(1, span/imb_span)` evaluate to 1 (`imb_span` is set from the machine-level NUMA domain, `topology.c:2921-2929`), so **`imb_numa_nr = 3`** at both onehop and machine for this topology, not 2 | UNDOCUMENTED MISMATCH (minor -- was flagged "unverified"; now confirmed off by 1) | slightly less balancing (destination-busy gate trips one task-count sooner in the sim than in real Linux) | periodic, burst |

**Measured impact of correcting both constants (Toggle C, `NUMA_IMBALANCE_MIN=2`, `NUMA_DST_BUSY_THRESHOLD=3`), same 10 seeds:**

| workload | metric | default | corrected | Δ | significance |
|---|---|---|---|---|---|
| stacked_high | p95_wait | 22.30 | 22.15 | -0.69% | not significant (wins=5 harms=3 ties=2, sign_p=0.73) |
| stacked_high | migrations | 471.1 | 475.5 | +0.93% | not significant (wins=4 harms=4 ties=2, sign_p=1.0) |
| bursty_high_s64 | p95_wait | 48.41 | 48.41 | 0.00% | byte-identical, all 10 seeds |
| bursty_high_s64 | migrations | 1629.5 | 1629.5 | 0.00% | byte-identical, all 10 seeds |

Measured, honestly reported: despite being wrong by a factor of 16 on
paper, correcting `NUMA_IMBALANCE_MIN`/`NUMA_DST_BUSY_THRESHOLD` has
**no measurable effect** at these two high-intensity workloads and
seeds. Consistent with most balancing activity under heavy/bursty load
happening at the pair/node (non-NUMA) levels, where this constant is
never consulted at all (`_adjust_numa_imbalance` is only called when
`domain.is_numa`, i.e. onehop/machine). Real-magnitude-wrong, but
apparently low-leverage for THESE workloads -- worth documenting as
correct-the-number-anyway (it's a one-line, zero-risk, now-verified fix)
but not worth ranking as a high-priority driver of any current result.

---

## 9. `sched_balance_newidle()`: cost gating vs single-attempt

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `newidle_mode="transition"` (default): fires `_newidle_attempt()` exactly once on the busy->idle edge, unconditionally -- no cost check at all | `sched_balance_newidle()` (`fair.c:14347-14420`): gated on `this_rq->avg_idle < sd->max_newidle_lb_cost` (skip entirely if the CPU's recent idle time is too short to be worth the cost of searching, `fair.c:14390-14395`), and even once past that gate, walks `for_each_domain` accumulating `curr_cost` and bails the moment `avg_idle < curr_cost + sd->max_newidle_lb_cost` (`fair.c:14412-14414`) -- an adaptive, self-measuring cost BUDGET, not a single fixed attempt | DOCUMENTED SIMPLIFICATION (code already states this exact gap, `LoadBalancer.py:82-88`) | earlier/more often (no cost gate at all -- always attempts once, where real Linux might skip entirely on a CPU with a history of long, expensive newidle searches and short idle periods) | newidle |
| `_newidle_attempt()` scans exactly ONE domain level and returns on first success | Real newidle walks the FULL `for_each_domain` chain in one pass (bounded by the cost budget above), same as `sched_balance_domains()`'s per-level loop, not a single-level probe | DOCUMENTED SIMPLIFICATION (implied by `try_newidle`'s own `while d is not None: ... d = d.parent` loop, which DOES climb the chain -- re-reading `LoadBalancer.py:594-612` this session: `try_newidle` itself climbs every level, calling `_newidle_attempt` once per level and returning on the first success. This is actually a closer match to the real per-domain-chain walk than previously summarized) | MATCH (corrected from a prior overly-pessimistic characterization -- see below) | neutral | newidle |
| `newidle_mode="legacy_ema"` (opt-in, non-default): EMA success-rate gate, `core.newidle_cost_avg` | Not a Linux mechanism at all -- an ad hoc, since-superseded approximation, already the non-default | DOCUMENTED SIMPLIFICATION (opt-in only, explicitly kept for reproducing pre-Fix-3b behavior) | n/a (not the default) | newidle |

Correction made during this audit: the earlier session's characterization
of `_newidle_attempt` as scanning only one level and never climbing was
imprecise -- `try_newidle()` (the caller) DOES climb `d = d.parent` across
every level in the chain, calling `_newidle_attempt` fresh at each; what
it does NOT do is Linux's cost-budget accounting (`curr_cost`/`avg_idle`)
that can cut that walk short partway up. That specific gap (no cost
budget, so the walk always goes as high as needed to find a task) is the
real, already-documented mismatch -- not "single-level" as previously
loosely stated.

Not independently measured: `avg_idle`/`max_newidle_lb_cost` cost
tracking doesn't exist anywhere in `Core`/`LoadBalancer` today (no
"how long was this core idle" or "how much simulated wall-clock time did
the last newidle search cost" state at all -- `pick_next()`/
`try_newidle()` are explicitly documented as costing zero simulated time,
`Core.py`'s `run()` docstring). Marked **needs code change to measure**.

---

## 10. EEVDF: base_slice, deadline formula, eligibility

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `Core.py` `TIME_SLICE = 4` (sim-ms) -- used BOTH for `_set_deadline()`'s deadline formula AND as the actual run quantum (`Core.run():281`, `slice_len = min(TIME_SLICE, task.remaining_time)` -- how long a core holds a task before re-picking) | `sysctl_sched_base_slice`'s RAW default is `700000ULL` ns = 0.7ms (`fair.c:79`), but this is NOT the effective value at runtime: `sched_init_granularity()` (`fair.c:223-226`, called at boot) calls `update_sysctl()` (`fair.c:213-221`), which does `sysctl_sched_base_slice = factor * normalized_sysctl_sched_base_slice` where `factor = get_update_sysctl_factor()` (`fair.c:192-211`). Under the DEFAULT `sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_LOG` (`fair.c:72`): `factor = 1 + ilog2(min(num_online_cpus(), 8))`. For this sim's 32-cpu topology (or any machine with >= 8 online CPUs -- the `min(..., 8)` caps the scaling input): `factor = 1 + ilog2(8) = 1 + 3 = 4`. **Effective `sysctl_sched_base_slice` = 4 x 0.7ms = 2.8ms**, not 0.7ms -- and this SCALED value is what `update_deadline()` actually reads (`se->slice = sysctl_sched_base_slice`, `fair.c:1249`, the global variable, not the `normalized_` one) | DOCUMENTED SIMPLIFICATION, but magnitude and bias corrected 2026-09-29 (see note below) | **NOT neutral**: `TIME_SLICE` sets how long a core holds its current task before a newly-queued task on a piled-up core gets its first chance to run -- directly on the p95_wait critical path, not just an internal deadline-ordering detail. Sim's 4ms quantum is ~1.43x the real EFFECTIVE 2.8ms (not ~5.7x vs the raw, unscaled 0.7ms figure this row previously compared against) -- biasing the sim toward SLIGHTLY LONGER per-task-turn hold times, and therefore slightly WORSE (higher) p95_wait for tasks queued behind a running one, than real Linux's actual scaled slice would produce | in-core scheduling, and now understood to reach p95_wait, this audit's own headline balancing metric |
| `_set_deadline()`: `deadline = vruntime + TIME_SLICE*(NICE_0_WEIGHT/weight)` | `update_deadline()` (`fair.c:1238-1254`): `se->deadline = se->vruntime + calc_delta_fair(se->slice, se)`, where `calc_delta_fair` scales `delta` by `NICE_0_LOAD/se->load.weight` when weight != `NICE_0_LOAD` (`fair.c:297-302`) -- same formula shape | MATCH | neutral | scheduling |
| `avg_vruntime()` / `EevdfTree` (ported `pick_eevdf()`) | `vruntime_eligible()` (`fair.c:894-925`): `avg >= key*load` where `avg = cfs_rq->sum_w_vruntime` (weighted sum) and `key = vruntime - zero_vruntime` -- a weighted-average-vs-V comparison | Previously confirmed MATCH (earlier session; "real augmented rbtree, ported from `pick_eevdf()`") -- not re-verified line-by-line this session, no new evidence either way | neutral (no change from prior finding) | scheduling |

**Correction, 2026-09-29:** this row previously compared the sim's
`TIME_SLICE=4` against `sysctl_sched_base_slice`'s raw, un-booted-scaled
default (0.7ms) and called the ~5.7x gap "neutral-ish" on the theory that
a uniform slice only stretches absolute granularity, never relative
fairness ordering. Both halves of that were wrong: (1) the raw 0.7ms
figure is not what runs on any real >=8-CPU machine -- `sched_init_
granularity()` scales it by `1+ilog2(min(ncpus,8))=4` at boot under the
default tunable-scaling policy, so the true comparison is 4ms (sim) vs
2.8ms (real, effective) -- a ~1.43x gap, not ~5.7x; and (2) "relative
fairness ordering" was the wrong lens -- `TIME_SLICE` isn't just a
deadline-formula input here, it's literally `Core.run()`'s dispatch
quantum (`slice_len = min(TIME_SLICE, task.remaining_time)`), so it
directly sets how long a queued task waits behind whichever task is
currently running, which is exactly p95_wait, this audit's own primary
metric. Not independently measured this session (would need a `TIME_
SLICE`-scaling toggle run the same way as Toggle A/B/C); flagged as a
real, now-correctly-characterized, and non-trivial candidate for a
future measurement -- see the revised §14.

---

## 11. Placement: fork-path core selection

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `Placement.hierarchical_new_task_placement(machine, entry_core)`: custom top-down domain descent (least-loaded child domain, then `select_idle_sibling`-style bottom search) -- NOT a literal port of any single v7.2 function (v7.2 has no function literally named `sched_balance_find_dst_group`/`_cpu`; the fork path in this kernel version goes through `select_task_rq_fair()` -> `wake_affine()`/domain descent -> `select_idle_sibling()`, `fair.c:8802` and surrounding) | Functionally analogous but not a byte-for-byte port -- already the documented state (Topology.py/Placement.py docstrings) | DOCUMENTED SIMPLIFICATION | neutral (no new finding; consistent with prior sessions) | placement |
| `Topology.build_topology()` builds one `machine` `Domain` PER NODE (`machines = []` accumulator, one appended per iteration of `for i in range(num_nodes): ... machines.append(machine)`, `Topology.py:434-456`), then `return machines[0], all_cores` (`Topology.py:458`) -- `machines[0]` (node 0's own machine domain) is the ONLY one ever handed back to a caller; `Main.py:36-37` receives just that single `machine` object and every task's placement (`Main.py`'s `place()` closure, `select_core_for_task(..., machine=machine)`) descends from it regardless of which node the task's `entry_core` is actually on | n/a -- this is the sim's own placement entry point, not a Linux function; the finding is about the sim's fidelity to its OWN documented per-node-anchored design (module docstring points 1-2, `Topology.py:36-47`), not a kernel comparison | **UNDOCUMENTED MISMATCH (real, confirmed this session, RETRACTING last session's incorrect "correction")** | placement only bias: node1/node3-forked tasks' top-level "local" domain resolves to node0's onehop (`onehop0`) instead of their own (`onehop1`/`onehop3`) -- see below | placement |

**RETRACTION, 2026-09-29 (this is a correction OF a correction -- the
wrong entry from last session is left below per ledger discipline, not
deleted):** last session's §11 claimed "there is only ONE `machine`
object in this sim -- no `machines[]` array to index into," and used
that to retract the original Phase-10 finding as a mischaracterization.
That claim was checked against `Main.py` alone (which only ever sees a
single `machine` variable) and was WRONG -- `Topology.build_topology()`
was never actually read before making it, and does exactly what the
original finding said: it builds a `machines = []` list with one
`Domain` PER NODE (`Topology.py:434-456`, one loop iteration per node,
`machines.append(machine)` each time) and returns only `machines[0]`
(`Topology.py:458`) to every caller. Node 0's machine domain is
literally the "fixed root every caller descends from," in `build_
topology()`'s OWN docstring wording (`Topology.py:356-358`, unchanged
since before this session and never itself in question).

The user's counter-mechanism is also confirmed exactly right:
`home_children` does NOT anchor onehop `.parent` relative to node 0 for
everyone -- each onehop is built with `home_children=[nodes[i]]`
(`Topology.py:406-418`, the "Pass 1" loop), so `nodes[i].parent` is set
to `onehop_i`, i's OWN onehop, for every `i` -- confirmed by re-reading
that loop directly. `domain_chain()`-based BALANCING is therefore
correctly per-node-anchored, exactly as both the original finding and
the CAVEAT already written into `Topology.py`'s own docstring
(`Topology.py:369-387`, dated 2026-09-29, i.e. already present in the
codebase BEFORE this audit task even started) say. The bug is
PLACEMENT-only: `machines[0].children == [onehop0, onehop2]` (verified
by tracing `build_topology()`'s Pass 2 for `i=0`: `neighbor_idx={1,3}`,
`far_idx=[2]`, so `machine0 = Domain(..., [onehop0] + [onehop2], ...)`),
and `hierarchical_new_task_placement()`'s local-domain lookup takes the
FIRST child whose `.cores()` contains `entry_core` -- since `onehop0`'s
span is `{node0,node1,node3}` (self + `ring_neighbors(0,4,2)={1,3}`) and
`onehop2`'s span is `{node1,node2,node3}`, a node1- or node3-forked
task's top-level "local" is `onehop0` (listed first, and it already
contains both), NEVER `onehop1`/`onehop3` -- exactly matching `Topology.
py`'s own in-code CAVEAT text ("node0->onehop0, node1->onehop0,
node2->onehop2, node3->onehop0"). The original finding stands, unaltered
in substance; only last session's retraction of it is now itself
retracted. See `docs/NOTEBOOK.md`'s dated correction-of-the-correction
entry for the ledger trail.

Not independently measured this session (out of scope -- item 11 was to
audit and now correctly restate the finding, not re-measure it;
Placement fixes remain explicitly not-yet-implemented per prior
sessions).

---

## 12. PELT: decay law and util_avg/load_avg/runnable_avg usage

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `Core.py` `PELT_DECAY_PER_TICK = 0.5**(1/32)` | `pelt.c:29-31` comment: "Approximate: val*y^n, where y^32 ~= 0.5"; `decay_load()` (`pelt.c:32-56`) implements exactly this via a precomputed `runnable_avg_yN_inv` table | MATCH, re-confirmed with citation this session | neutral | all (util_avg feeds `_migrate_util`, load feeds `_migrate_load`/classification) |
| `Core.util_avg` used for `_migrate_util`'s sizing (Fix C) and for `Core._initial_util_avg()` (port of `post_init_entity_util_avg`, `fair.c:1315-1351`, cited in-code) | `sa->util_avg` (`pelt.c:266-267`, `WRITE_ONCE(sa->util_avg, sa->util_sum/divider)`) -- running-time-only signal, used identically for `migrate_util` sizing in real Linux (`fair.c:12660`, `local->group_util`) | MATCH | neutral | balancing (has-spare/migrate_util path) |
| `Core.load()`/queue-aware `load_model="runnable"` (Fix B) used for `_migrate_load`, `classify_group`, checker-election load comparisons | `sa->load_avg`/`sa->runnable_avg` (`pelt.c:264-266`) -- running+queued signal, used for `cpu_load()`/`group_load` throughout `calculate_imbalance()`/`update_sd_lb_stats` | MATCH (already the subject of a prior-session fix, Fix B, confirmed here still wired through consistently) | neutral | balancing, classification |

No new PELT findings this session beyond re-confirming the decay-law
citation; Fix B/Fix C already addressed the util_avg-vs-load_avg
conflation in a prior session.

---

## 13. The burst path: every Linux-derived function it calls

Re-read `BurstScheduler.py` in full this session (127 lines). `on_task_
placed()` (`BurstScheduler.py:74-122`) is the entire burst mechanism; the
detector itself (`BurstDetector.is_burst()`) is NOT Linux-derived (it's
the paper's own novel signal). Once triggered (post-cooldown), for every
domain in `domain_chain(core)` it calls, IN ORDER:

1. `self._find_checker(domain)` (`BurstScheduler.py:105`, **no `from_core`
   argument**) -> always the LEGACY whole-span election
   (`LoadBalancer._find_checker`, `LoadBalancer.py:262`, the `from_core
   is None` branch) -- **never** the Task-7 `checker_model="kernel"`
   fix, regardless of the balancer's `checker_model` setting. This is
   the concrete instance of §3's general point.
2. `self._balance_domain(domain, checker, now, tag="burst")`
   (`BurstScheduler.py:106`) -> the FULL periodic pipeline: `classify()`
   / `classify_group()` (§4), `_balance_has_spare_kernel()` or the
   legacy min()-trick (§5), `_migrate_util()`/`_migrate_tasks()`/
   `_migrate_load()` (§6), `_adjust_numa_imbalance()` (§8), `_do_migrate()`
   (§7's cache-hot gap applies here too).

**Every UNDOCUMENTED MISMATCH found in §1, §4, §5, §6, §7, §8 above
therefore affects the burst path identically to the periodic path**,
with two burst-specific exceptions:
- §1's periodic-trigger/busy_factor/NOHZ findings do NOT apply to the
  burst path's OWN trigger (it's event-driven off `on_task_placed`, not
  the 1ms ticker) -- but DO still apply to `_balance_domain`'s internal
  logic once triggered (§4/§5/§6/§7/§8 all still apply).
- §3's `checker_model="kernel"` fix does not reach the burst path at all
  (see point 1 above) -- a strictly WORSE checker-election fidelity gap
  for burst than for periodic (periodic can opt into the fix; burst
  cannot, today).

---

## 14. Ranked recommendations: FIX before the Task 8 re-run vs DOCUMENT

No fixes were implemented in this task, per the explicit constraint.
Ranking is by (a) measured or plausible impact on the specific "does
burst-aware react earlier than periodic" comparison, and (b) how cheaply
each could be fixed as a follow-up opt-in flag consistent with this
project's established pattern (Fix A/B/C/D).

**Note (2026-09-29 corrections pass):** the numbered labels below (Rank
1, Rank 2, ...) are kept as originally assigned rather than renumbered
after Step 4's measurement demoted cache-hot's priority -- Rank 2's own
text says so explicitly. Read the numbers as historical/audit-trail, the
prose as current.

### Rank 1 -- FIX before the re-run: missing `busy_factor` (§1)

Measured: +26% to +48% p95_wait, -29% to -69% migrations when corrected,
both highly significant (sign_p <= 0.022, wilcoxon_p <= 0.008) at TWO
high-intensity workloads. This is not a minor nuisance -- it is
currently inflating the periodic path's own reactivity by checking busy
cores ~16x more often than real Linux would (`get_sd_balance_interval()`,
`fair.c:13565-13586`, `sd_init()`'s `busy_factor=16`, `topology.c:1958`).
The reason to fix it is fidelity, full stop: the simulator claims to
model `sched_balance_domains()`'s interval-gating and currently doesn't
apply one of that function's two multiplicative factors. Separately, and
regardless of which direction any given comparison moves once fixed: the
paper's central claim is comparative ("burst path reacts earlier than
periodic"), and an artificially fast periodic baseline mechanically
narrows the measured gap between the two paths, whichever way that gap
would otherwise point -- so the current confirmation-run numbers should
not be read as the true magnitude of that comparison until this is
fixed or explicitly accounted for.

### Rank 2 -- lower priority than first assumed: cache-hot / `task_hot()` (§7), now MEASURED

Originally ranked #2 sight-unseen ("applies to literally every
migration"). Now measured (Step 4, 2026-09-29, tracking-only `Task.
last_ran_until`, verified byte-identical before/after via fingerprint
hash): only 2.8%-10.1% of migrations across 5 workloads would actually
be refused as cache-hot -- most sim migrations are either already-cold
(ran >0.5ms ago) or, especially on the burst path (88-99.8%), of tasks
that have NEVER run since being queued, which real `task_hot()` would
never block either (`exec_start=0` at fork, `core.c:4568`). Still a
real, one-directional (more-migrations-than-Linux) bias, and still
one-directional in the same way §1's busy_factor gap is -- but an order
of magnitude smaller in apparent reach than originally assumed.
Demoted from "fix before the re-run" to: worth implementing the actual
`can_migrate_task()` refusal (not just tracking) as a future opt-in
flag, but not urgent enough to block the Task 8 re-run on, given the
measured scope.

### Rank 3 -- FIX, cheap and zero-risk: `NUMA_IMBALANCE_MIN`/`NUMA_DST_BUSY_THRESHOLD` (§8)

Measured: no significant effect at stacked_high/bursty_high_s64. But the
fix is a one-line constant correction (32->2, 2->3) with a now-fully-
verified derivation, zero implementation risk, and the two workloads
tested here are exactly the ones where NUMA-level balancing matters
LEAST (heavy/bursty load balances mostly at pair/node level). Recommend
fixing it regardless of the null result here -- it costs nothing and
removes a known-wrong number from the codebase -- but do NOT expect it
to change the confirmation-run headline numbers.

### Rank 4 -- DOCUMENT, do not fix now: `update_sd_pick_busiest()` type-ordering (§4)

Real, undocumented, but entangled with the already-accepted 3-type
`group_classify()` simplification -- fixing the ordering alone without
also expanding the type enum would be a partial, possibly-inconsistent
change. Recommend documenting as a known simplification (this file) and
folding it into a future, larger "expand group_classify toward the real
8-type enum" task if the paper's reviewers or the user ever need the
higher-fidelity comparison -- not before the Task 8 re-run.

### Rank 5 -- DOCUMENT, low measured leverage: `!idle` out_balanced gate (§5)

Real (grounded directly in `fair.c:12871-12881`), but measured ZERO
effect at both tested high-intensity workloads because `busiest` is
essentially always `group_overloaded` under sustained/bursty load in
this sim -- the gate is dormant exactly where the paper's headline
comparisons run. Recommend documenting rather than fixing before the
re-run; revisit only if a future task specifically targets
medium/low-intensity fidelity (where this gate would plausibly matter
more, per the reasoning in §5, though this was NOT measured and should
not be assumed).

### Rank 6 -- DOCUMENT, needs code change to even measure: NOHZ/ILB (§2), newidle cost-gating (§9), `migrate_util` rq-selection metric (§6)

All three are real, grounded in kernel source, but none is cheaply
togglable without adding new simulator state (idle-CPU tickless
modeling; `avg_idle`/`max_newidle_lb_cost` tracking; per-core util
history for `sched_balance_find_src_rq`'s `migrate_util` case
respectively). None showed up as an obvious first-order concern in the
already-measured findings above. Recommend documenting as known gaps and
leaving them for a dedicated future task if and when the paper's scope
expands to newidle-path or idle-CPU-modeling fidelity specifically --
neither is squarely in the "does burst-aware react earlier than
periodic" critical path the way §1 and §7 are.

### Not applicable / correctly a non-fix: `group_classify()`'s missing 5 types (§4), NOHZ-blocked-load (§2), fork-path placement's non-literal port (§11), EEVDF `base_slice` magnitude (§10)

Each of these was checked and found to be either (a) modeling a Linux
mechanism that requires hardware/topology features this sim
deliberately doesn't have (heterogeneous cores, task affinity masks,
`SD_ASYM_PACKING`), or (b) a uniform scaling factor that doesn't distort
the RELATIVE comparisons the paper actually makes. No action recommended
beyond the documentation already present in this file.

---

## STATUS UPDATE (2026-09-29d/e): all six fixes above implemented and made DEFAULT

Every "UNDOCUMENTED MISMATCH" row above whose fix is one of the six
listed in `docs/NOTEBOOK.md`'s 2026-09-29c pre-registration (§1 row 2
busy_factor, §3 checker_model, §7 cache-hot, §8 both NUMA rows, §10
TIME_SLICE, §11 placement_root) is now **FIXED and DEFAULT** as of
2026-09-29e -- implemented behind opt-in flags (Step 1), measured
baseline-only (Step 2, §15 below), then made the plain constructor/
`run_simulation()` defaults (Step 3). The legacy value of each remains
explicitly reachable (see `README.md`'s "Final configuration" section
and each flag's own docstring in `simulator/LoadBalancer.py`/`Main.py`/
`Placement.py`/`Core.py`/`Topology.py`). Findings NOT among these six
(§4's `update_sd_pick_busiest` ordering, §5's `!idle` out_balanced gate,
§6's `migrate_util` rq-selection metric, §9's NOHZ/ILB and newidle
cost-gating) remain exactly as documented above -- unfixed, per §14's
original ranking, which is left unedited below as the historical record
of the decision at the time it was made.

## 15. Task 8 Step 2: baseline-only ablation of the 6 fixes (2026-09-29d)

LoadBalancer only (burst-aware not run anywhere in this task), penalty=0,
10 seeds (60000-60009), 5 workloads. Configurations: `a_all_legacy`
(every fix at its pre-Task-8 value), each fix ALONE on top of `a`, and
`c_all_six` (every fix corrected). Full per-seed and per-metric data:
`development/fidelity_audit/results_task8_step2_ablation_perseed.csv`
and `_summary.csv`. `*` = significant by the corrected sign test
(`harms>wins` required) at p<0.05; `sig_p` shown when significant.

**p95_wait, % change vs all-legacy:**

| workload | checker | busy_factor | placement_root | cache_hot | numa_fix | time_slice | **all six** |
|---|---|---|---|---|---|---|---|
| stacked_medium | -1.9% | +1.2% | 0.0% | -1.9% | -0.9% | **-14.8%\*** (p=.021) | **-19.5%\*** (p=.002) |
| stacked_high | -8.4% | +4.1% | 0.0% | -1.4% | +0.5% | -10.3% | +1.2% |
| rate3.0_s12 | -6.5% | -11.2% | 0.0% | -5.2% | -0.2% | **-21.8%\*** (p=.002) | **-26.4%\*** (p=.002) |
| bursty_high_s24 | +0.5% | -1.1% | +3.8% | +7.0% | 0.0% | -11.4% | **-17.3%\*** (p=.021) |
| bursty_high_s64 | **-17.3%\*** (p=.002) | **+18.7%\*** (p=.002, HARM) | -1.3% | **-5.8%\*** (p=.021) | 0.0% | **-21.9%\*** (p=.021) | **-10.3%\*** (p=.021) |

**total_migrations, % change vs all-legacy:**

| workload | checker | busy_factor | placement_root | cache_hot | numa_fix | time_slice | **all six** |
|---|---|---|---|---|---|---|---|
| stacked_medium | **+23.7%\*** (p=.002, HARM by migration count) | **-7.5%\*** (p=.002) | 0.0% | -1.3% | +0.9% | +3.4% | **-9.5%\*** (p=.002) |
| stacked_high | +20.3% | **-11.2%\*** (p=.002) | 0.0% | -0.6% | -1.3% | +2.1% | **-12.7%\*** (p=.002) |
| rate3.0_s12 | **+31.2%\*** (p=.002, HARM) | -5.3% | 0.0% | -1.1% | +0.4% | +0.5% | **-10.6%\*** (p=.002) |
| bursty_high_s24 | +2.3% | -1.4% | +0.1% | -0.7% | 0.0% | -1.7% | **-12.2%\*** (p=.004) |
| bursty_high_s64 | **+147.4%\*** (p=.002, HARM) | **-31.1%\*** (p=.002) | +1.7% | -6.0% | 0.0% | -3.4% | **-20.0%\*** (p=.002) |

**Headline observations, reported as measured (no cherry-picking):**

- **`time_slice` (Fix 6) is the single largest, most consistent driver of
  p95_wait/avg_wait reduction** -- significant in 4/5 workloads (all but
  stacked_high, which is directionally the same, -10.3%, just short of
  significance at n=10), -11% to -24% on avg_wait everywhere. It also
  shows a small but significant avg_slowdown INCREASE in 2/5 workloads
  (stacked_medium +3.0%, bursty_high_s64 +2.6%) -- shorter absolute wait,
  slightly worse when normalized by service time. Reported, not hidden.
- **`busy_factor` (Fix 2) is a genuine trade-off, not a pure win**:
  significantly FEWER migrations in every workload (-5% to -31%), but a
  significant p95_wait HARM specifically on `bursty_high_s64` (+18.7%,
  the one workload where bursts create the most sustained busy-core
  load) -- consistent with the earlier Task-8-audit finding
  (`docs/FIDELITY_AUDIT.md` §1) that this fix trades periodic
  responsiveness for fewer, more deliberate checks.
- **`checker_model` (Fix 1, already known from Task 7) is confirmed
  again here**: consistently MORE migrations (significantly so in 3/5
  workloads, up to +147%), with p95_wait improving where that matters
  most (bursty_high_s64, -17.3%\*) and roughly neutral elsewhere.
- **`placement_root` (Fix 3) measured ESSENTIALLY ZERO effect** on
  `stacked_medium`/`stacked_high`/`rate3.0_s12` (10/10 seeds byte-
  identical -- `wins=0 harms=0 ties=10` on every metric) and small,
  non-significant effects on the two `bursty_*` workloads. This does
  NOT mean the fix is inert or buggy: a targeted synthetic test (crafted
  imbalance forcing a node1-anchored entry core through both roots)
  confirms it genuinely changes the resulting placement decision when
  the scenario calls for it (`fixed` -> node0, `own` -> node3 for one
  such constructed case) -- it simply almost never gets triggered by
  these workload generators' actual load patterns at these seeds. A
  real, structurally-fixed asymmetry with apparently low practical
  leverage for THIS paper's workloads.
- **`numa_fix` (Fix 5) measured near-zero effect** everywhere (mostly
  ties or small non-significant swings), consistent with the
  Task-8-audit's own earlier finding (§8) -- most balancing under these
  workloads happens below the NUMA (onehop/machine) levels this fix
  touches.
- **`cache_hot` (Fix 4) measured small, mostly non-significant effects**
  (one exception: `bursty_high_s64` p95_wait -5.8%\*), consistent with
  the Task-8-audit's own earlier finding (§7) that only 2.8%-10.1% of
  migrations are actually cache-hot-eligible for refusal under these
  workloads.
- **`c_all_six` (all fixes together)**: significant p95_wait improvement
  in 4/5 workloads (`stacked_medium` -19.5%\*, `rate3.0_s12` -26.4%\*,
  `bursty_high_s24` -17.3%\*, `bursty_high_s64` -10.3%\*), and NO
  significant change on `stacked_high` (+1.2%, n.s.) -- the one
  workload where `busy_factor`'s harm and `checker_model`'s benefit are
  closest to canceling out. Migrations significantly REDUCED in all 5
  workloads (-9.5% to -20.0%). avg_slowdown shows small, mixed,
  sometimes-significant movement in either direction (stacked_medium
  +2.1%\*, bursty_high_s64 +3.6%\*, both technically a slight harm on
  that one normalized metric even as absolute wait improves) --
  reported for completeness, not smoothed over.

---

## Appendix: kernel source cached this session

`/tmp/pelt_v72.c` (490 lines, newly fetched), `/tmp/core_v72.c` (11284
lines, newly fetched, used for the NOHZ softirq trigger point only --
most balancing logic in this kernel version lives in `fair.c`, not
`core.c`). Reused from Task 7: `/tmp/fair_v72.c` (15461 lines),
`/tmp/topology_v72.c` (3504 lines), `/tmp/sched_v72.h` (4216 lines).

## Appendix: measurement scripts

`development/fidelity_audit/task8_pre_audit_impact_measurements.py` --
monkeypatch-based, no simulator file edited, no default changed. 10
seeds (50000-50009), `stacked_high` + `bursty_high_s64`, baseline
(`LoadBalancer`) only, `checker_model="kernel"` held constant across
default and toggled runs so each toggle's effect is isolated from Task
7's already-measured checker fix. Output:
`development/fidelity_audit/results_task8_pre_audit_impact.csv`.

`development/fidelity_audit/task8_step4_cache_hot_scope.py` -- the ONE
script in this audit that DOES touch simulator files, per explicit
instruction (Step 4): `Task.py`/`Core.py` gained a tracking-only
`last_ran_until` field (verified byte-identical decisions before/after
via a throwaway fingerprint hash, not committed). Baseline + burst-aware,
5 workloads, 10 seeds (50000-50009), `checker_model="kernel"` held
constant. Output: `development/fidelity_audit/results_task8_step4_cache_
hot_scope.csv` and `_by_trigger.csv`.
