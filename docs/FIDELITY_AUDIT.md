# Fidelity Audit (Task 8 pre-audit, 2026-09-29)

Full Linux-fidelity audit of the simulator against kernel v7.2 source
(`raw.githubusercontent.com/torvalds/linux/v7.2/kernel/sched/{fair,core,
topology,pelt}.c`, `sched.h`), done BEFORE the Task 8 grid/confirmation
re-run this was requested to precede. **No simulator logic was changed
in this task** -- audit and measurement only. Every citation below was
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
| `Main.py:83-90` `periodic_ticker()`: every core, every 1 sim-ms, calls `periodic_balance()` unconditionally | `sched_balance_trigger`/`scheduler_tick()` raises `SCHED_SOFTIRQ` only when `time_after_eq(jiffies, rq->next_balance)` (`fair.c:14531`, `run_rebalance_domains` gates on `next_balance`, not every tick) | UNDOCUMENTED MISMATCH | earlier/more often | periodic, burst (shared `_balance_domain`) |
| `LoadBalancer.periodic_balance` interval check: `now - last_balance >= interval` where `interval` is `Domain.balance_interval` / per-cpu state, no scaling | `get_sd_balance_interval(sd, cpu_busy)` (`fair.c:13565-13586`): `interval = sd->balance_interval; if (cpu_busy) interval *= sd->busy_factor;` (**busy_factor = 16**, `topology.c:1958`), then `msecs_to_jiffies`, `-1` if busy, `clamp(1, max_load_balance_interval)` | UNDOCUMENTED MISMATCH | earlier (checks ~16x too often whenever the checking core is busy) | periodic, burst |
| `Topology.py:139-141` `min_interval = max(weight,1)`, `max_interval = 2*min_interval` | `sd_init()`: `.min_interval = sd_weight, .max_interval = 2*sd_weight` (`topology.c:1956-1957`) | MATCH | neutral | periodic |
| `LoadBalancer.periodic_balance:321` `new_interval = d.min_interval if n else min(interval*2, d.max_interval)` | success: `sd->balance_interval = sd->min_interval` (`fair.c:13507`); failure: `sd->balance_interval *= 2` capped at `max_interval` (`fair.c:13556-13557`), but ONLY reached after `out_one_pinned`, and explicitly SKIPPED for `CPU_NEWLY_IDLE`/misfit (`fair.c:13538-13554`) | DOCUMENTED SIMPLIFICATION | neutral (shape matches; the NEWLY_IDLE/misfit skip and the finer pinned-vs-unpinned distinction aren't ported, but the sim's periodic path never calls this from a NEWLY_IDLE context anyway) | periodic |
| No `max_load_balance_interval` clamp modeled | `max_load_balance_interval = HZ * num_online_cpus() / 10` (`fair.c:13692`) -- an upper ceiling independent of any one domain's `max_interval` | NOT MODELED | neutral (this sim's per-domain `max_interval` values, 2..64 "ms", never approach a realistic `HZ*32/10` ceiling anyway) | periodic |

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
| `_balance_domain`'s `local_type == GROUP_OVERLOADED` block (`LoadBalancer.py:351-357`): 3 checks, then falls to the generic `min()`-trick migrate_load formula | Real "both overloaded" path (`fair.c:12718-12753`): same 3 gate checks (`local->avg_load >= busiest->avg_load` / `>= sds.avg_load` / `100*busiest<=imbalance_pct*local`) then the same `min()` migrate_load formula (`fair.c:12748-12752`) -- **MATCH**, already correctly ported | MATCH | neutral | periodic, burst |
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
| **No cache-hot check anywhere** in `_migrate_load`/`_migrate_tasks`/`_migrate_util`/`_do_migrate` | `can_migrate_task()` calls `task_hot(p, env)` (`fair.c:10291-10329`, `10823`): a task is cache-hot (and normally NOT migrated) if `rq_clock_task(env->src_rq) - p->se.exec_start < sysctl_sched_migration_cost` (**500,000 ns = 0.5 sim-ms**, `fair.c:82`) -- exceptions only for active-balance, NUMA-preferred destination, or `nr_balance_failed>0` (`fair.c:10796+`) | UNDOCUMENTED MISMATCH | more migrations than Linux would ever allow (sim is willing to migrate a task that just started running on its current core; real Linux normally refuses for 0.5ms after a task starts running there) | periodic, burst, newidle |
| No `task_is_ineligible_on_dst_cpu` EEVDF-eligibility soft-limit | `can_migrate_task()` (`fair.c:10736-10739`): non-eligible tasks (see §10) are refused unless `nr_balance_failed != 0` | NOT MODELED | more migrations (no EEVDF-eligibility-based migration refusal at all) | periodic, burst, newidle |

The cache-hot gap is the second-largest-magnitude candidate in this
audit by construction (it applies to literally every migration in every
path), but unlike §1's busy_factor it is NOT cheaply monkeypatchable
without editing simulator files: `Task`/`Core` don't currently track
"time since this task last started running on its current core," so
measuring `task_hot()`'s effect would require adding that state to
`Core.py`/`Task.py` first. Marked **needs code change to measure**.

---

## 8. `adjust_numa_imbalance()` / `imb_numa_nr`

Previously marked in-code as "our own stand-ins ... never opened this
session" (`LoadBalancer.py:36-41`). Now opened and verified:

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `NUMA_IMBALANCE_MIN = 32` (compared directly against a raw task-count `imbalance`/`raw` value in both `_balance_domain` and `_balance_has_spare_kernel`) | `#define NUMA_IMBALANCE_MIN 2` (`fair.c:2177`), compared against the SAME raw pre-halving task-count `imbalance` (`fair.c:2195-2196`) -- **the sim's threshold is 16x too large** | UNDOCUMENTED MISMATCH (was flagged "unverified"; now confirmed wrong by a factor of 16) | less balancing at NUMA levels (sim forgives NUMA imbalances up to 32 excess tasks; real Linux forgives at most 2) | periodic, burst (onehop/machine levels only) |
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
| `Core.py` `TIME_SLICE = 4` (sim-ms, fixed for every task) | `sysctl_sched_base_slice = 700000ULL` (`fair.c:79`) = **0.7ms** in nanoseconds, likewise a single global default (per-task `se->slice` can override via `custom_slice`, not used by default either) | DOCUMENTED SIMPLIFICATION, magnitude now verified | neutral-ish (uniform scaling of ALL tasks' slice stretches absolute rescheduling granularity ~5.7x but does not distort RELATIVE deadline ordering between same-weight tasks, since `deadline = vruntime + slice*(NICE_0/weight)` scales identically for every task under a flat global slice) | in-core scheduling only, not balancing |
| `_set_deadline()`: `deadline = vruntime + TIME_SLICE*(NICE_0_WEIGHT/weight)` | `update_deadline()` (`fair.c:1238-1254`): `se->deadline = se->vruntime + calc_delta_fair(se->slice, se)`, where `calc_delta_fair` scales `delta` by `NICE_0_LOAD/se->load.weight` when weight != `NICE_0_LOAD` (`fair.c:297-302`) -- same formula shape | MATCH | neutral | scheduling |
| `avg_vruntime()` / `EevdfTree` (ported `pick_eevdf()`) | `vruntime_eligible()` (`fair.c:894-925`): `avg >= key*load` where `avg = cfs_rq->sum_w_vruntime` (weighted sum) and `key = vruntime - zero_vruntime` -- a weighted-average-vs-V comparison | Previously confirmed MATCH (earlier session; "real augmented rbtree, ported from `pick_eevdf()`") -- not re-verified line-by-line this session, no new evidence either way | neutral (no change from prior finding) | scheduling |

---

## 11. Placement: fork-path core selection

| sim | Linux | status | bias | affects |
|---|---|---|---|---|
| `Placement.hierarchical_new_task_placement(machine, entry_core)`: custom top-down domain descent (least-loaded child domain, then `select_idle_sibling`-style bottom search) -- NOT a literal port of any single v7.2 function (v7.2 has no function literally named `sched_balance_find_dst_group`/`_cpu`; the fork path in this kernel version goes through `select_task_rq_fair()` -> `wake_affine()`/domain descent -> `select_idle_sibling()`, `fair.c:8802` and surrounding) | Functionally analogous but not a byte-for-byte port -- already the documented state (Topology.py/Placement.py docstrings) | DOCUMENTED SIMPLIFICATION | neutral (no new finding; consistent with prior sessions) | placement |
| `hierarchical_new_task_placement(machine, entry_core)`'s `machine` parameter is always `machines[0]` (`Main.py`'s `place()` closure calls `select_core_for_task(task, entry_core, cores_by_id, machine=machine)` with the single `machine` from `build_topology()`, which IS the one true machine root in this sim -- re-checked this session: this sim only ever builds ONE `machine` object, `Main.py:36-37`, `machine, cores = build_topology(...)`) | n/a -- **re-verified this session and this specific claim needs a correction**: the earlier-session "Placement.py machines[0]-fixed-root" finding referred to a DIFFERENT, still-real bug -- `hierarchical_new_task_placement` descending through `domain_chain`/`.parent` relationships that are anchored to a fixed root perspective for node1/node3-forked tasks, not to a literal `machines[0]` array-indexing bug (there is only one machine object, not an array of machines to index wrong). See the correction note below. | -- | -- | placement |

**Correction to a prior-session finding, made during this audit's
re-read of `Placement.py` (124 lines, read in full):** the earlier
"Phase 10" finding described this as `machine` param "always
`machines[0]`" implying multiple machine objects exist and the wrong one
gets picked. Re-reading `Main.py` this session shows there is only ONE
`machine` object in this sim (`build_topology()` returns a single
`machine, cores` pair) -- there is no `machines[]` array to index into.
The REAL asymmetry (still present, still unfixed, still worth the
caveat) is that `Domain.home_children`-based ring topology gives node1/
node3-rooted tasks a `domain_chain()` that climbs through a onehop
domain whose `.parent` was fixed relative to node0's perspective when
the ring was built -- not a `machines[0]` indexing bug, but a "which
onehop domain object does this core's climb actually resolve to"
asymmetry from `build_topology()`'s ring construction. This is a
restatement/correction of the earlier finding's mechanism, not a new bug
and not a retraction of the underlying asymmetry -- both are logged here
per the ledger-discipline rule (see `docs/NOTEBOOK.md` for where the
original finding was first entered) rather than silently overwritten.

Not independently measured this session (out of scope -- item 11 was to
audit and clarify the finding, not re-measure it; Placement fixes remain
explicitly not-yet-implemented per prior sessions).

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

### Rank 1 -- FIX before the re-run: missing `busy_factor` (§1)

Measured: +26% to +48% p95_wait, -29% to -69% migrations when corrected,
both highly significant (sign_p <= 0.022, wilcoxon_p <= 0.008) at TWO
high-intensity workloads. This is not a minor nuisance -- it is
currently inflating the periodic path's own reactivity by checking busy
cores ~16x more often than real Linux would. Because the paper's central
claim is comparative ("burst-aware reacts earlier than periodic"), an
artificially fast periodic baseline can only work AGAINST that claim
being visible -- meaning current results are very likely a conservative
(under-)estimate of burst-aware's true advantage. Fixing this (opt-in,
`checker_busy_factor` flag following the established Fix pattern) before
the re-run would let the paper report the TRUE gap rather than a
narrowed one, which is directly in the user's interest, not just a
fidelity nicety.

### Rank 2 -- FIX before the re-run (if tractable): cache-hot / `task_hot()` (§7)

Not measured this session (needs new `Task`/`Core` state -- "time since
this task started running on its current core" -- which doesn't exist
today). Judged high-priority anyway because it applies to literally
every migration on every path (periodic, newidle, burst), and its
absence can only ever bias toward MORE migrations than real Linux,
never fewer -- a systematic, one-directional bias on the paper's other
headline metric (migration count). Recommend implementing the necessary
`Task`/`Core` timestamp as a small, targeted addition (not a "no
simulator changes" violation of THIS task, but of the NEXT one) and
re-running this section's measurement before deciding whether it's
large enough to matter, rather than shipping the re-run without knowing.

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

## Appendix: kernel source cached this session

`/tmp/pelt_v72.c` (490 lines, newly fetched), `/tmp/core_v72.c` (11284
lines, newly fetched, used for the NOHZ softirq trigger point only --
most balancing logic in this kernel version lives in `fair.c`, not
`core.c`). Reused from Task 7: `/tmp/fair_v72.c` (15461 lines),
`/tmp/topology_v72.c` (3504 lines), `/tmp/sched_v72.h` (4216 lines).

## Appendix: measurement script

`development/fidelity_audit/task8_pre_audit_impact_measurements.py` --
monkeypatch-based, no simulator file edited, no default changed. 10
seeds (50000-50009), `stacked_high` + `bursty_high_s64`, baseline
(`LoadBalancer`) only, `checker_model="kernel"` held constant across
default and toggled runs so each toggle's effect is isolated from Task
7's already-measured checker fix. Output:
`development/fidelity_audit/results_task8_pre_audit_impact.csv`.
