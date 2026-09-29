# Linux Scheduler Simulator v3 — Phase 2 harness + first real results

# Linux Scheduler Simulator — fork-placement baseline vs burst-triggered adaptive

**CORRECTION (2026-09-26t):** this title, and the `placement.py`
description just below, originally called the baseline's placement
mechanism "wake-affine." That's wrong -- see the 2026-09-26t entry
further down for the full explanation. Every task in this simulator
arrives via the FORK path (`hierarchical_new_task_placement()`), never
wake-affine (`wake_affine_idle()`/`wake_affine_weight()`/
`select_idle_sibling()`), which exists in `placement.py` but is
structurally unreachable code in every run this project has ever done.

Every mechanism here is a direct analog of something we traced in real
`fair.c` / `topology.c` / `pelt.c` source during the background research
phase. Where a simplification was made, it's stated explicitly in that
file's module docstring — search for "Simplification" in each file.

## Files

- `task.py` — vruntime/weight, matches `sched_entity` fields we used.
- `topology.py` — 3-level domain hierarchy (pair/group/machine), with
  the real `imbalance_pct` values from `sd_init()` (110 at pair/SMT
  level, 117 elsewhere). **Correction (2026-09-29): stale on two counts.**
  The real file is `Topology.py` (capitalized, matching every other
  module). The hierarchy has actually always been FOUR levels, not
  three, and there is no flat "group" level: pair → node → onehop →
  machine (`Domain.LEVEL_PAIR`/`LEVEL_NODE`/`LEVEL_ONEHOP`/
  `LEVEL_MACHINE`), built PER NODE on a ring adjacency (`build_topology()`'s
  own module docstring — the Scheduling-Group-Construction-bug fix).
  The `imbalance_pct` values (110/117) are still correct as stated.
- `core.py` — preemptive rbtree-lite execution + PELT-accurate decay
  law (`y^32 ≈ 0.5`, verified from `pelt.c`). Uses a FIXED time slice
  rather than EEVDF's dynamic slice — noted as a simplification.
  **Correction (2026-09-29): "rbtree-lite" is no longer accurate.** The
  real file is `Core.py`; as of 2026-09-24, `Core.rq` is a
  `TrackedRunQueue` backed by `EevdfTree.py` — a real augmented rbtree
  (deadline-keyed, `min_vruntime`-augmented, genuine rotations),
  verified against 16,000+ brute-force-checked picks, not a
  simplified stand-in. The fixed-time-slice simplification is still
  accurate as stated.
- `placement.py` — `wake_affine_idle()` / `wake_affine_weight()` /
  `select_idle_sibling()`, implementing the exact cross-multiplied,
  capacity-normalized, imbalance_pct-biased formula we read from
  `fair.c` line ~8380. **Correction (2026-09-26t): dead code in this
  simulator** -- only reached from `select_core_for_task()`'s
  `task.prev_core is not None` branch, and no task in this simulator
  ever has a `prev_core` set at placement time (every task is generated
  fresh with `prev_core=None` and placed exactly once, via the FORK path
  -- `hierarchical_new_task_placement()` -- immediately below). See the
  2026-09-26t ledger entry for what this does and doesn't change.
- `load_balancer.py` — `should_we_balance()`-style election,
  `group_classify()`-lite (3-state, real code has ~7), and
  `calculate_imbalance()`'s verified `min()` trick for the
  overloaded/overloaded case, plus the `>>=1` halving for the
  has-spare case. Also implements `sched_balance_newidle()`'s
  cost/probability-gated reactive pulling.
- `burst_scheduler.py` — **the actual research contribution.** Detects
  N arrivals landing on one pair-level domain within a time window,
  and immediately walks the SAME bottom-up domain chain periodic
  balancing would eventually walk — reusing `_balance_domain()`
  unchanged. This isolates your intervention to exactly one thing:
  _when_ the pipeline fires, not what it does once it fires.
- `workload_generator.py` — **five profiles are PLACEHOLDERS.** We
  never pinned down your paper's actual burst-size/duration/inter-
  arrival parameters in conversation — replace `PROFILES` with your
  real definitions before trusting any result.
- `metrics.py`, `main.py` — wiring and reporting.
- `stress_test_bursty.py` — paired sign-test / p95-wait / migration-count
  diagnostic for the `bursty` profile specifically. Added after
  aggregate-mean comparisons turned out not to be evidence of anything;
  see the "2026-09-26b" update below.

## Known rough edges, found by actually running it

- `bursty_large` currently shows triggers firing (3) with no visible
  metric change — worth investigating: likely `imbalance <= 0` at the
  point the trigger fires, meaning the burst resolves faster than the
  detection window catches it. Try tightening `burst_window` or
  lowering `burst_threshold` for that profile specifically.
- `group_classify()` is collapsed to 3 states; real Linux has ~7
  (misfit/asym_packing/smt_balance/llc_balance/imbalanced/
  fully_busy/overloaded/has_spare). Extend `load_balancer.classify()`
  if your evaluation needs those distinctions.
- Time slice is fixed (`TIME_SLICE = 4` in `core.py`), not
  weight/period-derived like EEVDF's real dynamic slice.
- `waker_core` in `placement.py` is a stand-in (`cores[0]`) since the
  simulator has no separate "waking task" process — real Linux
  distinguishes the waker's CPU from the task's own previous CPU.
- **Migrations were free.** `_do_migrate()` moved a task's rq membership
  and rebased its vruntime but charged no time cost at all — no
  cache/TLB warm-up penalty on the destination core, matching nothing
  in real hardware where a migrated task's first slice(s) after landing
  genuinely run slower. Since burst-aware's entire premise is "migrate
  proactively, more often than periodic balancing would," a model where
  migration is costless structurally flatters it — every extra migration
  is upside with zero downside. Added an opt-in `migration_penalty` (ms
  added to `task.remaining_time` per migration, `LoadBalancer.__init__`,
  default `0.0` so existing results are unaffected) to test whether the
  `bursty` result holds up once migrating has a cost. See the dated
  update below for the outcome. The penalty value itself is a flat,
  unverified stand-in, not a real hardware number — real cache-miss cost
  varies wildly by working-set size and what else is sharing the LLC.

## Running it

```
pip install simpy
python3 main.py
```

---

**Note (added later in our conversation): this is the FIRST version**,
delivered before the full spec (observable-signals-only detector,
5 real named profiles, event logging, burst response latency metric)
was given. It was superseded by v2 and then v3 — the profiles named
here are placeholders, not the real Uniform/Bursty/Heavy-tail/Mixed/
Deadline-driven set. Kept here only because you asked to see the
original file's exact content.

## What's new this revision

- **Fixed a real bug**: the burst path was resetting each domain's
  periodic-balance clock (`last_balance`) even when it migrated
  nothing — this was actively delaying real periodic corrections and
  caused a spurious regression on the `mixed` profile. Now
  `_balance_domain()` returns how many tasks it actually moved, and
  the clock is only reset on genuine action.
- **`experiment.py` (new)** — the Phase 2 harness: 2 schedulers × 5
  profiles × 3 intensities × 10 repetitions, **paired seeds** (same
  seed for both schedulers per repetition, minimizing noise in the
  comparison), writes `results.csv`.
- **`results.csv`** — first real run, included. See findings below.

## Findings from this run — READ BEFORE DRAWING CONCLUSIONS

1. **At low/medium intensity, baseline and burst-aware are identical
   across all 5 profiles.** Current thresholds mean the detector
   never diverges from baseline until intensity is high. Worth
   reporting as evidence of conservative behavior, but it also means
   the intervention isn't yet demonstrating the smooth
   advantage-scales-with-intensity curve RQ3 is looking for.
2. **At high intensity, effect is profile-dependent, and `bursty` —
   the primary experiment profile — gets WORSE**, not better:
   makespan 324.5→333.6, wait 35.95→37.68. `heavy_tail` and
   `deadline_driven` improve; `mixed` is a wash.
3. **Burst-aware shows higher run-to-run variance** than baseline in
   several high-intensity cases (e.g. `bursty` stdev 8.6 vs 15.5 is
   actually reversed there, but check `heavy_tail`/others in the CSV)
   — the intervention appears to make outcomes less predictable, not
   just shift the mean. Worth its own discussion in the write-up.

**Do not tune thresholds/migration logic until #2 is understood.**
Getting `bursty` to "look good" by adjusting parameters without
understanding _why_ it currently regresses would be circular — you'd
be fitting the mechanism to one profile's noise, not fixing a real
flaw. Recommended diagnostic: log which specific task gets migrated on
each burst-triggered action (`_migrate_load` picks the _heaviest_
task by weight — this may be a poor choice for `bursty`'s specific
arrival shape; try instrumenting whether the migrated task's
subsequent placement actually helps or just moves the bottleneck) and
compare against a burst-triggered pass that picks differently.

## Still needed for the full spec

- Diagnosis of the `bursty` regression (above) — priority before
  anything else.
- Sensitivity analysis varying the four intensity parameters
  one-at-a-time (currently only low/medium/high presets exist;
  `intensity_overrides` param in `run_profile()` supports this, just
  needs a sweep script).
- Plotting script (bar/line/heatmap/scatter) reading `results.csv`.
- The IEEE report.

Given today is Aug 28 and Phase 3 (write-up) was slated to start now,
the `bursty` regression is the critical path — a paper claiming
"burst-aware adaptive scheduling improves performance" can't lead with
a dataset where the primary profile regresses.

## Update (2026-09-14) — the burst mechanism wasn't actually doing anything

Root cause of the "no visible metric change despite triggers firing" rough
edge noted above, and a likely contributor to Finding #2: `on_task_placed()`
in `burst_scheduler.py` called `_balance_domain()` with the **arrival core
itself** as `local_core`. `_balance_domain()`'s whole design (shared with
periodic balancing) is "does `local_core` look needy relative to the
domain's busiest group?" — but the arrival core, by definition, is the one
that just became overloaded. It can never look needy relative to anyone,
so every burst-triggered call structurally returned 0.

Three fixes, all in `load_balancer.py` / `burst_scheduler.py`:

1. **Burst path now uses the domain's designated (idle-preferring) checker**
   as `local_core`, via a new `LoadBalancer._find_checker()` (factored out
   of the existing `is_designated_checker()`), instead of the arrival core.
   This makes the burst path ask the right question: "can some idle/less-
   loaded core in this domain relieve the one that just got hit?"
2. **The categorical gate `if b_type <= local_type: return 0` was changed
   to strict `<`.** With only 3 group-type states, `GROUP_OVERLOADED` is
   the max value, so `b_type <= local_type` was *always* true once
   `local_type == GROUP_OVERLOADED` — making the already-written,
   already-verified overloaded-vs-overloaded numeric block just below it
   (the `local_avg`/`busiest_avg`/`imbalance_pct` checks) structurally
   unreachable. Same problem, less obviously, for any other category tie
   (`FULLY_BUSY == FULLY_BUSY`, `HAS_SPARE == HAS_SPARE`): the gate
   returned 0 before the type-specific numeric/idle-count logic ever ran.
   Strict `<` lets ties fall through to whichever block actually
   arbitrates them.
3. **Added the missing `imbalance_pct` margin check to the generic
   `migrate_load`-style path** (previously only the `GROUP_OVERLOADED`
   branch had it). Necessary once ties can reach that path — otherwise a
   `FULLY_BUSY == FULLY_BUSY` tie with a negligible real difference could
   trigger a migration with no conservatism check at all.

Confirmed via direct tracing (not just aggregate metrics) that periodic and
newidle balancing benefit from fix #2/#3 as well, since `_balance_domain()`
is shared code, not burst-specific.

One thing this update does **not** change: at low/medium intensity, burst
and baseline are still identical — but now for an understood, legitimate
reason rather than an open question. `classify()`'s load is PELT-decayed
(`core.load()` / `load_avg`), decoupled from the actual run queue,
and `_migrate_load`/`_migrate_tasks` can only steal from `core.rq`
(queued, not-yet-running tasks) — never the currently-executing task. At
low/medium intensity queues are shallow enough that by the time a burst
trigger fires, there's usually nothing actually queued to move, even when
the decayed-load imbalance is real. This is a property of the workload
at that intensity, not a bug in the gate logic (verified: a case where the
gate *should* block — checker fell back to a core that was itself more
loaded than its "busiest" comparison — correctly computed a negative
imbalance and returned 0, rather than pulling in the wrong direction).

### Re-ran `experiment.py` with the fix — Finding #2 reverses for `bursty`

New `results.csv` (still 2 schedulers × 5 profiles × 3 intensities × 10
paired-seed repetitions). Comparing against the original findings above:

- **`bursty` (the primary profile) no longer regresses.** medium:
  750.0→748.0 ms; high: 197.9→195.3 ms. This was the specific blocker
  Finding #2 called out as critical-path before Phase 3 could start.
- `uniform` and every `low`-intensity row across all profiles: still
  identical baseline vs. burst-aware, as expected (`uniform` never
  triggers bursts; `low` intensity never builds queued backlog).
- `mixed` high: small improvement (263.2→261.7).
- `heavy_tail` high: 183.7→187.1, but stdev is ±42ms on **both** sides —
  inside the noise floor, not a meaningful difference. Its variance is
  large regardless of scheduler and is still unexplained — worth its own
  investigation, separate from the burst mechanism.
- `deadline_driven` high: small regression (195.3→196.8), and its wait
  time moved more (1.59±0.58 → 1.70±0.54 ms) than its makespan did.
- `bursty`'s wait time got slightly worse and noisier alongside its
  improved makespan (1.64±0.58 → 1.76±0.67 ms) — consistent with the
  mechanism migrating tasks earlier (better total completion time) at
  the occasional cost of a task that would have run sooner in place.
  Worth naming as a real tradeoff in the write-up, not glossing over.

**Still open, not yet investigated:** `heavy_tail`'s large run-to-run
variance (±40ms range) independent of scheduler choice, and whether
`deadline_driven`'s small high-intensity regression is noise or real
across more repetitions.

## Update (2026-09-26) — migrations were free; tested whether that flattered burst-aware

Raised directly: burst-aware's whole mechanism is "migrate more, migrate
sooner." If migrating costs nothing in the model, that structurally
favors it. Confirmed `_do_migrate()` (`load_balancer.py`) charged no
time cost at all — no cache/TLB warm-up penalty on the destination
core — before this update. Added `migration_penalty` (ms added to
`task.remaining_time` per migration; default `0.0`, opt-in via
`balancer_kwargs`) and re-ran `bursty` — the profile the paper's
headline claim rests on — at several penalty values instead of just
one, to see whether the effect was fragile or robust:

| penalty | intensity | baseline makespan | burst_aware makespan | delta | rel. |
|---|---|---|---|---|---|
| 0 ms (orig.) | high | 197.9 | 195.3 | -2.6 | -1.3% |
| 2 ms (n=10, full grid) | high | 203.8 | 201.1 | -2.7 | -1.3% |
| 4 ms (n=20) | high | 212.9 | 211.5 | -1.4 | -0.7% |
| 8 ms (n=20) | high | 230.7 | 227.6 | -3.1 | -1.3% |

**RETRACTED, see below.** The table above (n=10 or n=20, no paired
significance test) was read as "the advantage survives, never flipping
sign." That conclusion doesn't survive proper testing -- see the
2026-09-26b correction immediately following.

## Update (2026-09-26b) — the "advantage survives the penalty" claim was wrong

Pushback, correctly: (1) a mean makespan delta with no stdev/paired
test isn't evidence of anything -- 10-20 unpaired-looking samples can
keep the same sign by chance; (2) if burst-aware migrates roughly the
same *amount* as baseline, a migration penalty testing "does the
advantage survive being more expensive" is close to moot; (3) wait
time / p95 wait, not just makespan, is where a burst response
mechanism should show up; (4) needed the actual entry-core mode
confirmed, not assumed.

Reran `bursty` at medium/high, penalty in {0, 2, 4, 8} ms, n=30 per
cell, **paired seeds** (`Experiment.py`'s own scheme: `1000*profile_id
+ 100*intensity_id + rep`), computing a paired sign test (wins/N
excluding ties, exact binomial p) instead of just an unpaired mean:

| intensity | penalty | baseline makespan | burst_aware | mean Δ | wins/N (ties) | sign-test p |
|---|---|---|---|---|---|---|
| high | 0ms | 196.80±4.57 | 197.40±4.61 | +0.60 | 9/24 (6) | 0.31 |
| high | 2ms | 204.27±5.01 | 203.20±6.23 | -1.07 | 16/28 (2) | 0.57 |
| high | 4ms | 211.10±7.63 | 212.33±7.01 | +1.23 | 11/28 (2) | 0.34 |
| high | 8ms | 229.80±8.28 | 231.33±6.30 | +1.53 | 12/25 (5) | 1.00 |
| medium | 0/2/4/8ms | (all) | (all) | ~0 | ~5-6/11-14 | 0.42-1.00 |

**None of this is statistically distinguishable from zero, at any
penalty, at either intensity.** The sign of the mean delta flips
between penalty levels (favors burst-aware only at 2ms; favors
baseline at 0/4/8ms) with p-values nowhere near significant -- exactly
what pure noise centered on zero looks like, not "a small real effect."
The earlier table's "0-8ms, never flipping sign" claim was an artifact
of looking at 1-2 point estimates without a paired test; it does flip,
once you look properly.

**Wait time and p95 wait make the picture worse for burst-aware, not
better:** at every penalty level, burst-aware's avg_wait and p95_wait
are equal or *higher* than baseline's (high/0ms: avg_wait +0.09ms,
p95 +0.20ms; high/8ms: avg_wait +0.61ms, p95 +1.83ms). If the burst
mechanism were doing something real, this is the metric it should
improve -- it doesn't.

**Migration counts explain why the penalty barely moved anything:**
burst-aware migrates only ~2-5% more tasks than baseline across all
penalty levels (ratio 1.02-1.05, both intensities). There was never
much extra migration volume for a penalty to tax, so "the effect
survives a migration penalty" was never a strong test to begin with --
there's very little for the penalty to act on.

**Entry-core mode, confirmed from code (not assumed):** `bursty` uses
`_emit_burst()`'s shared-per-burst random entry core
(`WorkloadGenerator.py`: `burst_entry_core = random.choice(self.cores)`),
not a fixed `cores[0]` and not independent-per-task random. So this
isn't the core-0 pile-up artifact from before the 2026-09-24 fix
resurfacing -- the effect is genuinely absent under proper testing,
independent of that question.

**Where this leaves the paper's claim:** at `bursty`/high -- the
profile and intensity the headline claim rests on -- burst-aware
shows no measurable makespan advantage over baseline once tested
with paired seeds and a significance test, migrates only marginally
more, and trends slightly *worse* on wait/p95 wait. This does not
support "burst-aware adaptive scheduling improves performance" as
currently configured (these thresholds, this profile). Before
concluding the mechanism doesn't work at all: (a) try other
profiles/intensities with the same rigor -- `heavy_tail` and
`deadline_driven` were never retested this way either; (b) check
whether `BurstDetector`'s thresholds are simply too conservative to
fire meaningfully more than periodic balancing would anyway on this
profile (worth logging *when* each scheduler's migrations happen
relative to burst start, not just how many); (c) increase n further
(30 is a floor, not a strong sample) before ruling it out entirely.
Do not re-tune thresholds to manufacture a win on `bursty` specifically
without understanding why the current mechanism doesn't separate from
baseline -- same reasoning as the "don't tune until #2 is understood"
guidance above, now doubly applicable.

## Correction (2026-09-26t) — "wake-affine baseline" was the wrong name

Every task in this simulator is generated fresh with `prev_core=None`
and placed exactly once, via `select_core_for_task()`'s FORK path
(`hierarchical_new_task_placement()`, matching real Linux's
`wake_up_new_task()` -> `select_task_rq_fair()` for `WF_FORK`/`WF_EXEC`).
The wake-affine functions (`wake_affine_idle()`, `wake_affine_weight()`,
`select_idle_sibling()`) exist in `placement.py` and are faithful to what
was traced from `fair.c`, but they only run from `select_core_for_task()`
's `else` branch (`task.prev_core is not None`) -- a branch NO task in
this simulator's history has ever reached, because there is no "task
wakes up again after running before" event modeled, only initial
arrival. **Confirmed empirically**, not just by code reading: patched
`Placement.wake_affine`/`hierarchical_new_task_placement` to count calls
on a real `bursty`/high run -- `wake_affine: 0`, fork-path: `200` (every
task). Fixed the title and file-list wording throughout this document
and the module docstrings that called it "wake-affine placement." This
changes no code and no prior numeric result -- it's a correction to how
the mechanism was DESCRIBED, not to what it does. Worth deciding later
whether the "task wakes up" case is worth modeling at all, since as
written it's dead code that could be removed instead.

## Task 6 prep, item 1 (2026-09-26u) — idle-core balancing confirmed, but the drain trace found something bigger

**Both literal checks confirmed**, via a live trace on a real run (not
just code reading): (1) `Main.py`'s `periodic_ticker` calls
`periodic_balance()` for every core, idle or busy, every 1ms tick, with
no gating on idleness. (2) `LoadBalancer._find_checker()` prioritizes
any idle sibling over the min-core_id fallback -- on `stacked_burst`
/high (seed 5200), the elected checker for core 0's pair-domain was
IDLE on **479/479 (100%) of sampled ticks**. Both hold, so: **this does
functionally approximate real Linux's nohz idle load balancing** in the
specific sense that idle capacity is preferentially used to perform
balancing checks -- though not in mechanism (real nohz suppresses the
periodic tick on idle CPUs and uses an explicit "ilb" kick from a busy
CPU to a chosen idle one; this simulator ticks every core every 1ms
unconditionally and elects a checker from whoever's available, which
produces a similar idle-does-the-checking outcome via a different route).

**The trace of the actual drain (baseline, first burst, seed 5200,
transition mode) found something more consequential than the checklist
item.** Core 30 gets stacked with 30 tasks, its queue peaking at 29
still-queued by t=23. **Only 2 periodic migrations ever touch core 30 in
this whole run** (t=24, node level; t=32, machine level) -- then
periodic balancing goes quiet near this core for the rest of the drain.
**The remaining 27 tasks drain one at a time via `newidle`**, roughly
every 4-40 ticks, taking until **t=461** (438ms after the burst ended at
t=22.7 -- a burst that took under 8ms to arrive takes nearly 450ms to
fully drain from this one core). `_newidle_attempt()` only ever migrates
ONE task per successful pull (`task = src.rq[0]`, single `_do_migrate`
call, no loop), and under `transition` mode a destination core gets
exactly one attempt per idle spell -- so the drain rate is capped at
roughly "one task per time some neighboring core happens to go idle,"
which is slow relative to the size of the pileup.

**Not yet explained: why periodic balancing stops correcting core 30
specifically after t=32,** despite running unconditionally every tick
with (by t=32) still ~28 tasks visibly queued there. Possibilities not
yet checked: PELT-decayed `core.load()` (what `classify_group()` actually
compares, not raw queue length) may stop reading core 30's domain as
comparatively overloaded once its OWN load average saturates near the
same ceiling as a busy neighbor; or `nr_balance_failed`'s relaxation
logic; or an `imbalance_pct` conservatism gate. **This is very likely
part of why Task 5's effect size is as large as it is** -- baseline's
two "automatic" recovery paths are both far weaker in practice here than
their unconditional-every-tick/idle-prioritized design would suggest,
leaving a lot of room for burst-aware's dedicated mechanism to help.
Flagged as an open question, not chased down further here per "stop and
report" -- worth a dedicated root-cause pass if this is pursued, using
the same monkey-patch-and-trace technique (`task6prep_idle_balance_
trace.py`) rather than modifying `LoadBalancer.py` itself.

## Task 6 prep, item 1 follow-up (2026-09-27) — load-signal diagnosis: two separate bugs found

Live snapshot at t=40 (stacked_burst/high, seed 5200, baseline):

| | core 30 | core 24 |
|---|---|---|
| `load()` | 465.66 | 299.92 |
| `running_count()` | 28 | 1 |
| `len(rq)` | 27 | 0 |
| pair `classify_group` | **OVERLOADED** | HAS_SPARE |
| node `classify_group` | FULLY_BUSY | FULLY_BUSY |

**(a) CONFIRMED:** `Core.tick_load()`'s `instant = 1.0 if current_task
else 0.0` is a pure running/not-running binary -- blind to `rq` and task
weights. `load()` caps near `NICE_0_WEIGHT=1024` regardless of queue
depth.

**(b) NOT confirmed -- correcting the suspicion rather than rubber-
stamping it:** `running_count() = len(self.rq) + (1 if current_task
else 0)` already includes the queue.

**(c) Nuanced:** a single overloaded core's PAIR-level group CAN
classify `OVERLOADED` (confirmed live, because the type gate's
`running > len(cores)` term already sees the queue via (b)). But at
NODE level the SAME backlog classifies only `FULLY_BUSY`: the gate is
`load_ratio > 100 AND running > len(cores)` -- `running(29) > 8` holds,
but `load_ratio = load*1024/8192 = 95.7`, just under 100, because the
queue-blind `load()` component dilutes across more cores' capacity.
**Queue-blindness doesn't block overload detection at small aggregation,
but does suppress it at larger ones** -- exactly where cross-node
correction matters most.

**(d) Answered, but the dominant cause is a SEPARATE bug from the load
signal:** traced `is_designated_checker()`/`_balance_domain()` directly
(`task6_why_periodic_stalls.py`). For core 30's pair (n3-pair3),
`is_designated_checker` was asked **178 times, always with `asked_core=
30` -- never once with core 31**, the actual idle checker.
`_balance_domain` succeeded for this pair **zero times in the entire
run**. Root cause in `LoadBalancer.periodic_balance()`:
`d.last_balance = now` was written unconditionally once the interval
elapsed, BEFORE checking `is_designated_checker` -- so whichever core's
climb reaches the (shared) `Domain` object first each cycle permanently
steals that domain's balancing turn from the actual checker. Since cores
are visited in a fixed order every tick, the lowest-`core_id` member of
any domain always wins the race. This is the DOMINANT explanation for
item 1's "why does periodic stop correcting core 30" finding -- not the
load signal.

## Fix A + Fix B (2026-09-27) -- implemented, verified, decided as the final baseline BEFORE seeing paired results

Both opt-in (default off, every existing result stays valid), fixing the
two DISTINCT bugs found above.

**FIX A -- `per_cpu_last_balance=True`** (`LoadBalancer.__init__`,
forwarded through `BurstAwareLoadBalancer`): real Linux keeps a per-CPU
COPY of each `sched_domain` (`for_each_domain(cpu, sd)` walks a per-cpu
allocated hierarchy in `sched_balance_domains()`; `should_we_balance()`
gates whether THIS cpu proceeds, but each cpu's own `sd->last_balance`/
`sd->balance_interval` are private to its own copy -- a non-designated
CPU's bail never touches the elected CPU's timer). Implemented via
`LoadBalancer._balance_state(core, domain)`: a lazily-created
`{(core_id, id(domain)): {"last_balance", "balance_interval"}}` dict,
used in place of the shared `Domain.last_balance`/`.balance_interval`
fields when the flag is on. `periodic_balance()` rewritten to branch on
the flag (legacy path reads/writes `Domain` fields exactly as before,
byte-for-byte); `BurstAwareLoadBalancer.on_task_placed()`'s post-burst-
migration `domain.last_balance = now` updates the SAME per-(checker,
domain) entry when the flag is on, per the request, rather than reverting
to the shared field.

**FIX B -- `load_model="runnable"`** (`Core.__init__`/`tick_load()`,
threaded through `build_node()`/`build_topology()`/`Main.run_simulation()`
as a new `load_model` param): PELT's instantaneous signal sums the
WEIGHT of every runnable task -- `current_task` plus everything still in
`rq` -- instead of a bare 0/`NICE_0_WEIGHT` running indicator. Matches
real Linux's `cfs_rq->avg.load_avg` (PELT-decayed, accumulated over
`cfs_rq`'s runnable population via `runnable_avg`, not just `curr`).
`running_count()` unchanged (already correct, per (b) above).

**Verification (stacked_burst/high, seed 5200, baseline, drain trace for
all 4 configs)** -- table above. **Fix A is the dominant effect** (drain
438ms->181ms, periodic goes from 2 successful migrations all run to
doing nearly all the work); **Fix B alone does almost nothing** (438ms->
434ms) because without Fix A, periodic still barely runs regardless of
what load signal it would act on; **together, periodic handles the
entire drain with zero newidle needed** (181ms->174ms, marginal further
gain from B once A has unblocked periodic).

**Final baseline decided now, before any paired result is seen (per
explicit instruction): both fixes ON (`per_cpu_last_balance=True`,
`load_model="runnable"`) for BOTH schedulers**, for every experiment from
here on. All earlier results in this document -- specifically Task 5's
`stacked_burst` "hypothesis CONFIRMED" table (2026-09-26s), and the
`bursty`/`heavy_tail` regression checks -- were measured against a
baseline with BOTH the shared-`last_balance` checker-stealing bug and
the queue-blind load signal present, and **are superseded, pending
re-run with the fix.** Given Fix A alone recovered most of core 30's
drain speed in the single-seed check above, Task 5's dramatic effect
size is now a real open question: it may have been measuring "burst-
aware compensates for broken periodic balancing" rather than a genuine
burst-awareness benefit -- the same shape of confound as the RNG bug
(Fix 3d) and the newidle-gate bug (Task 3b), found a third time. The
re-run (next entry) is what actually answers this, not this paragraph.

## Kernel reference rule (2026-09-27), applies going forward

Fetched kernel source directly rather than relying on memory, per
explicit instruction. **Pinned version: Linux v7.2** (`torvalds/linux`,
tag `v7.2`, fetched via
`raw.githubusercontent.com/torvalds/linux/v7.2/kernel/sched/fair.c`).
Note: the instruction anticipated "latest stable 6.x" -- by this
session's date (2026-09-27) mainline has progressed past the 6.x series
to 7.x; `v7.2` is the actual latest stable tag (confirmed via GitHub's
tag list, which also showed `v7.3-rc1..rc4` as the current -rc series).
Elixir's web UI couldn't be fetched directly (its source view is
client-side-rendered; static fetch only returns the navigation shell),
so the raw file was pulled directly and searched locally for exact line
numbers instead of trusting a summarized re-rendering.

## Task 6, Step 1 (2026-09-27) -- kernel comparison: TWO missing/wrong branches confirmed

**Live trace, A+B on, stacked_burst/high seed 5200, baseline**
(`task6_kernel_compare_diagnose.py`, wraps `_balance_domain` to log its
own internal `classify_group()`/branch-selection state alongside the
real call -- no production code touched): of 84 effective periodic
passes, the first 5 all show `local_type=HAS_SPARE`, `busiest_type=
OVERLOADED` (core 30's neighbors are idle, core 30 itself is
overloaded) -- and every one takes the simulator's **generic
`_migrate_load` min()-trick branch**, computing a small imbalance
(645-1125, in load-average units) that a single task's weight (often
1024+) exhausts after 1-2 migrations. Confirmed directly (not inferred):
the loop stops because **imbalance is reached**, not `loop_max`, not the
anti-livelock break, not the `min_task_weight` skip.

**Comparison against the fetched source (`calculate_imbalance()`,
`kernel/sched/fair.c:12577-12754`; `group_classify()`, line 11528;
`group_is_overloaded()`/`group_has_capacity()`, lines 11478-11525;
`sibling_imbalance()`, lines 11644-11677; `detach_tasks()`'s per-
migration-type cost switch, lines 10941-10992; `loop_max` derivation,
line 13341, `= min(sysctl_sched_nr_migrate, busiest->nr_running)`):**

When `local->group_type == group_has_spare` (`fair.c:12642`), real Linux
branches on `busiest`'s type, not on a single unconditional formula:

1. **`busiest overloaded AND domain doesn't share LLC` (`!(env->sd->
   flags & SD_SHARE_LLC)`, `fair.c:12643-12644`) -> `migrate_util`**:
   size the move to fill LOCAL's spare capacity
   (`imbalance = max(local->group_capacity, local->group_util) -
   local->group_util`, `fair.c:12654-12655`), and in `detach_tasks()`
   each candidate task costs `task_util_est(p)` (`fair.c:10969`, PELT
   per-task ESTIMATED UTILIZATION -- near-zero for a task that has
   never run yet) rather than its static weight. **This is the branch
   the trace above actually hits (busiest overloaded, local has spare)
   and the simulator does NOT implement it at all** -- it falls through
   to the generic min()-trick instead, which is the WRONG formula for
   this type combination (that formula's real use is
   overloaded-vs-overloaded, per this file's own module docstring).
2. **`busiest->group_weight == 1 OR prefer_sibling` -> `migrate_task`
   sized by `sibling_imbalance()`** (`fair.c:12672-12678`, `11644-11677`):
   `imbalance = busiest->sum_nr_running - local->sum_nr_running`
   (clamped >= 0), NOT an idle-core-count difference. At this
   simulator's PAIR level, every "group" IS a single core
   (`group_weight == 1` always), so real Linux would ALWAYS take this
   branch there, not the idle-diff one -- for pass 1 above (pair level,
   busiest_run=19, local_run=0), that's `imbalance=19`, halved to `~9`,
   vastly more than what either the simulator's current idle-diff
   formula OR its (wrongly-taken) min()-trick branch computes.
3. **Otherwise (busiest fully_busy/has_spare, no sibling preference)
   -> `migrate_task` sized by idle-core-count diff**
   (`fair.c:12681-12687`): `max(0, local->idle_cpus - busiest->idle_cpus)`
   -- **this is the ONE branch the simulator already implements**
   (`_balance_domain`'s `local_type==HAS_SPARE and b_type!=GROUP_
   OVERLOADED` guard, matching `_migrate_tasks`), confirmed correct as
   far as it goes.
4. All three of the above get the SAME final treatment real Linux
   applies uniformly: an `adjust_numa_imbalance()` call when `SD_NUMA`
   (`fair.c:12690-12697`) -- the simulator only calls its own
   `_adjust_numa_imbalance()` in the generic (non-has-spare) path, never
   inside `_migrate_tasks`'s has-spare branch -- followed by `>>= 1`
   (`fair.c:12700`), which the simulator's has-spare branch DOES already
   apply (`// 2`), just without the NUMA step first.

**Discrepancy from the understanding given in the prompt, flagged as
instructed:** the stated understanding ("busiest overloaded ->
migrate_util") is directionally right but **omits the `!SD_SHARE_LLC`
gate** (`fair.c:12644`) -- migrate_util fires only when the domain does
NOT share an LLC. Real hardware (and presumably this simulator's own
topology, though it has no `SD_SHARE_LLC`-equivalent flag on `Domain` at
all right now) typically shares LLC at the pair/SMT and often node/
socket level, and does NOT share it once crossing NUMA nodes --
suggesting migrate_util's real applicability in THIS topology would be
at the `onehop`/`machine` (`is_numa=True`) levels specifically, not at
`pair` (which should instead take branch 2, `sibling_imbalance`, since
`group_weight==1` there). The trace's pair-level pass (pass 1) and
onehop-level passes (2/3/5) are therefore both wrong, but for two
DIFFERENT reasons: pair should be `sibling_imbalance` (branch 2, no LLC
condition needed since branch 2 doesn't care about LLC-sharing at all),
onehop should be `migrate_util` (branch 1, IF onehop is confirmed to not
share LLC in this topology -- an explicit design question for Step 2,
since no `SD_SHARE_LLC` concept exists in `Domain` yet).

**Also confirmed, not previously verified against source:** the queue-
blind PELT signal issue from item 1 maps onto real Linux's `group_util`/
`group_runnable` split (`group_has_capacity()`/`group_is_overloaded()`,
`fair.c:11478-11525`) -- real Linux tracks RUNNING-only utilization
(`group_util`) separately from RUNNABLE (running+queued, `group_runnable`)
and TASK COUNT (`sum_nr_running`) as three genuinely distinct signals,
where this simulator's Fix B collapses running+queued into one `load()`
number. Not chased further here -- out of scope for Step 1's specific
branch comparison, noted for completeness.

**Summary of what's missing/wrong, for Step 2:** (1) no `migrate_util`
branch at all (the one actually triggered in the observed trace); (2) no
`sibling_imbalance`/`group_weight==1` branch (the one that SHOULD have
fired for the pair-level pass instead); (3) no NUMA adjustment inside
the has-spare branch; (4) no `SD_SHARE_LLC`-equivalent concept on
`Domain` to decide which of (1)/(2) applies at which level. Stopping
here per instruction -- Step 2 (Fix C) needs a design decision on (4)
before it can be implemented faithfully, flagged for the next turn
rather than guessed at now.

## Task 6, Step 2 (2026-09-27) -- Fix C implemented and verified

**`SD_SHARE_LLC`/`SD_PREFER_SIBLING` mapping confirmed against source**
(`kernel/sched/topology.c`, v7.2, `sd_init()` ~1933-2020,
`default_topology[]` ~2089-2103): bottom-up, `SDTL_INIT(tl_smt_mask,
cpu_smt_flags, SMT)` -> `cpu_smt_flags()` returns `SD_SHARE_CPUCAPACITY
| SD_SHARE_LLC` (line 2032-2035); `SDTL_INIT(tl_mc_mask, cpu_core_flags,
MC)` -> `cpu_core_flags()` returns `SD_SHARE_LLC` only (2056-2059);
`SDTL_INIT(tl_pkg_mask, NULL, PKG)` -> no special flags (2081-2084); and
NUMA-distance levels beyond PKG explicitly strip `SD_PREFER_SIBLING`
from their OWN flags (`sd->flags &= ~SD_PREFER_SIBLING`, ~2013).
**Confirmed exactly as proposed: pair (SMT) and node (MC/one-LLC-
socket) get `share_llc=True`; onehop/machine (NUMA-crossing) get
`share_llc=False`.** No discrepancy to flag this time.

`prefer_sibling`'s real derivation (`fair.c:12551-12552`) reads
`sds->busiest->flags & SD_PREFER_SIBLING`, where "the flags of a sched
group are those of the child domain" -- i.e. it asks whether the
BUSIEST GROUP's own child-domain level carries the bit, not whether the
CURRENT balancing level does. Since nothing except the NUMA-strip above
ever clears it, and this simulator has no separate sched_group-vs-
child-domain object to look the bit up on more literally, implemented as
`prefer_sibling = not busiest.is_numa` -- true whenever the busiest
group being evaluated isn't itself a NUMA-level domain, matching every
level in this topology's actual flag state. Documented as an
interpretation (no separate group/domain distinction exists to check it
more precisely), not a second verified fact like `share_llc`.

**`post_init_entity_util_avg()` fetched and ported** (`fair.c:1315-1351`):
a brand-new task's util_avg is NOT zero -- `cap = (cpu_scale -
cfs_rq->avg.util_avg) / 2`; if the destination core already has nonzero
util, `util_avg = min(core_util * task_weight / (core_load + 1), cap)`;
if the core is truly empty, `util_avg = cap` (half of full capacity)
directly. Ported verbatim (module-scale constant substitutions noted
inline) as `Core._initial_util_avg()`, called from `Core.enqueue()` --
which, in this simulator's actual call graph, only ever runs once per
task (migrations/preemption-requeues append to `rq` directly, bypassing
it), matching real Linux calling `post_init_entity_util_avg()` only once,
at fork/exec. Added `Task.util_avg` and a SEPARATE `Core.util_avg`
(running-only PELT signal, always tracked, decayed in `tick_load()`
alongside but independent from Fix B's runnable `load_avg` -- real
Linux tracks `cfs_rq->avg.util_avg` and `.load_avg`/`.runnable_avg` as
genuinely distinct sums for exactly this reason).

**`sysctl_sched_nr_migrate` confirmed** (`kernel/sched/core.c:191`:
`= SCHED_NR_MIGRATE_BREAK`; `sched.h:3100-3107`: `32` outside
`CONFIG_PREEMPT_RT`) -- `MAX_MIGRATE_PER_PASS=32` was already right,
now verified rather than assumed.

**Fix C -- `imbalance_model="kernel"`** (`LoadBalancer.__init__`,
forwarded through `BurstAwareLoadBalancer`; default `"legacy"`, every
existing result stays valid): `_balance_has_spare_kernel()` implements
`calculate_imbalance()`'s real 3-way branch for `local_type==group_
has_spare` (`fair.c:12642-12703`) -- `_migrate_util()` (busiest
overloaded AND `not domain.share_llc`) sized in `util_avg` units with
per-task cost = that task's own `util_avg`; `sibling_imbalance`-style
`busiest_run - local_run` (busiest `group_weight==1` -- always true at
this sim's pair level -- or `prefer_sibling`), including the "empty
sched group" nudge (`fair.c:11671-11674`); idle-cpu-count diff
otherwise (the one branch `"legacy"` already had) -- all three followed
by the same NUMA adjustment + `>>=1` halving real Linux applies
uniformly. Documented simplification: `_migrate_util` stops at the
first candidate whose cost would exceed the remaining budget rather
than skipping it and trying another (`detach_tasks()`, `fair.c:10968-
10975`) -- immaterial for the scenarios this was built against, since
every candidate there is a never-run task with near-zero `util_avg`
anyway.

**Verification: A+B vs A+B+C, stacked_burst/high, seed 5200, baseline**
(`task6_verify_fixC.py`):

| config | drain time | vs burst end | migrations off core 30 | mean tasks/pass | max tasks/pass | makespan |
|---|---|---|---|---|---|---|
| A+B | t=197 | +174ms | 29 (29 periodic, 0 newidle) | 1.61 | 7 | 220 |
| A+B+C | t=179 | +156ms | 30 (30 periodic, 0 newidle) | **3.37** | **28** | 213 |

**Fix C gives a further real, smaller improvement on top of A+B**
(drain -18ms, ~10%; makespan -7) by moving BIGGER batches per pass
(mean tasks/pass roughly doubles, one pass moves 28 tasks at once) --
exactly the predicted effect of migrate_util's near-zero per-task cost
for never-run queued tasks. A+B remains the dominant fix; C is a real
but secondary refinement, consistent with the same pattern Step 1's
verification already showed for A vs B.

**Final baseline, decided now per instruction: A+B+C ON for both
schedulers**, every experiment from here on.

## Task 6, max-pass check (2026-09-27) -- confirmed as real kernel behavior, not a gap

Traced the 3 largest `_balance_domain` passes under A+B+C on
`stacked_burst`/high seed 5200, baseline (`task6_check_max_pass.py`).
The top 2 show the SAME batch bouncing between cores: t=24, `migrate_util`,
core30->core0, 28 tasks (dst queue=28 right after); t=72, `migrate_util`,
core0->core24, 25 tasks. **A single pass does relocate the whole pile
onto ONE other core, rather than spreading it.**

**Checked against the source rather than assumed wrong:** `sgs->
group_capacity = group->sgc->capacity` (`fair.c:11877`, `12189`) --
`migrate_util`'s `local->group_capacity`/`local->group_util`
(`fair.c:12654-12655`) are GROUP-WIDE aggregates (summed across every
CPU in the local group via `sgc`, built by `update_group_capacity()`),
not per-destination-CPU values. `env->dst_cpu = this_cpu`
(`fair.c:13283`, the `lb_env` initializer in `sched_balance_rq()`) --
the one CPU actually running this specific pass. `detach_tasks()`
(`fair.c:10895-11014`) has no per-CPU distribution logic at all; every
detached task goes to the same `env->dst_rq`. **Real Linux genuinely
allows (and by this design, effectively produces) one CPU absorbing an
entire group-wide spare-capacity budget in a single pass -- there is no
missing guard.** The "pile relocates" pattern is the same iterative
machinery correctly re-triggering on whichever core is now busiest
(core 0, once it received the first batch) -- expected multi-pass
behavior of an algorithm that doesn't achieve global balance in one
shot, not a bug. **No fix applied.**

## Machine-wide drain metric (2026-09-27)

Added `diagnostics.machine_wide_queue_sampler`/`machine_wide_drain_times`:
per ground-truth burst, time from its start until `max(len(rq))` across
ALL cores recovers to `<=1` (spike-detection-based -- see the function's
own docstring for why a naive `t>=start` boundary check falsely reads
"0.0ms recovery" on the very first burst, a same-instant sampler/arrival
race). A per-core-only drain trace (Task 6 prep) can't see this: once
Fix C's `migrate_util` can relocate a whole pile onto a DIFFERENT core
in one pass, "core 30 emptied" no longer means the machine has actually
caught up.

**stacked_burst/high, seed 5200, baseline, all 7 bursts:**

| burst # | A+B drain (ms) | A+B+C drain (ms) |
|---|---|---|
| 1 | 169.0 | 158.0 |
| 2 | 146.3 | 135.3 |
| 3 | 123.5 | 112.5 |
| 4 | 100.8 | 89.8 |
| 5 | 78.1 | 67.1 |
| 6 | 55.3 | 44.3 |
| 7 | 32.6 | 21.6 |

**A+B+C drains faster machine-wide on every single one of the 7
bursts** (consistently ~11ms faster each time) -- confirms Fix C's
benefit holds up when measured across the WHOLE machine, not just the
originally-stacked core, despite the single-core view showing the pile
bouncing between cores rather than dissolving directly.

## Task 6, Step 3 (2026-09-27) -- FULL RE-RUN, A+B+C on for both schedulers

**Every earlier result in this document that predates this entry --
Task 5's original `stacked_burst` table (2026-09-26s), all `bursty`/
`heavy_tail` checks, `results.csv`, `results_migration_penalty2ms.csv`,
`results_task5_stacked_burst.csv` -- was measured against an older,
less faithful baseline (missing some combination of Fix 3d/newidle-
transition/A/B/C) and is SUPERSEDED by the numbers below.** n=30
paired seeds, `transition` mode, `per_cpu_last_balance=True`,
`load_model="runnable"`, `imbalance_model="kernel"` for BOTH schedulers,
workload-equality asserted on every call (`task6_step3_full_rerun.py`,
`results_task6_step3_full_rerun.csv`).

### Primary metric (p95_wait) and cost-vs-benefit, by case

| case | penalty | p95_wait baseline | p95_wait burst_aware | % change | sign p | Wilcoxon p | extra scan work | extra migrations |
|---|---|---|---|---|---|---|---|---|
| stacked_burst/low | 0 | 7.40 | 8.50 | **+14.9%** (worse) | <0.0001 | <0.0001 | +0.9% | -0.9% |
| stacked_burst/low | 2 | 7.80 | 8.80 | **+12.8%** (worse) | <0.0001 | <0.0001 | +1.1% | -2.0% |
| stacked_burst/medium | 0 | 14.48 | 12.16 | -16.0% (better) | <0.0001 | <0.0001 | +3.4% | -4.2% |
| stacked_burst/medium | 2 | 14.30 | 11.86 | -17.1% (better) | <0.0001 | <0.0001 | +2.6% | -2.3% |
| stacked_burst/high | 0 | 21.72 | 16.51 | -24.0% (better) | 0.0003 | 0.0002 | +5.9% | -3.2% |
| stacked_burst/high | 2 | 33.96 | 28.39 | -16.4% (better) | 0.0014 | 0.0004 | +3.5% | -7.4% |
| bursty/high | 0 | 4.40 | 4.30 | -2.1% (n.s.) | 0.36 | 0.22 | +2.4% | +5.0% |
| bursty/high | 2 | 5.72 | 5.44 | -4.9% (n.s.) | 0.58 | 0.18 | +3.0% | +5.2% |
| heavy_tail/high | 0 | 0.933 | 0.933 | ~0% (but see below) | 0.0002 | 0.0009 | +2.0% | +5.2% |
| heavy_tail/high | 2 | 0.933 | 0.933 | ~0% (but see below) | 0.0002 | 0.0007 | +2.0% | +5.0% |

**`stacked_burst`'s hypothesis is no longer uniformly confirmed --
it REVERSES at low intensity and holds, but much more weakly, at
medium/high.** This is the headline change from the original (now-
superseded) Task 5 result:

- **Low intensity: burst-aware is now significantly WORSE**
  (p95_wait +12.8% to +14.9%, p<0.0001 both tests, both penalties) --
  the complete opposite of the original broken-baseline result, which
  showed burst-aware winning at low intensity too. Once baseline's
  periodic balancing actually works (A+B+C), it's already good enough
  at low intensity that burst-aware's extra/redirected migrations make
  things marginally worse, not better.
- **Medium intensity: still confirmed, roughly the same magnitude**
  (-16% to -17%, p<0.0001 both tests) -- this held up essentially
  unchanged.
- **High intensity: still confirmed, but the effect size shrank a
  lot** -- was ~50% reduction under the broken baseline (2026-09-26s);
  now -16% to -24%. Real, still significant (p<0.001-0.003), just far
  more modest than originally reported.

**`bursty`/high: still no effect, consistent with every prior check**
(p95_wait p=0.36-0.58, all six metrics non-significant). A+B+C changed
nothing here.

**`heavy_tail`/high: still no makespan/makespan_excess effect
whatsoever** (mean_diff exactly 0.000, wins=0/0 -- every single one of
30 seeds is an EXACT tie on both), **but a tiny, consistently real wait-
time cost survives**: avg_wait +0.003-0.004ms (p=0.0001-0.0007),
p95_wait's rounded 0.000% mean change hides a real Wilcoxon-significant
effect (p=0.0007-0.0009, wins=0/13 non-tied pairs -- ALL 13 non-tied
cases go the same direction, just by an amount too small to move the
3-decimal display). Exactly the same story as the pre-A+B+C finding:
burst-aware's extra migrations never touch the one task that determines
makespan on this profile, but do add a small, real, consistent
overhead elsewhere. A+B+C changed nothing here either.

### RQ4: is the extra work worth it?

Cost pattern is now genuinely informative, not just "burst-aware always
costs a bit more": on `stacked_burst` at medium/high (where it wins),
burst-aware scans MORE cores (+2.6% to +5.9%) but does FEWER total
migrations (-2.3% to -7.4%) than baseline -- it's not spending more
migrations to win, it's making smarter/earlier ones. At `stacked_burst`
low intensity (where it now LOSES), the migration count is similarly
reduced (-0.9% to -2.0%) but the outcome is worse -- the smaller,
redirected migration set is actively counterproductive there. On
`bursty`/`heavy_tail` (no benefit either way), burst-aware both scans
more (+2.0% to +3.0%) AND migrates more (+5.0% to +5.2%) for zero or
negative payoff -- pure overhead with nothing to show for it.
**The extra work is only clearly justified on `stacked_burst` at
medium/high intensity; everywhere else tested, it's either wasted or
actively harmful.**

### Machine-wide drain time

`stacked_burst` (mean ms per burst, across all 30 seeds' bursts):
low 22.0->19.7 (p0) / 21.0->16.2 (p2); medium 19.2->15.1 / 19.2->15.3;
high 63.8->41.4 / 99.5->93.0 -- burst-aware drains the whole machine
faster at every intensity/penalty tested, INCLUDING low intensity where
its p95_wait is worse. This is worth sitting with: burst-aware clears
the machine-wide backlog faster while simultaneously making the
PER-TASK wait experience worse at low intensity -- plausible mechanism,
not yet confirmed: it may be front-loading migrations that help the
aggregate queue depth recover sooner while individually delaying some
tasks more than baseline's slower-but-steadier drain would have. `bursty`/
`heavy_tail` show drain times too close to call (heavy_tail: 0.00ms for
both -- this profile doesn't build the kind of concentrated backlog this
metric is measuring).

### Bottom line after the full fix chain (Fix 3d + Task 3b + A + B + C)

The paper's claim survives only in a narrower form than originally
found: burst-aware adaptive scheduling helps on `stacked_burst`
specifically, at medium-to-high intensity specifically, by a real but
modest margin (16-24% p95_wait reduction, not the ~50% first measured
against a broken baseline) -- and it now measurably HURTS at low
intensity on the very same profile. It shows no effect on `bursty` and
a small consistent wait-time cost with zero makespan benefit on
`heavy_tail`. Every one of these findings required fixing a baseline
defect first (RNG contamination, a self-suppressing newidle gate, a
checker-stealing bug in periodic balancing, a queue-blind load signal,
and a missing kernel imbalance branch) -- underscoring that an
apparently large effect size measured against an unverified baseline
is not evidence of anything until the baseline itself has been checked.

## Task 6 follow-up 1 (2026-09-27) -- why stacked_burst/low reverses, mechanism found

Worst 2 seeds by p95_wait diff (`stacked_burst`/low, penalty=0, A+B+C):
5004 (+3.0ms) and 5027 (+2.0ms). Traced every burst in both
(`task6_low_intensity_reversal.py`) and confirmed the pattern is fully
deterministic, not seed-specific noise: across BOTH seeds' full 50
bursts each (100 bursts, zero variance), the detector fires at
**exactly `t = burst_start + 4ms`, every single time** -- `queue_growth`
crosses its 2.0 threshold on a fixed schedule set entirely by this
profile's own fixed burst timing (`burst_size=4`,
`arrival_rate_during_burst=0.5`), not by adaptively detecting anything
different burst to burst -- and moves **exactly 1 task** (49-50 of 50
triggers) via the burst-tagged path, always onto a core that is
busy-but-not-queued (`rq_len=0, running=True` at that instant) -- the
least-loaded option available, not an obviously bad choice by anything
visible at that moment.

**Concrete mechanism (burst 1, seed 5004, tasks 4-7, arriving t=126,128,
130,132):** burst-aware pulls task 4 at t=130 (burst-tagged). Baseline,
via periodic ALONE, pulls task 4 at the SAME t=130, then task 7 at
t=132, then task 5 at t=134 -- three periodic corrections in quick
succession, unaided. Burst-aware's early pull changes the queue state
periodic reacts to next: it ends up moving task 5 (t=134) then task 6
(t=136) instead of baseline's order, leaving **task 6 to wait 9ms**
(starts t=139) vs baseline's 4ms for the same task -- while task 7,
never migrated at all under burst-aware, finishes with only a 2ms wait.
**The burst path's one mechanical pull reshuffles WHICH task absorbs
the delay, for better or worse essentially by chance, because periodic
balancing was already reacting to this 4-task burst within 2-6ms on its
own** (Fix A/B/C fixed exactly the periodic-balancing weaknesses this
depended on). Averaged over ~50 near-identical tiny bursts per seed,
this reshuffling occasionally produces a worse tail (p95) wait than
letting periodic run unperturbed -- not because burst-aware does
anything obviously wrong (moving tasks onto busy cores, moving tasks
about to run, or a cooldown/timing bug), but because its single fixed
action perturbs an already-fast baseline's own ordering with no
guarantee the new order is better.

**The machine-wide-drain-vs-p95_wait tension, explained:** the early
pull relieves the STACKED core's OWN queue sooner, which is exactly
what machine-wide drain measures (fastest time to `max(rq)<=1` across
all cores) -- but it doesn't necessarily help the specific task that
got moved, or the ones periodic handles differently afterward as a
result. Aggregate recovery speed and worst-case per-task wait are
measuring genuinely different things; at this tiny burst scale, the
burst mechanism's one fixed action reliably helps the former while only
sometimes helping the latter. No logic changed for this diagnosis, per
instruction.

## Task 6 follow-up 2 (2026-09-27) -- burst-size sweep, crossover found, two caveats surfaced

A+B+C, `transition`, n=30 paired, penalty in {0, 2}ms
(`task6_burst_size_sweep.py`, `results_task6_burst_size_sweep.csv`,
plotted in `task6_burst_size_sweep.png`). `stacked_burst`: `burst_size`
in {2,4,8,12,16,24,32,48,64}, medium-intensity timing otherwise
unchanged (`burst_duration=8`, `arrival_rate_during_burst=1.5`) via
`intensity_overrides`. `bursty`: same sizes {16,32,48,64}, same base,
as the fork-placement comparison.

| burst_size | stacked_burst % change (p0 / p2) | sign/Wilcoxon p | bursty % change |
|---|---|---|---|
| 2 | 0.0% / 0.0% (detector never fires) | 1.0 / 1.0 | -- |
| 4 | -13.2% / -13.7% | <0.0001 both | -- |
| 8 | -6.2% / -6.2% | 0.02-0.10 / 0.006-0.007 | -- |
| 12 | -16.0% / -17.1% | <0.0001 both | -- |
| 16 | -15.1% / -14.4% | <0.0001 both | 0.0% |
| 24-64 | identical to size=16 (see caveat) | identical to size=16 | 0.0% |

**Caveat 1, confirmed directly (`WorkloadGenerator._plan_stacked_burst`
called standalone):** at this medium-intensity timing, `burst_duration=8`
combined with `arrival_rate_during_burst=1.5` (gap 0.667ms between
arrivals) caps the ACTUAL emitted tasks per burst at **13**, regardless
of requested `burst_size` beyond that -- confirmed empirically
(`requested=16` -> `actual=13`; `requested=64` -> `actual=13`). Sizes 16
through 64 are silently testing the IDENTICAL effective workload, which
is exactly why their numbers are byte-identical in the table (not a
real saturation/plateau finding -- an artifact of the duration cap,
marked on the plot rather than left to look like a real effect).

**Caveat 2, an important correction to the "low-intensity reversal"
finding above:** `burst_size=4` at MEDIUM-intensity timing shows a
clear, significant BENEFIT (-13.2%/-13.7%, p<0.0001) -- the opposite
sign from `stacked_burst`/low's `burst_size=4` result (+12.8%/+14.9%,
worse). Same nominal size, opposite outcome: **the low-intensity
reversal is NOT really about burst_size=4 specifically -- it's driven
by the SURROUNDING timing** (low intensity's slower
`arrival_rate_during_burst=0.5` spreads the same 4 tasks over 8ms
instead of ~2.7ms, and its `inter_burst_interval=60` gives periodic
balancing much more idle time to fully settle between bursts). This
sweep, which holds the surrounding timing fixed at medium and varies
only size, found **no negative region at all** across the whole
2-64 range -- burst-aware is either neutral (size=2, too small to
detect) or a consistent win (size>=4) at medium-intensity timing
specifically. The crossover into harm found in follow-up 1 is a
low-intensity-timing phenomenon, not a small-burst-size phenomenon --
worth being precise about this distinction in the paper.

**`bursty` (fork-placement comparison): p95_wait effect is exactly 0%
at every size tested, and the detector barely ever fires** (mean
`detector_fires` = 0.6-0.9 per 30-seed run, essentially never,
regardless of `burst_size` 16-64). Confirms and sharpens the earlier
finding: it's not that larger bursts under fork placement fail to
trigger a strong response -- fork placement spreads a burst's tasks
across the machine on arrival so effectively that the detector's
concentrated arrival-rate/queue-growth signals almost never cross
threshold at ANY burst size, because there's rarely a single core
with a large enough backlog to trigger them. Burst SIZE isn't the
relevant variable for `bursty`; PLACEMENT is.

**Crossover summary (SUPERSEDED, see 2026-09-27e below): at medium-
intensity timing, the transition is sharp and clean -- `burst_size=2`
(no detectable burst) to `burst_size=4` (clear, significant benefit),
with no negative region anywhere in the swept range.** This sweep's
sizes 16-64 were all secretly capped to the same ~13-task effective
workload by the fixed `burst_duration=8` (see the duration-cap caveat
above) -- the corrected re-run below tests real sizes up to 64 and
finds a materially different, more informative picture, including a
crossover into harm for `bursty` that this capped sweep could not have
found no matter how many "sizes" were nominally tried.

## Task 6 item 3 (2026-09-27e) -- sweeps redone with the duration cap fixed, and both burst_resets_timer variants

`burst_duration = burst_size / arrival_rate_during_burst` (so the
requested size is actually emitted -- asserted for every single burst
in every run, not just the first), `n_tasks` scaled to an exact
multiple of `burst_size` (10 full bursts, avoiding a truncated final
burst from the fixed task budget -- a second, smaller capping issue the
assertion caught immediately when first tried). A+B+C, `transition`,
n=30 paired, both `burst_resets_timer` variants
(`task6_sweeps_v2.py`, `results_task6_sweepA_burstsize_v2.csv`,
`results_task6_sweepB_interval_v2.csv`, plotted in
`task6_sweepA_burstsize_v2.png` / `task6_sweepB_interval_v2.png`).

### Sweep A: burst_size in {2,4,8,12,16,24,32,48,64}

**`stacked_burst`: no negative region anywhere from size 4 to 64 --
and the benefit does NOT plateau, it keeps growing with size** (roughly
-11% to -25% at sizes 4-16, then -20% to -38% at sizes 24-64,
`resets_false` reaching -35% to -39% at size 32-64). The earlier
"plateau at size>=16" was entirely the duration-cap artifact; the real
larger bursts keep giving the mechanism more genuine backlog to fix,
and the benefit scales with it. `size=2` still shows exactly 0% (the
detector never fires -- confirmed unchanged). `resets_false` is at
least as good as `resets_true` at every size, and consistently better
at size>=24 (e.g. size=64: -35.0%/-37.0% vs -33.0%/-35.1%).

**`bursty`: this is the big correction. It is NOT flat 0% at every
size -- it was flat 0% only because sizes 16-64 were all secretly
testing the SAME ~13-task capped workload before.** With the cap fixed:
sizes 2-16 still show exactly 0% (detector essentially never fires,
`detector_fires` 0-1.3 per 30-seed run) -- but starting at **size=24**,
`detector_fires` climbs (4.5 -> 11.8 -> 37.9 -> ~55-60) and a real,
significant HARMFUL effect appears, growing with size:

| burst_size | bursty p95_wait % change (penalty=0, resets_true / resets_false) | sign p |
|---|---|---|
| 16 | 0.0% / 0.0% | 0.25 (n.s.) |
| 24 | 0.0% (rounds to zero, but real) | <0.0001 |
| 32 | 0.0% (rounds to zero, but real) | <0.0001 |
| 48 | 0.0% (rounds to zero, but real) | 0.0066 |
| 64 | **+6.0% / +7.2%** | 0.043 / 0.005 |

At sizes 24-48 the mean % change rounds to 0.0% (baseline p95_wait is
tiny, ~1.0-1.02ms, at this intensity/size combination) but the sign
test is decisively significant (p<0.0001 to p=0.0066) -- the same
"rounds-to-zero-but-real" signature already seen for `heavy_tail`/high.
At size=64 the effect becomes visible in the mean too: **+6.0% to
+7.2%** (worse for burst-aware), significant at penalty=0 (p=0.04-0.005)
though noisier and non-significant for one cell at penalty=2.
**Fork placement spreads load well at small-to-moderate burst sizes,
but once a burst is big enough (~24+ tasks arriving via one shared
entry core, even under placement), some real concentration starts
forming, the detector starts seeing it, and the same low-intensity-
style harm mechanism (a smaller-snapshot batch getting split off early)
appears here too -- burst SIZE, not just intensity timing, can push
`bursty` into the same negative territory `stacked_burst`/low showed.**

### Sweep B: inter_burst_interval in {10,20,35,60,90}ms at fixed burst_size 4 and 12

**No negative region at any interval tested, for either size** --
`stacked_burst` stays favorable (roughly -15% to -32%) across the
entire 10-90ms range at both sizes. This is an important negative
result: **the original low-intensity harm is NOT explained by
`inter_burst_interval` (spacing between bursts) with `arrival_rate_
during_burst` held at medium's faster 1.5 -- it takes low intensity's
own slower 0.5 arrival rate specifically.** The closest this sweep
comes to reproducing it: at `burst_size=4`, `resets_true`'s benefit
shrinks toward neutral as interval grows (-19.8% at interval=10 down to
**-1.1%** at interval=60, non-significant, before partially recovering
at interval=90) -- trending toward the harm region but not clearly
crossing into it within this range. `resets_false` stays robustly
negative across the whole sweep regardless of interval, including at
`burst_size=4`/interval=60 where `resets_true` goes flat (-7.5% vs
-1.1%) -- **the `resets_false` design choice is not just better at low
intensity specifically; it's more robust across this whole timing
axis.** `burst_size=12` shows the benefit growing, if anything, at
larger intervals (up to -32% at interval=90) -- opposite of size=4's
trend, suggesting size and interval interact rather than either alone
being the whole story.

**Overall correction to the record:** the crossover into harm is driven
by SLOW within-burst arrival rate (confirmed: low intensity's own 0.5
tasks/ms specifically, not just "low intensity" as a bundle, and not
inter_burst_interval alone) for `stacked_burst`, AND, newly found here,
by LARGE burst size for `bursty` specifically (a mechanism `stacked_
burst`'s own sweep doesn't show, since it has no negative region across
the same size range) -- two distinct routes into the same qualitative
failure mode, on two different profiles, along two different axes.
**Correction below (2026-09-27f): the bursty size-24 finding, as
originally stated against p95_wait, does not survive a tie-tolerance
fix -- a smaller, different, but still real effect does.**

## Tie-tolerance bug fix (2026-09-27f)

**Found:** `bursty` size 24/32 showed mean_diff=+0.0% (CI +-0.0%) yet
sign_p<0.0001 -- per-seed differences were tiny float noise (arrival
times are multiples of 1/1.5ms and similar irrationals, not exactly
representable), and every sign-test/Wilcoxon site in this investigation
compared diffs to EXACT `0`, so two runs that were conceptually
identical for a given seed, differing only by ~1e-13, were never
classified as ties.

**Fixed:** added `TIE_TOLERANCE = 1e-6` to `paired_compare.py`
(`summarize_metric()`'s wins/ties classification and
`wilcoxon_signed_rank()`'s zero-dropping both now use
`abs(diff) <= TIE_TOLERANCE` instead of exact equality -- equivalent to
rounding each raw value to ~1e-6 before diffing) and applied the
identical fix to every task script with its own inline sign-counting
logic (`task6_sweeps_v2.py`, `task6_burst_size_sweep.py`,
`stress_test_bursty.py`). **New standing practice: every sweep/
experiment script now also saves PER-SEED raw metric values to CSV**
(not only aggregates), so a result can be re-analyzed (or re-checked
for exactly this kind of bug) without re-running any simulation.

**Re-checked `heavy_tail`/high (Task 6 Step 3's flagged case) with the
fix** (same seeds 2200-2229, A+B+C):

| metric | penalty | before fix | after fix | verdict |
|---|---|---|---|---|
| p95_wait | 0 & 2 | "significant," p~0.0002-0.0009 | **0/0 ties (exact tie every seed)**, p=1.0 | **RETRACTED -- pure floating-point noise; this metric is an exact structural floor for this profile (stdev=0.000 across all 30 seeds regardless of scheduler)** |
| avg_wait | 0 & 2 | wins=0/14, p=0.0001 | identical | survives -- real |
| p95_slowdown | 0 | wins=0/13, p=0.0002 | wins=0/2 (28 ties), p=0.50 | **RETRACTED at penalty=0** |
| p95_slowdown | 2 | wins=2/15, p=0.007 | unchanged | survives at penalty=2 |
| avg_slowdown | 0 & 2 | significant both | identical both | survives -- real |
| makespan / makespan_excess | 0 & 2 | 0/0 ties already | unchanged | never affected (already exact ties) |

**Re-ran the `bursty` half of sweep A with the fix**
(`task6_bursty_sweep_fixed.py`, per-seed values in
`results_task6_bursty_sweep_fixed_perseed.csv`, same seeds/settings as
the original for direct comparison), reporting p95_wait, p99_wait,
avg_wait, avg_slowdown:

**p95_wait (the pre-registered PRIMARY metric): the size-24/32 "harm"
is fully retracted -- exact ties, 30/30, zero variance, at BOTH
penalties, sizes 2 through 32.** Only at size 48 (p=0.06-0.22, n.s.)
and size 64 (p=0.01-0.42, borderline and inconsistent between
`resets_true`/`resets_false` and between penalties) does anything
resembling a real p95_wait effect appear, and it is weak.

**`avg_wait`/`avg_slowdown` DO show a real effect surviving the fix,
starting at size 24, identical between `resets_true`/`resets_false`
(ruling out a resets-timer artifact), growing with size:**

| size | avg_wait % change | sign p | avg_slowdown % change | sign p |
|---|---|---|---|---|
| 16 | +0.16-0.28% | 0.25-0.50 (n.s.) | +0.03-0.04% | 0.25-0.38 (n.s.) |
| **24** | **+1.34-1.60%** | **<0.0001** | **+0.12%** | **<0.0001** |
| 32 | +1.26-1.57% | <0.0001 | +0.12-0.21% | <0.0001 |
| 48 | +1.36-1.45% | 0.0002 | +0.13-0.14% | <0.0001 |
| 64 | +1.65-2.30% | 0.008-0.024 | +0.16-0.19% | 0.014-0.043 |

**Corrected verdict: `bursty`'s pre-registered primary metric
(p95_wait) does NOT show reliable harm until size 48-64 at the
earliest, and even then only marginally.** What IS real from size 24
onward is a smaller, consistent AVERAGE-case cost (not a tail-latency
one) -- a genuinely different, more modest finding than "real harm from
size 24," which is retracted as stated. The underlying mechanism
(concentration forming even under fork placement once a burst is large
enough) likely still holds, but its measurable consequence is on
typical-case wait, not the tail, at these sizes.

## Detector threshold calibration (2026-09-27g)

Pre-registered 12-config grid: `queue_growth_threshold` in {2,4,8},
`arrival_rate_threshold` in {0.8,1.5}, `combine` in {or,and} (new opt-in
parameter added to `BurstDetector.is_burst()` -- `"or"` preserves the
original single behavior exactly). A+B+C, `resets=False`, penalty in
{0,2}, n=30 paired, fresh seeds (10000-10899 range), tie-tolerant
tests, per-seed CSV, across **9 workloads**: `stacked_burst`
low/medium/high, `bursty`/high at sizes 24 and 64 (duration-scaled, per
the earlier fix), and the arrival-rate sweep's slowest/fastest points
(rate 0.5 and 3.0, sizes 4 and 12, `inter_burst_interval=60`) --
216 (workload x penalty x config) cells, ~7000 sim runs total, run as 9
parallel background jobs (`task6_threshold_grid.py <workload_key>`,
merged by `task6_threshold_grid_analyze.py`).

**Selection rule, stated before seeing results:** choose the config
with no significant harm (any of p95_wait/avg_wait/avg_slowdown, any
workload, either penalty) and, among those, the largest mean p95_wait
reduction on `stacked_burst` medium/high.

**4 of 12 configs were harm-free:** `q2_a1.5_and`, `q4_a1.5_or`,
`q8_a0.8_and`, `q8_a1.5_and` -- all but one are `combine="and"`, the
other is `"or"` at a much higher queue-growth bar, confirming that
requiring agreement between both signals (or a far stricter single
threshold) is what eliminates the harm found throughout this
investigation. Mean stacked_burst medium/high p95_wait reduction among
these four: **q2_a1.5_and -20.2%, q4_a1.5_or -19.0%, q8_a0.8_and
-10.0%, q8_a1.5_and -10.0%.**

**Selected: `queue_growth_threshold=2, arrival_rate_threshold=1.5,
combine="and"`.**

| workload | p95_wait % change | fires | significant harm |
|---|---|---|---|
| stacked_burst/low | 0.0% | 0 (never fires) | none |
| stacked_burst/medium | -14.5% / -10.1% | 56-59 | none |
| stacked_burst/high | -30.3% / -25.7% | 145-148 | none |
| rate=0.5 (slow), size 4 & 12 | 0.0% | 0 | none |
| rate=3.0 (fast), size 4 | 0.0% | 0 | none |
| rate=3.0 (fast), size 12 | -26.7% / -20.7% | ~50 | none |
| bursty/high, size 24 | 0.0% | 0 | none |
| bursty/high, size 64 | +0.4% to +4.6% (n.s.) | 232-255 | none |

**This setting resolves every harmful edge case found in this entire
investigation (2026-09-26/27's low-intensity `stacked_burst` harm, the
slow-arrival-rate mechanism, small-fast-burst `bursty` harm) simply by
declining to fire on them, while firing reliably and helping
substantially where the mechanism has genuine value** (`stacked_burst`
medium/high; the larger fast-arrival case). Recall/precision stayed at
1.0 (or the fire count itself was 0, recall/precision undefined) across
every harm-free workload -- when it fires under this calibration, it's
never firing on nothing.

**`BurstDetector`'s defaults updated to match** (`arrival_rate_
threshold=1.5`, `combine="and"`; `queue_growth_threshold=2.0` was
already the default) -- same precedent as the `burst_resets_timer`
design decision: pass `arrival_rate_threshold=0.8, combine="or"`
explicitly to reproduce the original detector behavior.

## CORRECTION (2026-09-28b) -- "requiring AND eliminates the harm" overclaims what the data shows

The claim two paragraphs up ("confirming that requiring agreement
between both signals ... is what eliminates the harm") does not
survive a direct check with `task6_threshold_grid_tradeoff.py`/
`task6_threshold_grid_harm_breakdown.py` (both new, `final_results/
1_calibration_grid/`, no new simulations -- read the existing per-seed
CSVs only). **`combine` is not what separates harmful from harm-free
configs.** Direct counterexample: `q4_a1.5_or` (`combine="or"`) is
harm-free (0 of 9 workloads); `q4_a1.5_and` (`combine="and"`, same
`q`/`a`) is harmful on 1 of 9 (`bursty_high_s64`/penalty=2, via
`avg_slowdown` +1.3%, 21/30 seeds worse, sign_p=0.043). AND does not
guarantee safety, and OR does not guarantee harm.

**The pattern that actually holds, across all 12 configs' harm counts
(0, 1, or 3 of 9 workloads):**
- Every `a=0.8` + `combine="or"` config (`q2_a0.8_or`, `q4_a0.8_or`,
  `q8_a0.8_or`) is harmful on the SAME 3 workloads
  (`bursty_high_s24`, `bursty_high_s64`, `rate3.0_s4`) regardless of
  `queue_growth_threshold` -- `q` doesn't matter here because
  `arrival_rate_threshold=0.8` alone, ORed in, already fires on its
  own often enough that queue growth is never the deciding factor.
- The harm-free configs are the ones that simply **fire less easily**:
  either `arrival_rate_threshold=1.5` (arrival rate rarely reaches it
  at all, so it rarely contributes a false trigger regardless of
  `combine`) or `queue_growth_threshold=8` (a high bar that rarely
  trips regardless of `arrival_rate_threshold`). `combine="and"` at
  `a=0.8` DOES reduce harm relative to the matching `"or"` config
  (`q2_a0.8_and`/`q4_a0.8_and`: harm 1, vs `q2_a0.8_or`/`q4_a0.8_or`:
  harm 3; `q8_a0.8_and`: harm 0, vs `q8_a0.8_or`: harm 3) -- AND has a
  real, independent, partial protective effect at the low arrival-rate
  threshold -- but it is not BY ITSELF sufficient (`q2_a0.8_and` and
  `q4_a0.8_and` are still harmful on 1 workload each) or necessary
  (`q4_a1.5_or` is harm-free).
- **The selection was a near-tie, not a clear win.** Among the 4
  harm-free configs, `q2_a1.5_and`'s benefit score (20.17% mean
  p95_wait reduction on stacked_medium/high) beat `q4_a1.5_or`'s
  (19.00%) by 1.17 points -- decided by the pre-registered "largest
  benefit among harm-free options" rule as written, not because AND
  was structurally superior to OR at this operating point. See
  `figure_threshold_grid_tradeoff.png`/`.pdf` for the full harm-vs-
  benefit scatter this correction is based on.

**What triggers each of the 3-harm configs' `bursty_high_s64`/
penalty=2 flag, checked directly (`harm_breakdown.csv`):** it's
`avg_slowdown` in every case (+1.2% to +3.1%, 21-26 of 30 seeds
worse), never `p95_wait` (whose change there is a non-significant
+0.4% to +4.6%, already noted above as "n.s.") -- consistent with the
average-case-not-tail harm pattern found earlier in this investigation
for `bursty`.

**The size-4 arrival-rate blind spot, mechanism stated explicitly:**
`arrival_rate()` is measured over a 5ms sliding window
(`arrival_window=5`). A `burst_size=4` burst therefore has AT MOST 4
arrivals in any 5ms window by construction, so its measured rate can
never exceed 4/5 = **0.8 tasks/ms**, no matter how fast those 4 tasks
actually arrive within the burst. `arrival_rate_threshold=1.5` is
therefore mathematically unreachable for any `burst_size=4` workload,
regardless of `arrival_rate_during_burst` -- this is why the calibrated
detector (§07's AND-gate finding) never fires on any size-4 workload in
the confirmation run's rate sweep, not a coincidence of the specific
rates tested.

## Correction (2026-09-27h) -- harm-flag significance-test bug (verified harmless to the grid's outcome), plus honest disclosure of what the selection trades away

**Default change above RETRACTED pending this check, per instruction --
`BurstDetector`'s defaults are back to the original
(`arrival_rate_threshold=0.8, combine="or"`) until this section's
findings are reviewed.**

**1. The bug.** `sign_p(k_side, n)` (`task6_threshold_grid.py`, and its
twin in every earlier task script) uses `k = max(k_side, n - k_side)`,
which is symmetric: **`sign_p(harms, n_eff) == sign_p(wins, n_eff)`
identically**, for any split with no ties, because both express the
same "how far is this split from 50/50" question regardless of which
side is larger. Analytically checked (`n=30`, several splits) --
confirmed exact equality every time. So `any_significant_harm`'s
definition, `p_harm < 0.05 and mean_var > mean_base`, does not actually
test "harms are the significant, majority direction" -- it tests "the
wins/harms split is significantly non-50/50, in EITHER direction," and
then uses the outlier-sensitive mean to decide which way to label it.
A metric where most seeds improve (wins >> harms) but a few outlier
seeds inflate the mean could, in principle, be mislabeled "harm."

**2. Recomputed from the existing per-seed CSVs** (no new
simulations -- `task6_threshold_grid_recompute_harm.py`), with the
corrected per-metric rule: `harms > wins` (direction must actually
favor harm by seed count, not just by mean) `and sign_p(harms, n_eff) <
0.05 and mean_var > mean_base + TIE_TOLERANCE`.

**Result: the bug did not change any classification in this dataset.**
19 cells were flagged by the old rule, 19 by the new rule, **0
disagreements** -- every cell the old rule called "harm" also had
`harms > wins`, so the outlier-driven misfire the bug makes *possible*
never actually occurred here. Harm-free config set is unchanged:
`q2_a1.5_and`, `q4_a1.5_or`, `q8_a0.8_and`, `q8_a1.5_and`. Re-applying
the pre-registered selection rule with the corrected flag gives the
same answer: **`q2_a1.5_and` (-20.17%), unchanged from before.** (The
19 flagged cells themselves are a mix of already-known effects: bursty/
size-24 p95_wait+avg_wait harm and bursty/size-64 + rate3.0_s4
avg_slowdown harm under the weaker/OR-combined configs -- none of them
land on the four winning configs' harm-free workloads.)

The bug is still worth fixing on principle (it happened to be inert
here, not because the logic is correct) -- `task6_threshold_grid.py`'s
`any_significant_harm` computation should be patched to require
`harms > wins` before the next grid that reuses it.

**3. Honest accounting of what the winner costs, since "harm-free +
largest mean reduction" is not the same as "strictly best":**

- **The -20.2% vs -19.0% margin between `q2_a1.5_and` and the runner-up
  `q4_a1.5_or` is close, but NOT simply noise -- it is a real,
  significant trade-off in OPPOSITE directions depending on workload/
  penalty**, found by a direct paired (same-seed) head-to-head between
  the two variants' `p95_wait` (not each vs baseline): `q2_a1.5_and` is
  significantly BETTER on `stacked_medium` at both penalties (-8.2%/
  -7.4%, sign_p=0.016/0.024) and on `rate3.0_s12` at penalty=0 (-10.5%,
  sign_p=0.008), but significantly WORSE on `stacked_high` at
  penalty=2 (+14.7%, sign_p=0.0052; a tie at penalty=0). The aggregate
  medium+high mean the selection rule uses averages over this split
  and reports a clean-looking number that hides it.
- **`q2_a1.5_and` gives up `rate0.5_s12`'s entire benefit.** Under
  `combine="or"` (`q2_a0.8_or` and `q2_a1.5_or` alike), this
  slow-arrival/size-12 workload gets a real -35% p95_wait reduction
  (23-25 fires/run). Under `combine="and"` (every "and" config,
  including the winner), arrival rate never reaches even the lower
  0.8 threshold on this slow workload, so the AND gate never opens
  regardless of queue-growth threshold: 0 fires, 0% change, across the
  board. This is a real, structural cost of AND-gating, not noise.
- **The winner's `stacked_medium`/`high` benefit is itself smaller than
  the ORIGINAL, harmful detector's** (`q2_a0.8_or`: -18.1%/-16.5% on
  medium, vs the winner's -14.5%/-10.1%) -- consistent with AND-gating
  trading some benefit for eliminating harm elsewhere, as expected, not
  a free lunch.
- **`q2_a0.8_or` (original detector, `resets=False`) is confirmed NOT
  significantly harmful on `stacked_low` or either `rate0.5` workload**
  in this grid -- its harm is concentrated on `bursty_high_s24`
  (p95_wait +15.7%, avg_wait +3.0%) and on `avg_slowdown` for
  `bursty_high_s64`/penalty=2 and `rate3.0_s4`/both penalties (+3-7%).
  So the original detector's harm profile, as measured by this specific
  grid, is narrower than "harmful everywhere" -- it is concentrated on
  small/fast bursts and average-case (not tail) slowdown, matching
  Task 6's earlier `bursty` average-case finding.

  **CORRECTION (2026-09-28) -- the `stacked_low` "not harmful" claim
  above does NOT hold on a second, independent seed sample.** Auditing
  the 2026-09-27j confirmation run's compact summary table (built by
  `task6_confirmation_tables.py`, no new simulations -- see below)
  found `q2_a0.8_or` IS flagged `any_harm=True` on `stacked_low` there,
  on fresh seeds (20000-20029) that were never inspected before this
  grid was designed: p95_wait +6.2%/+7.1% (wins 2/15, 0/16 -- 13-14/30
  seeds tie out), p99_wait +12.3%/+8.1%, both penalties, sign_p as low
  as 3e-05. This is NOT a bug or a contradiction between the two runs
  -- it is real sampling variance at a small, marginal effect sitting
  near the significance boundary: the raw magnitude is small (+6-7%)
  and roughly half of each 30-seed sample lands as an exact tie, so a
  weak true effect that close to the boundary can legitimately flip
  significance between two independent 30-seed samples of the same
  workload/config. Both the original grid (seeds 10000-10029, "not
  harmful") and the later confirmation run (seeds 20000-20029,
  "harmful") are valid, honestly-reported observations of the same
  underlying marginal signal -- neither supersedes the other, and
  averaging or re-running with a larger combined n (not done here)
  would be the correct way to resolve which is closer to the true
  effect size, not picking whichever result is more convenient.

  This correction was prompted by an unrelated request to audit a
  compact reporting table for exactly this kind of inconsistency (does
  a "harm: yes/no" flag ever contradict the primary metric's own
  reported change) -- while checking that, the `stacked_low` discrepancy
  between this section and the fresh-seed confirmation data surfaced
  directly. The same audit also found, and is logging here for
  completeness since it was likewise never given its own dated entry:
  `bursty_high_s24` is harmful on `avg_slowdown` (both penalties,
  +0.1%/+0.4%, 20/30 seeds worse both times) in addition to the
  `bursty_high_s24`/`bursty_high_s64` numbers already listed above;
  `rate3.0_s4` improves p95_wait a real -30.4%/-29.5% (p<0.001) while
  `avg_slowdown` gets significantly WORSE at the same time, +6.8%/+7.3%,
  27 of 30 seeds worse at BOTH penalties -- an improving primary metric
  and a harm flag from a different metric are not contradictory, they
  are two different questions about the same run. Full breakdown (every
  flagged metric, wins/harms/ties, sign_p, for all five harmful
  workloads) is in the Annotated Scheduler Internals artifact's §08
  "original detector's full harm profile" table, kept there rather than
  duplicated here in full.

**4. The pre-registered arrival-rate grid was only two-point, not the
full 5-point sweep.** This grid covers rate in {0.5, 3.0} at sizes
{4, 12} -- the sweep's slowest and fastest points only, as stated in
the pre-registration. The intermediate points (0.75, 1.0, 1.5) from the
earlier item-3 sweep instruction were **not** re-run under the
threshold grid; this section's `rate0.5`/`rate3.0` conclusions do not
generalize to the intermediate rates without further runs.

**Net:** the selection outcome is unchanged by the bug fix, but is
narrower than the earlier writeup implied -- `q2_a1.5_and` is the
correct pick under the pre-registered rule as literally written, but
it is a trade (better at medium intensity, worse at high-intensity+
penalty, forfeits the slow-arrival benefit) rather than a strict
improvement over the runner-up, and it gives up some of the original
detector's benefit to buy its harm-freedom.

## Design decision (2026-09-27i) — keep the pre-registered selection; final configuration becomes the default everywhere

**Decision: keep `q2_a1.5_and`.** The selection rule was stated before
seeing results, and the 2026-09-27h recheck (corrected harm flag,
`harms > wins` required) still selects it -- 0 of 216 cells were
reclassified by the bug fix, so there is no post-hoc justification for
overriding a pre-registered choice here. The trade-offs the recheck
surfaced are real and are recorded, not hidden, below.

**Trade-offs of `q2_a1.5_and`, stated explicitly (see 2026-09-27h for
the underlying numbers):**
- vs the runner-up `q4_a1.5_or`: better on `stacked_burst`/medium at
  both penalties (-8.2%/-7.4% p95_wait, sign_p=0.016/0.024) and on
  `rate3.0_s12`@penalty=0 (-10.5%, sign_p=0.008); worse on
  `stacked_burst`/high@penalty=2 (+14.7%, sign_p=0.0052; tied at
  penalty=0).
- forfeits `rate0.5_s12`'s entire -35% p95_wait benefit: windowed
  arrival rate on that workload peaks at 0.6 tasks/ms, verified
  directly by instrumenting `is_burst()`'s inputs -- below even the
  ORIGINAL detector's 0.8 threshold, so no `arrival_rate_threshold`
  value fixes this under `combine="and"`; only `combine="or"` (which
  lets queue-growth alone trigger) captures it.
- smaller `stacked_burst`/medium+high benefit than the original,
  harmful detector (`q2_a0.8_or`: -18.1%/-16.5% vs the winner's
  -14.5%/-10.1% at penalty=0) -- AND-gating buys harm-freedom by also
  giving up some benefit where the original detector wasn't actually
  harmful.

**Future work idea, recorded for the paper's discussion/limitations
section:** the AND gate's blind spot (slow, low-amplitude bursts where
arrival rate never crosses even a low threshold, but queue length still
grows meaningfully because the machine's remaining capacity is also
low) suggests arrival rate is the wrong denominator. A **capacity-
relative queue-based trigger** -- e.g. queue growth relative to the
domain's currently-idle/spare capacity, rather than an absolute
tasks/ms arrival-rate floor ANDed with queue growth -- could plausibly
recover the `rate0.5_s12`-style benefit without reopening the harm
cases that motivated requiring two independent signals in the first
place, since it would still require sustained real queueing (not just
a raw arrival count) before firing. Not implemented or tested this
session; a candidate for the next calibration round.

**Code changes (this entry, 2026-09-27i):** the "opt-in, old-behavior-
by-default" stance that governed every fidelity fix through Task 6 is
now superseded for these five flags, by explicit instruction --
`LoadBalancer`/`BurstAwareLoadBalancer` now default to
`newidle_mode="transition"`, `per_cpu_last_balance=True`,
`imbalance_model="kernel"`; `Main.run_simulation()` now defaults to
`load_model="runnable"`; `BurstDetector` now defaults to
`arrival_rate_threshold=1.5, combine="and"` (already had
`queue_growth_threshold=2.0`). `Experiment.run_all()` inherits all of
these through `run_simulation()`/`balancer_kwargs`, so a fresh
`results.csv` now reflects the final configuration. **`results.csv` in
this repo predates this change and was generated under the OLD
defaults** (`legacy_ema`, `per_cpu_last_balance=False`,
`imbalance_model="legacy"`, `load_model="legacy"`,
`arrival_rate_threshold=0.8, combine="or"`) -- not comparable to a
fresh `run_all()` without passing those explicitly. Every old default
remains reachable by passing it explicitly (see the module-level
comments added at each `__init__` in `LoadBalancer.py`,
`BurstScheduler.py`, `BurstDetector.py`, and `Main.py`, and
`Experiment.py`'s docstring for the caveat that `run_all()`'s single
shared `balancer_kwargs` can't express different per-scheduler kwargs,
so reproducing the pre-calibration detector specifically needs a
split like `task6_threshold_grid.py`'s `run_baseline()`/`run_variant()`).
Sanity-checked end-to-end: default `LoadBalancer`/`BurstAwareLoadBalancer`
construction now reproduces the exact same `p95_wait`/`detector_fires`
values as the earlier explicit-kwargs sanity check (seed 5100,
`stacked_burst`/medium); explicit legacy kwargs reproduce the original
pre-Fix-A/B/C numbers.

## Task 6 follow-up 3 (2026-09-27) -- low-intensity mechanism refined: splitting, not timer postponement

Pushback: a consistent p<0.0001 effect over 30 seeds can't be pure
chance -- correct, and Task 1's own finding already showed why (the
detector fires at exactly `t=start+4` with zero variance across 100
bursts). Hypothesis offered: `BurstScheduler`'s post-migration
`last_balance` reset (Fix A, per (checker, domain)) postpones the
periodic pass that would have moved MORE tasks. Checked directly rather
than assumed.

**(b) confirmed precisely, and it's the real driver:** at pair level,
`Domain.share_llc=True` (Fix C), so `migrate_util` NEVER fires there --
pair-level has-spare cases always take the sibling/`nr_running`-diff
branch, sized `(busiest_run - local_run) // 2` (`group_weight==1`
always holds at this level). Instrumented that branch directly on
burst 0, seed 5004, domain `n2-pair2`: at **t=64** (the burst-tagged
pass), `busiest_run=3, local_run=0` -> `(3-0)//2=1` -> moves 1 task.
At **t=66** (baseline's periodic pass, same domain), one more task has
arrived by then, `busiest_run=4` -> `(4-0)//2=2` -> moves 2 tasks
together. **The burst path uses the IDENTICAL Fix C formula, not a
different one** -- it just evaluates it against a smaller accumulated
snapshot because it fires earlier by design. The same formula
mechanically returns a smaller number, splitting what would be one
combined 2-task periodic move into two separate 1-task moves a few ms
apart.

**(a), first attempt (2026-09-27, superseded below):** compared gaps
between EFFECTIVE passes (n>0) and found a small, inconsistent pattern
-- but this measurement was flawed (correctly caught): comparing
gaps between effective passes pulls in unrelated LATER bursts on the
same domain (594ms, 1518ms gaps came from the domain's NEXT unrelated
burst, not from this burst's own timer behavior). Re-measured properly
below.

**(a), corrected measurement (`task6_verify_timer_attempts.py`):**
logged every periodic ATTEMPT -- every `is_designated_checker()` call,
which only happens once the interval gate has already passed,
regardless of the group-classification or `_balance_domain` outcome
that follows -- for the burst's own checker core and domain, from burst
start to +15ms, in both schedulers. In every directly comparable case
(4 of 6 bursts per seed; the other 2 involved a higher-level domain
with only one attempt inside the window either way), **the attempt
schedule is byte-identical between schedulers**:

| checker/domain | burst-aware attempts | baseline attempts |
|---|---|---|
| core0/n0-pair0 | [126,130,134,138] | [126,130,134,138] |
| core20/n2-pair2 | [192,196,200,204] | [192,196,200,204] |
| core0/n0-pair0 | [258,262,266,270] | [258,262,266,270] |
| core0/n0-pair0 | [390,394,398,402] | [390,394,398,402] |

**The interval-gate timing is NOT phase-shifted at all -- Fix A's
per-(core,domain) `last_balance` reset does not measurably delay the
checker's own attempt schedule.** What the earlier raw dump showed
(`n2-pair2`'s has-spare-branch check "missing" at t=66/70, appearing at
t=68 instead) is a real observation, but of a different thing: that
dump was scoped to `_balance_has_spare_kernel`, reached only when the
group still classifies as `has_spare` at that tick -- once burst-aware's
early action changes the checker's own busy/idle state (or the
busiest's remaining count), the group can classify differently and that
specific code path isn't reached that tick, even though the underlying
interval-gate attempt fired on schedule regardless. **This is a
downstream symptom of batch-splitting (b), not a separate timer effect.**

**Conclusion at the time, since corrected below by the pre-registered
ablation: only ONE mechanism looked confirmed (batch-splitting). The
timer-postponement hypothesis looked unsupported at the attempt-
schedule granularity measured here.** Whether the split favors or hurts
the tail task depends on which specific task ends up in which of the
two smaller batches. No logic changed, per instruction. **See the
2026-09-27b ablation entry below -- this conclusion was too strong; the
timer effect turned out to be real too, just not visible in the small,
manually-traced sample checked here.**

## Task 6, item 2 (2026-09-27b) -- pre-registered ablation: BOTH mechanisms are real, correcting follow-up 3

Added opt-in `burst_resets_timer` (`BurstAwareLoadBalancer.__init__`,
default `True` = current/original behavior, every existing result stays
valid). `False`: the burst path still moves tasks (batch-splitting
still happens) but does NOT touch `last_balance` at all afterward,
isolating batch-splitting's own effect from any timer interaction.
Three-way comparison (baseline vs `resets_true` vs `resets_false`),
A+B+C, `transition`, n=30 paired, **FRESH seeds (7000-7029/7100-7129/
7200-7229 -- distinct from every seed range inspected in any earlier
Task 5/6 entry)**, `stacked_burst` low/medium/high, penalty in {0,2}ms
(`task6_ablation_burst_resets_timer.py`).

| intensity | penalty | baseline vs resets_true | baseline vs resets_false | resets_true vs resets_false (direct) |
|---|---|---|---|---|
| low | 0 | +1.00ms, p<0.0001 | +0.50ms, p=0.0044 | -0.50ms, **p=0.0039** |
| low | 2 | +1.17ms, p<0.0001 | +0.43ms, p=0.0023 | -0.73ms, **p=0.0002** |
| medium | 0 | -2.90ms, p<0.0001 | -2.32ms, p<0.0001 | +0.58ms, p=0.0357 (borderline) |
| medium | 2 | -2.38ms, p<0.0001 | -2.59ms, p<0.0001 | -0.21ms, p=0.459 (n.s.) |
| high | 0 | -8.28ms, p<0.0001 | -6.82ms, p<0.0001 | +1.46ms, p=0.36 (n.s.) |
| high | 2 | -9.45ms, p<0.0001 | -8.15ms, p<0.0001 | +1.29ms, p=0.87 (n.s.) |

**`resets_false` does NOT remove the low-intensity harm -- it roughly
HALVES it, and the direct `resets_true` vs `resets_false` comparison is
itself significant (p=0.0039/0.0002).** This corrects follow-up 3's
"only batch-splitting matters" conclusion: that was based on manually
tracing 2 seeds' first 4-6 bursts each (a small sample, and possibly
not the pathway where the timer effect actually operates), while this
ablation is a direct causal test (toggle the mechanism, observe the
outcome) across 30 seeds -- the stronger form of evidence, and it says
plainly that the timer reset has its own real, independent contribution
at low intensity: batch-splitting alone (`resets_false`) still causes
significant harm vs baseline (p<0.005 both penalties, ruling out "it
was ONLY the timer"), but the timer reset roughly DOUBLES that harm on
top of it (p<0.001 for the direct comparison, ruling out "the timer
never mattered"). **Both mechanisms are real at low intensity; neither
one alone is the full story.**

At medium/high intensity, the timer-reset's own contribution shrinks to
borderline (p=0.036 at medium/penalty=0) or disappears entirely
(p=0.36-0.87 at high, p=0.46 at medium/penalty=2) -- there, the large
favorable effect is carried almost entirely by batch-splitting/early-
action dynamics regardless of whether the timer gets touched.
**Lesson for future mechanism claims in this investigation: a small,
manually-traced sample (however carefully instrumented) can miss a real
effect that a properly powered, pre-registered ablation catches
cleanly -- prefer the ablation's causal evidence over hand-tracing's
correlational evidence when they disagree, as they did here.**

## Correction (2026-09-27c) -- the item-1 attempt-trace was invalid; redone correctly

Found: `task6_verify_timer_attempts.py`'s domain/checker identification
matched `is_designated_checker()` calls by TIMESTAMP alone
(`t == t_trigger and is_checker`), but many domains get checked at any
given tick, so it silently picked whichever happened first in iteration
order -- **core 0 / n0-pair0 every time**, regardless of which core the
burst was actually stacked on (21, 11, 13, 7, ...). Every "byte-
identical attempt schedule" result in the superseded entry above was
comparing the WRONG domain. **That entry, and its conclusion that the
timer-postponement hypothesis wasn't supported, is retracted.**

**Redone correctly** (`task6_verify_timer_attempts_v2.py`): identify the
domain/checker DIRECTLY from the burst-tagged `_balance_domain()` call
itself (same technique the original, correct domain identification in
`task6_low_intensity_reversal.py` already used), not by guessing from
timestamp collisions. Result, **all 15 of 15 comparable bursts across
both seeds, zero exceptions:**

```
burst 2 (seed 5004): burst-tagged call at t=196, domain=n3-pair0
  burst-aware attempts: [192, 200, 204]
  baseline    attempts: [192, 196, 198, 200, 202, 204, 206]
```

**Baseline's attempts fire continuously every ~2ms (its natural
`min_interval`); burst-aware's attempts on the SAME (checker, domain)
go quiet for ~4-8ms immediately after the burst-tagged reset, giving
baseline 1-2 EXTRA intervening checks burst-aware doesn't get.** This
pattern repeats identically in every single burst checked -- a direct,
mechanistic confirmation of the phase shift, fully consistent with (and
now explaining, at the mechanism level) Task 6 item 2's ablation result
(the timer reset causes roughly half of `stacked_burst`/low's harm,
p=0.0039/0.0002 for the direct `resets_true` vs `resets_false`
comparison). The ablation and this corrected trace now agree: **both
the timer-reset mechanism and batch-splitting are real, independent
contributors** -- the record is fully consistent again.

## Design decision (2026-09-27d)

Based on the pre-registered ablation (halves `stacked_burst`/low's harm
while preserving essentially all of the medium/high benefit -- see the
2026-09-27b table), **`BurstAwareLoadBalancer`'s default changes to
`burst_resets_timer=False`.** `True` (the original/historical behavior)
remains available by passing it explicitly, for comparison. This is a
deliberate behavior change to the DEFAULT construction of
`BurstAwareLoadBalancer()` with no `burst_resets_timer` argument
supplied (e.g. `Main.main()`'s bare calls) -- every experiment script in
this investigation has passed explicit `balancer_kwargs` throughout, so
no prior EXPERIMENT result is silently affected, but this is noted here
because it changes what "the mechanism" means by default going forward.

## Update (2026-09-26c) — Task 1: heavy_tail/high's ~5.7% gain doesn't hold up either

Same paired-seed, n=30, sign-test methodology as the `bursty` correction
above (`task1_heavytail_check.py`, results in
`results_task1_heavytail.csv`), checking the ~5.7% makespan gain seen in
the original n=10 migration-penalty run (190.3->179.4 at 2ms).

| penalty | metric | baseline | burst_aware | mean diff (95% CI) | wins/n_eff (ties) | sign p |
|---|---|---|---|---|---|---|
| 0ms | makespan | 198.2+-53.8 | 178.6+-32.5 | -19.6 ([-38.3, -0.8]) | 12/21 (9) | 0.66 |
| 0ms | avg_wait | 0.477 | 0.485 | +0.008 | 0/16 (14) | 0.0000 |
| 2ms | makespan | 189.0+-36.8 | 177.1+-15.6 | -11.9 ([-26.3, +2.5]) | 15/23 (7) | 0.21 |
| 2ms | avg_wait | 0.477 | 0.484 | +0.008 | 0/17 (13) | 0.0000 |

**Not significant** (sign-test p=0.66 / 0.21 for makespan) despite a
mean-based 95% CI that (barely) excludes zero at penalty=0. That
disagreement is itself informative: `heavy_tail`'s task sizes are
Pareto-tailed by construction (`WorkloadGenerator._sample_cpu_time`),
so a handful of extreme-length-task seeds can drag a normal-approximation
CI around while most seeds show nothing -- the CLT assumption behind that
CI doesn't hold well here. The sign test, which doesn't assume normality,
is the one to trust, and it says no effect.

**Flag, not yet explained:** 9-15 of the 30 seeds (30-50%) produced
byte-identical makespan/wait between baseline and burst-aware. That's
stronger than "no significant difference" -- it suggests burst-aware's
trigger is firing on those seeds but finding nothing to move (same
"queues too shallow" mechanism already documented for low/medium
intensity in the 2026-09-14 update, possibly also active at
heavy_tail/high specifically because Pareto-tailed task sizes mean most
of the makespan is dominated by one long-running task with a short,
easily-drained queue around it). On the seeds where the schedulers *do*
differ, avg_wait is significantly worse for burst-aware (p<0.0001, 0
wins) -- consistent, but in the wrong direction. **Open question, not
yet explained** -- revisit with Task 4's detector-fire/burst-triggered-
migration counters and per-task traces to see whether this is a real
mechanism (e.g. burst-aware's extra checking work delaying something) or
another artifact of the tie-heavy pattern below. Worth investigating via
Task 3 (newidle gate) too. Not fixed here, ground rules say diagnose
only for now.

### Follow-up: the mean/CI and sign-test disagreement is a tail-clipping pattern

Baseline's makespan stdev (53.8) being much larger than burst_aware's
(32.5) at penalty=0 suggested burst_aware might be winning big on a few
seeds while losing small on many -- checked via sorted per-seed diffs
(`task1_followup.py`) and a Wilcoxon signed-rank test added to
`paired_compare.py` (normal approximation with tie correction, no scipy
available in this env).

At penalty=0 (30 seeds, same as above): **9 exact ties, 9 seeds where
burst_aware wins big (-12 to -170ms), 3 small wins, 9 small losses (+1
to +49ms).** Wilcoxon signed-rank: n=21 (9 zeros dropped), z=-1.478,
**p=0.1395**. At penalty=2ms: 7 ties, 8 big wins (-9 to -174ms), losses
up to +37ms, z=-1.643, **p=0.1003**.

This confirms the shape the stdev gap hinted at -- it is NOT "no effect,"
it's "an effect that's real in magnitude on a minority of seeds but
inconsistent in direction," which the sign test (counts only, blind to
magnitude) mostly missed (p=0.66/0.21) while Wilcoxon (rank-weighted)
picks up a suggestive-but-still-not-significant trend (p=0.10-0.14).
Plausible mechanism, not yet confirmed: `heavy_tail`'s Pareto-tailed
task sizes occasionally produce one very-long straggler task; if
burst-aware's proactive trigger catches it *while still queued* (before
it starts running and becomes un-migratable), it can avoid a large
makespan hit; if the straggler is already running, or the trigger fires
too late/early, nothing is gained and the extra checking overhead
becomes a small net loss instead. This is exactly the kind of
timing-dependent effect Task 4's lead-time metric (burst-aware's first
burst-triggered migration vs baseline's first migration, relative to
burst start) is meant to test directly -- noting it here as the
motivating hypothesis rather than confirming it yet.

## Update (2026-09-26d) — Task 2: placement_levels_walked_mean=800 explained

Checked (was flat at exactly 800 in every row of both `results.csv` and
`results_migration_penalty2ms.csv`, across every profile/intensity/
scheduler). It counts LEVELS, not events -- `placement_calls` is the
event count (one per task, since every task is born with
`prev_core=None` and always takes `hierarchical_new_task_placement()`'s
fork path exactly once; there is no re-placement path for an
already-run task), and `placement_levels_walked` is the SUM, across
every one of those calls in a run, of Domain-tree levels iterated
(`Placement.py`'s `while isinstance(node, Domain)` loop increments the
counter on every iteration, skip-branches included).

**It's not a bug -- it's a real total that happens to be a
deterministic constant in this experimental grid**, because both
factors that determine it are fixed everywhere: `n_tasks` defaults to
200 and is never overridden by `Experiment.py`, and the per-call tree
depth (4, from `build_topology()`'s static `cores_per_pair`/
`pairs_per_node`/`nodes_in_one_hop` params) never varies either.
200 calls x 4 levels/call = 800, every single row, regardless of
profile, intensity, or scheduler -- confirmed directly (`placement_calls
=200, placement_levels_walked=800, 800/200=4.0` for a spot-checked run).
Unlike `periodic_levels_walked`/`newidle_levels_walked` (which DO vary
by profile/intensity/scheduler because they depend on how long the run
takes and how often cores go idle), this one carries zero information
about runtime behavior -- it would only move if `n_tasks` or the
topology's depth were varied between runs, which nothing here does.

**Fix applied (rename + derived metric, no behavior change):** renamed
the `Metrics.summary()` key `placement_levels_walked` ->
`placement_levels_walked_total` (making clear it's a per-run total, not
a per-task average, despite Experiment.py's `_mean` column suffix), and
added `placement_levels_per_call` (`= levels_walked / calls`) as the
actually self-documenting form -- it would be the metric to watch if a
future experiment varies `n_tasks` or topology depth (Task 6's burst-
size sweep does not touch either, so it will still print a flat 4.0
there too; that's expected, not a regression). Updated
`Experiment.py`'s `METRIC_KEYS`, `Metrics.print_results()`, and added an
explanatory comment at the `WorkStats` counters themselves
(`Topology.py`). Raw simulation behavior is unchanged -- old
`results*.csv` files stay valid as historical snapshots under their
original column name.

## Pre-registered hypothesis (2026-09-26e) -- recorded BEFORE running Task 4's trace

Motivated by the tail-clipping pattern found in the 2026-09-26c follow-up
(heavy_tail/high, penalty=0, seeds 2200-2229): burst_aware's two biggest
wins were -170 and -157ms; its biggest loss was +49ms. Task 4 will trace,
for the task that finishes LAST in each of those three runs, its arrival
time, core, every migration, how many tasks shared its core over time,
and its finish time, under both schedulers.

**Hypothesis:** in baseline, the longest Pareto-tailed task ends up
sharing a crowded core under EEVDF fair-share (getting only its
proportional slice while queue-mates also run) and finishes late as a
result; burst-aware's proactive trigger moves that task's queue-mates
away before they can drag its finish time out, which baseline's
periodic/newidle paths either don't do or do too late.

**This is recorded here as a hypothesis to be tested, not a finding.**
It has NOT been checked against the actual per-task traces yet (Task 4
is planned, not run, as of this entry) and it must NOT be considered
confirmed by inspecting the same three seeds that motivated it -- that
would be circular (the hypothesis was built by looking at those seeds'
aggregate diffs, so of course a trace of those exact seeds can be made
to look consistent with it after the fact). **Confirmation requires
re-running the same per-task trace on 30 FRESH seeds** -- a different
seed range than 2200-2229 (already inspected for heavy_tail/high) and
1200-1229 (already inspected for bursty/high) -- and checking whether
the same mechanism (queue-mates moved away from the longest task) shows
up as the reason on the new sample's wins, not just plausible-sounding
on the original three.

## Update (2026-09-26f) — Task 3: newidle_cost_avg is deeply suppressed everywhere, confirmed

Checked whether the newly-idle gate (`Core.newidle_cost_avg`, starts at
0.5; EMA `0.9*avg + 0.1*(1 if a pull succeeded else 0)`; also gates
whether the NEXT attempt even happens via `random.random() < avg`) has
decayed near zero by the time a burst lands, which would mean baseline's
one reactive (non-periodic) balancing path is structurally suppressed
right when it matters. Traced every idle core's live `newidle_cost_avg`
at the exact instant of every ground-truth burst onset
(`task3_newidle_trace.py`, a `WorkloadGenerator` subclass that snapshots
before delegating to the real `_emit_burst()` -- no changes to
balancing/gate logic, report only, per the ground rules).

30 seeds x {bursty, heavy_tail} x {medium, high} x {baseline,
burst_aware}, pooling every idle core's value across every burst onset:

| profile/intensity | median | mean | max seen (whole run) | frac < 0.1 |
|---|---|---|---|---|
| bursty/high | 0.103 | 0.110 | 0.43 | 43.6% |
| bursty/medium | 0.011 | 0.021 | 0.23 | 98.4% |
| heavy_tail/high | 0.049 | 0.061 | 0.22 | 81.4% |
| heavy_tail/medium | 0.009 | 0.016 | 0.25 | 99.9% |

(baseline and burst_aware numbers are near-identical in every row --
e.g. bursty/high mean 0.1099 vs 0.1122 -- expected, since `try_newidle`
is shared code neither scheduler overrides.)

**Confirmed: the gate is suppressed almost everywhere, not just at
burst onset**, and never recovers to its own 0.5 starting value across
an entire run (max seen tops out at 0.43 even in the best case, bursty/
high; at medium intensity it's crushed to ~1-2% typical). Lower
intensity means more quiet time between bursts to decay in, and that's
exactly the pattern seen (medium is worse than high in both profiles).
**Correction (2026-09-26g): it DOES bias the comparison, retracting the
"does not bias" line above.** `try_newidle` is shared code, but
burst-aware has a SECOND path -- its own burst-triggered domain walk --
that entirely bypasses this crippled gate. Baseline's only non-periodic
recourse is the broken one; burst-aware's extra mechanism isn't gated by
it at all. So any win burst-aware showed in earlier tables may be partly
(or wholly) compensation for baseline's reactive path being artificially
disabled, not evidence of "burst-awareness" being a good idea on its own
merits. This directly motivates Task 3b below: fix the gate for BOTH
schedulers and see whether burst-aware's results change once baseline's
newidle path actually works.

## Update (2026-09-26h) — Task 3b: newidle_mode="transition", closer to real Linux

Added an opt-in `newidle_mode` (`LoadBalancer.__init__`, forwarded through
`BurstAwareLoadBalancer`): `"legacy_ema"` (default -- exact prior
behavior, so every existing `results*.csv` stays valid) vs
`"transition"`.

- **legacy_ema** (what Task 3 traced): `Core.run()` calls
  `try_newidle()` on EVERY idle tick; `try_newidle()` gates each attempt
  behind `random.random() < core.newidle_cost_avg`, an EMA of recent
  success that Task 3 showed decays toward 0 during any quiet stretch
  and then suppresses its own recovery.
- **transition** (new): `Core.run()` calls `try_newidle()` exactly ONCE,
  on the busy->idle edge (tracked via a new
  `Core._newidle_tried_this_spell` flag, reset when the core next picks
  up a task) -- not on every subsequent idle tick. `try_newidle()` in
  this mode has NO random gate at all; the once-per-transition call from
  `Core.run()` is the only gate. A core that stays idle after that one
  failed attempt relies on periodic balancing, per spec, until it goes
  busy and idle again.
- **Real Linux, for comparison:** fires newly-idle balancing on the same
  busy->idle transition, gated by `avg_idle` (measured, tracked per-rq)
  vs `sd->max_newidle_lb_cost` (a real, measured scan-cost budget) --
  and idle CPUs mostly rely on nohz idle-balance *kicks* from a busy CPU
  rather than re-polling themselves once idle. `transition` is the
  closer approximation of the two modes, but still doesn't model a real
  cost budget or kicks.
- **Not implemented:** the optional avg-idle-vs-fixed-cost-constant
  gate mentioned as a possible refinement. Judged not "simple" enough
  to add alongside the transition-timing change without introducing a
  second new unverified constant in the same step -- keeping this
  change isolated to the timing fix only, so its effect can be read
  cleanly. Worth adding as a separate, later change if the timing fix
  alone doesn't settle things.

Sanity-checked: `newidle_mode="legacy_ema"` (the default) reproduces
identical makespan to pre-change runs at a fixed seed (196.0ms,
bursty/high/seed=1, baseline) and identical `newidle_calls` (1909) --
confirms zero behavior change for every existing result.
`newidle_mode="transition"` at the same seed drops `newidle_calls` from
1909 to 221 (as expected: once per idle spell instead of once per idle
tick) and, notably, **baseline and burst-aware now produce the exact
same makespan (196.0) at this one seed.**

**CORRECTED (2026-09-26l, see Fix 3d): this tie was NOT because
`transition` fixed baseline's reactive path.** It's because `transition`
mode happens to make zero calls to any shared random source anywhere in
the balancing code, so it accidentally sidestepped the RNG-contamination
bug (found in Control 3c, root-caused and fixed in Fix 3d) that was
silently making baseline and burst_aware run different workloads under
`legacy_ema`. Once Fix 3d isolated the RNGs properly, `legacy_ema` ALSO
ties on this same seed (see Fix 3d's verification) -- the gate-timing
change itself (per-tick polling vs once-per-idle-spell) turns out to
make little to no difference to heavy_tail/high's outcome. The
correction below (2026-09-26l) supersedes the "compensating for
baseline's broken reactive path" reasoning originally written here and
in the Task 3b/Control 3c sections that follow.

### Task 3b results (2026-09-26i) — pre-registered hypothesis DISCONFIRMED

Ran the same paired-seed, n=30 methodology with `newidle_mode="transition"`
(`task3b_transition_check.py`, `results_task3b_transition.csv`).
`heavy_tail/high` used 30 FRESH seeds (base 3200) per the pre-registration
rule -- none of these seeds were inspected while forming the hypothesis.

| case | metric | baseline | burst_aware | wins/n_eff (ties) | sign p | Wilcoxon p |
|---|---|---|---|---|---|---|
| bursty/high, 0ms | makespan | 194.6+-2.1 | 194.4+-1.7 | 6/10 (20) | 0.75 | 0.47 |
| bursty/high, 2ms | makespan | 196.5+-2.8 | 197.1+-3.2 | 8/20 (10) | 0.50 | 0.21 |
| heavy_tail/high, 0ms (fresh) | makespan | 201.8+-66.0 | 201.8+-66.0 | 0/0 (30 ties) | 1.00 | 1.00 |
| heavy_tail/high, 2ms (fresh) | makespan | 202.1+-65.8 | 202.1+-65.8 | 0/0 (30 ties) | 1.00 | 1.00 |

**heavy_tail/high: makespan is IDENTICAL between schedulers on all 30
fresh seeds, at both penalty levels.** Not "not significant" -- zero
difference, every single seed. The pre-registered hypothesis
("burst-aware reduces worst-case makespan on heavy_tail/high") is
**disconfirmed**.

**Re-attributed (2026-09-26l, see Fix 3d): NOT because the newidle gate
got fixed.** At the time this was written, the tie was read as baseline
recovering a real straggler-avoidance benefit once its own reactive path
worked. Fix 3d's investigation and verification showed the true cause is
simpler and less interesting: `transition` mode makes no calls to any
shared random source, so it never triggered the RNG-workload-divergence
bug that `legacy_ema` had. After Fix 3d isolated the RNGs, `legacy_ema`
ALSO ties (29/30) on these same seeds. So this tie is not evidence that
"fixing newidle timing removes burst-aware's advantage" -- it's evidence
that **the original advantage (2200-2229, the tail-clipping pattern) was
itself just workload divergence, not a real effect of any kind, gate-
related or otherwise.** The disconfirmation of the pre-registered
hypothesis still stands, just for a more mundane reason than originally
written: there was never a real "burst-aware reduces worst-case
makespan" effect to explain -- the seeds that seemed to show one were
comparing different randomly-generated workloads.

Two things still differ, in burst-aware's disfavor: migration counts
are ~3% higher (51-53 vs 51, both penalties) and avg_wait/p95_wait are
very slightly worse (tiny, ~0.003ms, but p<0.005 -- a real, if small,
cost). So burst-aware isn't doing nothing; its extra migrations just
never touch the one straggler task that determines makespan on this
profile -- they move other, inconsequential queued tasks instead, at a
small real cost in wait time.

`bursty/high` under `transition` mode reconfirms the earlier
"no significant effect" finding from the `legacy_ema` runs (sign
p=0.50-0.75, Wilcoxon p=0.21-0.47) -- fixing the newidle gate didn't
change that conclusion, only heavy_tail/high's.

## Control 3c (2026-09-26j) — found a bigger problem than the one it was checking for

Asked: re-run heavy_tail/high on the SAME fresh seeds (3200-3229) with
`newidle_mode="legacy_ema"` -- if it shows real wins there but Task 3b's
`transition` run was 30/30 ties, the gate-attribution story is
confirmed; if legacy_ema also ties out, attribution stays open.

**Literal answer: neither.** `legacy_ema` on these seeds shows NO
significant net effect either (makespan mean_diff=+7.07ms, p=0.29 at
penalty=0; +0.40ms, p=0.65 at penalty=2) -- but with huge per-seed
swings in BOTH directions (-166 to +94 at penalty=0; -116 to +232 at
penalty=2), unlike `transition`'s exact 30/30 tie on the identical
seeds. That contrast (zero variance in one mode, huge variance in the
other, same seeds) didn't fit either predicted outcome, so it was
checked directly instead of just reported as "open":

**Found: under `legacy_ema`, baseline and burst_aware run DIFFERENT
workloads from the same seed.** Direct check at seed=3200: first 8
tasks are identical between the two runs, but baseline's longest task
is id=159 (cpu_time=127) while burst_aware's longest task is a
different task, id=192 (cpu_time=39). Under `transition`, both runs'
longest task is the same id=150, cpu_time=240.

**Root cause:** `try_newidle`'s legacy gate calls `random.random()` on
every idle tick, and how many idle ticks (hence how many of these
calls) happen before the next task arrives depends on scheduler
behavior -- baseline and burst_aware migrate differently, so their idle
timing differs, so they consume a DIFFERENT NUMBER of `random.random()`
calls between one task's generation and the next. `random` is one
global, shared module-level state; `WorkloadGenerator` draws every
task's cpu_time/weight/entry-core from that same stream. The instant
the two runs' idle-tick counts diverge -- which can happen from the very
first idle core -- every task generated after that point differs
between the "baseline" and "burst_aware" run of the SAME seed. They
stop being a paired comparison on one workload and become two
schedulers each running its own, only-initially-identical workload.
`transition` mode has zero scheduler-dependent `random()` calls
anywhere in the balancing code (confirmed: neither periodic nor
burst-triggered balancing call `random()` either), so it never
contaminates the shared stream -- which is the real reason it produces
exact ties on heavy_tail/high: the workload is now PROVABLY identical
between runs, so a tie means no scheduling difference touched the
critical-path task, not a lucky cancellation of different workloads.

**This retroactively affects every `legacy_ema` paired comparison run in
this investigation** (Task 1's heavy_tail check, the original bursty/
high corrections in 2026-09-26b/c, `results.csv`, `results_migration_
penalty2ms.csv`) -- none of them were actually comparing two schedulers
on one workload; each "pair" only started identical and diverged
partway through. The qualitative conclusion (no significant effect,
looks like noise) likely still stands -- this contamination adds noise
on top of any real scheduling difference, so if anything it made "no
effect" easier to find, not harder, and the actual reported numbers
already came with sign-test/Wilcoxon tests that don't assume a clean
paired design held. But the specific magnitudes and per-seed traces
from those earlier runs should not be read as "same workload, different
scheduler" -- they were partly comparing different workloads dressed up
as paired. **This is a strong, independent reason `newidle_mode=
"transition"` must be the mode for everything from Task 4 onward**
(already planned), beyond just fixing the suppressed gate: it's the
only mode where the paired-seed design is actually valid.

## Fix 3d (2026-09-26k) — root-caused: isolated RNGs, not just avoided legacy_ema

Control 3c found the symptom; this fixes the cause instead of just
routing around it.

**Full audit of every `random.` call in the codebase** (grep across all
`.py` files), before the fix:

| site | what it drew | category |
|---|---|---|
| `LoadBalancer.py` (`try_newidle`) | `random.random()` vs `newidle_cost_avg` | scheduling |
| `Main.py` (top of `run_simulation`) | `random.seed(seed)` | global seed |
| `Main.py` (`place()` fallback) | `random.choice(cores)` when `entry_core is None` | workload-adjacent |
| `WorkloadGenerator.py` (`_sample_cpu_time`) | `random.randint` / `random.paretovariate` | workload |
| `WorkloadGenerator.py` (`_sample_weight`) | `random.choice(nice_distribution)` | workload |
| `WorkloadGenerator.py` (`_emit_burst`) | `random.choice(cores)` (shared burst entry core) | workload |
| `task3_newidle_trace.py` | duplicated Main.py's `seed`/`entry_core` pattern | workload-adjacent |

All seven shared ONE process-global `random` module. `try_newidle`'s call
(scheduling, frequency depends on scheduler behavior) and the workload
draws (generation, meant to be scheduler-independent) drawing from the
same stream is exactly the bug Control 3c's investigation found.

**Fix, two layers (either alone would suffice; kept both for robustness):**

1. **`WorkloadGenerator` now owns a private `random.Random(seed)`
   instance** (`self.rng`, constructor param, never the shared module)
   for every one of its draws, entry-core selection included (the old
   `Main.place()` fallback is gone -- `place()` now asserts `entry_core
   is not None` instead of silently drawing one, since the generator
   always supplies one now).
2. **`generate_plan()` pre-computes the ENTIRE task arrival stream
   (times, sizes, weights, entry cores, deadlines) synchronously, before
   any simpy process -- in particular any `Core` -- has run at all.**
   `run_profile()` just plays that fixed plan back through simpy
   timeouts. This makes it structurally impossible for scheduling timing
   to affect generation, not just statistically unlikely given the RNG
   split: generation is fully finished before there's anything running
   that could perturb it.
3. **`LoadBalancer` gets a SEPARATE `random.Random(seed +
   NEWIDLE_RNG_SEED_OFFSET)`** (offset = 10,000,000, far outside any
   seed range this project sweeps) for the legacy newidle gate --
   `seed` is now threaded from `Main.run_simulation` into
   `balancer_cls(...)` for exactly this. `transition` mode still has no
   random call anywhere.
4. **`Main.run_simulation()` now also returns the generator's `plan`**
   (6th tuple element; every unpacking call site updated:
   `Main.main()`, `Experiment.py`, `paired_compare.py`,
   `stress_test_bursty.py`).
5. **`paired_compare.run_pair()` now asserts both runs saw an identical
   workload** (`assert_same_workload()`, comparing task_id/arrival_time/
   cpu_time/weight/entry_core-by-id/deadline for every task) before
   returning -- fails loudly, naming the first divergent task, rather
   than silently repeating the bug. This runs on every single call now,
   a standing regression check baked into the shared harness.
6. `task3_newidle_trace.py` reworked to match (it doesn't need Fix 3d's
   API changes for its own purpose, but the `WorkloadGenerator` rewrite
   broke its old `_emit_burst()`-subclassing approach; it now calls
   `generate_plan()` directly to read `ground_truth_bursts` before simpy
   starts, and schedules a small watcher process per burst instead of
   hooking generation).

**Verification (point 4): re-ran Control 3c** (heavy_tail/high, the SAME
seeds 3200-3229, `legacy_ema`, penalty=0.0) with the fix
(`control3c_v2_fixed_rng.py`), every call asserting matching workloads:

**29/30 exact ties, one seed differing by +1.00ms.** The huge swings
(-166 to +94ms before the fix, on these same seeds) are completely
gone. `legacy_ema` now matches `transition`'s 30/30-tie result almost
exactly. This confirms Control 3c's swings were ENTIRELY the workload-
divergence bug -- not a real difference in how the two newidle-gate
modes behave, and not a real burst-aware effect either. avg_wait is
still very slightly higher for burst-aware (mean +0.006ms, p<0.0001,
tiny but real) and migrations are still ~6% higher -- consistent with
Task 3b's `transition` finding that burst-aware moves a few extra,
inconsequential tasks without ever touching the critical-path task.

**Point 5: earlier legacy_ema paired comparisons are marked INVALID for
paired comparison** -- they were not comparing two schedulers on one
workload:
- Task 1's original heavy_tail/high check (2026-09-26c/2026-09-26 follow-up)
- The "tail-clipping" per-seed pattern found there (9 big wins / 9 losses
  / 9 ties, Wilcoxon p=0.10-0.14) -- **the big wins were most likely
  comparing different randomly-generated workloads (a different
  straggler-task length between the "baseline" and "burst_aware" run of
  the same nominal seed), not baseline recovering a real straggler-
  avoidance benefit.** This directly retracts the tail-clipping
  mechanism story: there was no real mechanism to explain, because the
  "paired" seeds weren't paired.
- The original `bursty`/`heavy_tail` corrections in 2026-09-26b/c and the
  `stress_test_bursty.py` results behind them
- `results.csv` and `results_migration_penalty2ms.csv` in full -- both
  generated entirely under `legacy_ema` with the shared-RNG bug present

**What likely still stands, with lower confidence:** the qualitative
conclusion "no significant, reliable burst-aware advantage" -- workload
divergence adds noise on top of any real scheduling difference, which
if anything makes a null result easier to reach honestly, not harder,
and every one of those conclusions already rested on a sign test/
Wilcoxon test rather than a raw mean. But treat the SPECIFIC numbers,
CIs, and per-seed traces in all of the above as unreliable regardless of
which way they pointed. Every experiment from here on (Task 4 onward)
uses the RNG-isolated code and `newidle_mode="transition"`, and is
valid as a genuine same-workload paired comparison. `"transition"` is
now this project's chosen operating mode for every experiment going
forward, for realism (Task 3's finding that `legacy_ema` self-suppresses
still stands on its own merits, independent of the RNG bug) --
`LoadBalancer.__init__`'s hardcoded parameter default stays
`"legacy_ema"` only for strict backward compatibility with code that
doesn't pass the kwarg, not as a recommendation.

## Makespan floor (2026-09-26m) — added lower-bound/excess/slowdown metrics

Added to `Metrics.summary()`: `makespan_lower_bound = max(arrival_time +
cpu_time)` over completed tasks (the earliest ANY scheduler could
possibly finish, if the single latest-relative-to-its-own-length task
ran with zero wait and zero preemption -- a per-task bound, not a
whole-machine capacity bound), `makespan_excess = makespan -
makespan_lower_bound` (the part actually attributable to scheduling
decisions), and per-task `slowdown = turnaround / cpu_time` (mean and
p95). Surfaced in `print_results()` too.

**Checked seed 3200, heavy_tail/high (`transition`, penalty=0):
makespan=369.0, lower_bound=368.67, excess=0.33 -- identical for both
schedulers.** Makespan on this profile/seed is almost entirely the
workload's own structural floor, not scheduling overhead: there is
essentially nothing left for ANY scheduler to improve. This is
consistent with, and explains, every heavy_tail/high result in this
investigation so far (the 30/30 ties under both modes post-Fix-3d, and
even the pre-fix noise, which was never real scheduling signal to begin
with). **Makespan is a weak metric for comparing schedulers on
`heavy_tail` specifically** -- it's dominated by one Pareto-tailed
straggler task whose own length sets the floor almost completely,
leaving near-zero excess for a load balancer to possibly affect. Prefer
`makespan_excess`, `avg_slowdown`/`p95_slowdown`, or wait-time metrics
over raw makespan when evaluating scheduler differences on this
profile going forward -- raw makespan differences here mostly reflect
the workload's own longest task, not the scheduler.

## Regression check (2026-09-26n) — bursty/high still shows no effect after the generator rewrite

Re-ran bursty/high, `transition`, penalty=0.0, n=30, same seeds
(1200-1229), against the Fix-3d-rewritten `WorkloadGenerator`. Result is
bit-identical to Task 3b's original `transition` run: makespan
mean_diff=-0.200, sign-test p=0.7539, Wilcoxon p=0.4685, same sorted
per-seed diffs, migrations ratio 1.025. **"No significant difference"
still holds, unchanged.** Expected and reassuring rather than a new
finding: `transition` mode never called any shared random source,
before or after Fix 3d, so the generator rewrite couldn't have changed
its behavior -- this is exactly confirmation that the rewrite introduced
no regression on the one case with nothing to fix in the first place.
`makespan_excess` tracks `makespan`'s p-value exactly, confirming
`makespan_lower_bound` is scheduler-invariant per seed as it should be.

## Pre-registered hypothesis (2026-09-26o) -- recorded BEFORE running Task 5

**Metrics, decided before looking at any `stacked_burst` results:**
PRIMARY metric is **p95 wait**. SECONDARY: mean slowdown, p95 slowdown,
`makespan_excess`, avg wait. **Makespan itself is reported (for
continuity with earlier tables) but not used for any conclusion** --
per the 2026-09-26m finding, raw makespan is dominated by the workload's
own longest task and is a weak signal for scheduler differences,
`heavy_tail` especially but not exclusively.

**Hypothesis:** on `stacked_burst` (all of one burst's tasks enqueued
directly onto a single random core, bypassing fork placement
[`hierarchical_new_task_placement()`] entirely, on purpose -- modeling
load landing unevenly on one core the way wakeup stacking or CPU
affinity can in real workloads, which fork placement's own descent
logic normally prevents -- the deliberately worst-case test for a
balancer when placement itself does nothing to spread load), burst-aware
reduces p95 wait vs baseline.

This is recorded here, before implementation or any run, so a later
result can be checked against what was predicted rather than a
hypothesis quietly reshaped to fit whatever comes out.

## Task 5 (2026-09-26s) — stacked_burst: hypothesis CONFIRMED, the first real effect found

**Implementation** (`WorkloadGenerator.py`): `stacked_burst` added as an
opt-in profile, deliberately kept OUT of `PROFILE_NAMES` (a new
`EXTRA_PROFILE_NAMES` list instead) so `Experiment.py`'s default grid
and every existing `results*.csv` are completely unaffected. Each burst
picks one random core (`self.rng.choice(self.cores)`, fresh per burst)
and every task in it carries a new `direct_core` field -- a stronger
instruction than `entry_core` (a placement HINT that still goes through
`select_core_for_task()`'s FORK-path descent,
`hierarchical_new_task_placement()`): `direct_core` bypasses placement
entirely, enqueuing straight onto that one core. Wired through `Main.place()` (and `task3_newidle_trace.py`'s
copy) and included in `paired_compare._plan_signature()`'s workload-
equality check. `run_pair()` gained `extra_processes_factory` (a nullary
callable invoked once PER SIDE) so baseline and burst_aware each get
their own fresh `diagnostics.py` sampler state instead of sharing one --
needed for Task 4's live idle/queue-imbalance metrics to mean anything
in a paired run.

**Result: n=30 paired seeds, transition mode, penalty in {0, 2}ms,
low/medium/high** (`task5_stacked_burst.py`,
`results_task5_stacked_burst.csv`):

| intensity | penalty | p95_wait baseline | p95_wait burst_aware | mean diff | wins/30 | sign p | Wilcoxon p |
|---|---|---|---|---|---|---|---|
| low | 0 | 11.2 | 8.3 | -3.0 | 30/30 | <0.0001 | <0.0001 |
| low | 2 | 11.4 | 8.4 | -3.0 | 29/29 (1 tie) | <0.0001 | <0.0001 |
| medium | 0 | 32.6 | 23.0 | -9.6 | 28/30 | <0.0001 | <0.0001 |
| medium | 2 | 33.7 | 23.0 | -10.8 | 30/30 | <0.0001 | <0.0001 |
| high | 0 | 178.2 | 88.0 | -90.1 | 30/30 | <0.0001 | <0.0001 |
| high | 2 | 185.7 | 93.6 | -92.1 | 30/30 | <0.0001 | <0.0001 |

**PRE-REGISTERED HYPOTHESIS CONFIRMED, at every intensity and penalty
tested, both tests agreeing every time.** Every secondary metric
(avg_slowdown, p95_slowdown, avg_wait) shows the identical pattern --
strongly significant, growing effect size with intensity, all 28-30/30
wins. Relative effect: ~27% p95_wait reduction at low, ~30% at medium,
**~50% at high** (178ms->88ms). This is the first reliably real
scheduler-vs-scheduler effect found anywhere in this investigation --
`bursty` and `heavy_tail` showed nothing this clean at ANY sample size.
Makes sense given the design: `stacked_burst` is the scenario
specifically engineered so placement leaves real, large, exploitable
imbalance behind (all of a burst on one core, no spreading at all)
-- exactly the condition under which a proactive rebalancer has
something real to fix, unlike `bursty`/`heavy_tail` where fork placement
(the descent-with-idlest-sibling-bias logic in
`hierarchical_new_task_placement()`) already spreads load reasonably
well on arrival and leaves little room to improve on. `stacked_burst`
exists specifically to bypass that -- it's not a claim that fork
placement fails in general, only that this profile deliberately removes
the one mechanism (spread-on-arrival) that was otherwise absorbing most
of `bursty`/`heavy_tail`'s load without any balancer's help.

**`makespan`/`makespan_excess` (reported, not used for conclusions --
per the pre-registration, and this result vindicates that choice):**
significant at low (p=0.0009/0.087) and massively significant at high
(p<0.0001, -200ms), but a clean NULL at medium (p=0.70/0.85, wins
15/27 and 13/28 -- indistinguishable from a coin flip). Had makespan
been the metric relied on, medium intensity would have looked like "no
effect" despite p95_wait showing a strong, consistent one there too --
exactly the failure mode the pre-registration was written to avoid.

**Diagnostics:** `detector_quality` is essentially perfect at every
intensity -- recall=0.997-1.000, precision=1.000 (stacked_burst's signal
is about as unambiguous as a burst detector could ever see: 20-30 tasks
landing on one core with the same entry point). The funnel narrows very
differently by intensity: low keeps ~99% of fires through to an attempt
(50.4 fires -> 49.9 attempts), medium narrows hard (159.3 -> 33.0, the
cooldown gate doing much more filtering), and at high, attempts (13.0)
are FEWER than resulting migrations (18.6) -- each burst-triggered pass
moves multiple tasks per call once there's this much backlog on one
core. `avg_detection_latency` drops with intensity (4.0ms -> 1.6ms ->
0.73ms), as expected -- faster arrival means the threshold trips sooner.
`idle_while_waiting_time` and `queue_imbalance` (during-burst mean) are
both lower for burst_aware at every intensity, consistent with the wait-
time story.

**Lead time (`vs_burst_tagged`) is intensity-dependent, not uniformly
favorable:** positive (burst-aware's own mechanism genuinely acts
first) at low (+2.6 to +2.7ms) and high (+1.65 to +2.15ms), but
slightly NEGATIVE at medium (-1.0 to -1.3ms) -- baseline's periodic/
newidle path still sometimes beats burst-aware's dedicated mechanism to
the punch at medium intensity specifically, even though burst_aware
still wins decisively on every wait-time metric there. So burst-aware's
medium-intensity advantage isn't fully explained by "it reacts first" --
worth investigating further if pursued, but not blocking the headline
result, which doesn't depend on lead time being positive.

## Task 4 (slimmed) — diagnostic metrics implemented, verified on one seed

Implemented, all read-only instrumentation (no scheduling/detector
logic touched):

- `Main.run_simulation()` gained an `extra_processes` hook (list of
  `fn(env, cores, balancer) -> generator`, registered as extra simpy
  processes; `None` by default, zero effect on any existing call).
- `diagnostics.py` (new): `idle_while_waiting_sampler` /
  `queue_imbalance_sampler` (live, per-tick, use the hook above),
  `summarize_queue_imbalance`, `detector_quality` (recall/precision/
  avg detection latency vs `ground_truth_bursts`), `lead_time_per_burst`
  (baseline's first migration at/after a burst's start minus
  burst-aware's first BURST-TAGGED migration at/after the same start;
  positive = burst-aware acted earlier) -- all post-hoc, pure functions.
- `BurstAwareLoadBalancer` gained the funnel counters: `detector_fires`
  + `detector_fire_times` (raw `is_burst()==True`, before the cooldown
  gate), `burst_balance_attempts` (property aliasing the existing
  `burst_triggers`, post-cooldown), `burst_triggered_migrations` (actual
  task-count moved, summed across every domain level's
  `_balance_domain()` call under the burst tag).
- `Metrics.summary()` gained `p95_wait`/`p99_wait` directly (previously
  only computed ad hoc in `paired_compare.py`; that duplicate removed).

**Verified on bursty/high, seed=1200, transition, penalty=0**
(`task4_verify_one_seed.py`; ground-truth-bursts equality between the
two runs asserted and held):

| | baseline | burst_aware |
|---|---|---|
| makespan / excess | 197.0 / 4.53 | 193.0 / 0.53 |
| avg_wait / p95 / p99 | 1.052 / 4.667 / 7.867 | 1.147 / 5.067 / 7.867 |
| avg_slowdown / p95 | 1.109 / 1.422 | 1.115 / 1.533 |
| idle_while_waiting_time | 13.0 ms | 6.0 ms |
| queue_imbalance (during-burst mean/max) | 0.385 / 4 | 0.462 / 4 |

burst_aware-only: `detector_fires=20`, `burst_balance_attempts=9`,
`burst_triggered_migrations=2` (7 ground-truth bursts; the funnel narrows
sharply: 20 raw signals -> 9 past cooldown -> 2 actual tasks moved).
`detector_quality`: recall=0.71 (5/7 bursts got a raw fire inside their
window), precision=1.00 (every fire fell inside some real burst's
window -- no false positives on this seed), avg_detection_latency=4.11ms.

**Notable on this one seed, not yet a finding (n=1):**
`lead_time_per_burst` came back **negative** on all 4 bursts where both
sides had a matching migration (-30.2, -30.2, -5.2, -5.6; mean -17.8) --
i.e. **baseline's existing periodic/newidle path reacted to these
bursts BEFORE burst-aware's own dedicated burst-triggered mechanism
did.** If this holds up across a real sample (Task 5/6), it would be a
genuinely important, if unflattering, result: the detector's own
cooldown-gate delay plus the ~4ms average detection latency may make
the "proactive" mechanism slower in practice than the reactive paths it
was meant to beat. Flagged here as a lead worth specifically checking
in Task 5's stacked_burst results, not asserted yet from one seed.

### Fix (2026-09-26p): lead_time_per_burst double-counted a migration across bursts

Bug, found by inspecting the seed-1200 output above: with no upper
bound on the match window (`t >= start`, no `end`), a single later
migration could get claimed as "the nearest match" by every EARLIER
burst that had no closer migration of its own -- explaining why -30.2
appeared twice in the original output despite only 2 burst-tagged
migrations existing that whole run. Fixed in `diagnostics.
lead_time_per_burst()`: matches are now restricted to
`[start, end + 10ms]` and are exclusive (each migration claimed by at
most one burst, first-come-first-served in burst-start order). Also
split into two definitions per the request: `vs_burst_tagged`
(baseline's first-any-migration vs burst_aware's first BURST-TAGGED
migration) and `vs_any_migration` (baseline's first-any-migration vs
burst_aware's first migration of ANY kind) -- plus explicit
no-match counts per side instead of silently shortening the list.

**Corrected seed-1200 values:** `vs_burst_tagged` now has only 2 entries
(-5.2, -5.6ms, mean -5.4) -- 5 of the 7 bursts had NO burst-tagged
migration in their window at all, consistent with only 2
`burst_triggered_migrations` total that run. `vs_any_migration` has 6
entries, **all exactly 0.000** -- baseline's and burst_aware's first
migration of ANY kind land at the identical simulated tick in every
matched burst. Plausible explanation, not yet confirmed: periodic
balancing is shared code running against a now-provably-identical
workload (Fix 3d), so it's unsurprising it fires at the same tick for
both schedulers when nothing burst-specific has intervened -- meaning
the burst-tagged comparison (still negative, favoring baseline) is the
one that actually isolates the burst mechanism's own contribution; the
any-migration comparison is mostly measuring the shared periodic path
and isn't very informative here.

### Observer-effect check (2026-09-26q): samplers don't perturb the simulation

Ran bursty/high seed=1200, both schedulers, with and without
`diagnostics.py`'s live samplers attached (`task4_observer_effect_check.
py`). makespan, avg_wait, p95_wait, migrations, and detector_fires came
back **bit-identical** with vs. without, for both schedulers. No fix
needed -- the samplers only read core state (`is_idle()`, `len(rq)`)
and never touch task/core/balancer fields, and that held up under a
direct check rather than just being assumed safe.

### Single-seed observations to watch in Task 5 (not findings yet, n=1)

- **The funnel narrows sharply:** `detector_fires=20` -> `burst_balance_
  attempts=9` (past the cooldown gate) -> `burst_triggered_migrations=2`
  (tasks actually moved). Most of what the detector "sees" never turns
  into action, and almost none of what turns into action actually moves
  a task.
- **Detection latency (~4.1ms) is more than half the burst duration
  (~7.7ms, `burst_duration=8` at high intensity).** By the time the
  detector fires, on average, more than half the burst has already
  arrived -- worth checking whether this leaves enough of the burst
  still queued for a migration to matter.
- **`vs_burst_tagged` lead time favors baseline (-5.4ms mean, n=2)** --
  baseline's periodic/newidle path beat burst-aware's own dedicated
  mechanism to the punch on both bursts where a comparison was even
  possible. If this holds up on a real sample, it's a genuinely
  important, unflattering result for the mechanism as currently tuned.

## Task 6, item 4 — confirmation run (2026-09-27j): the final configuration, on fresh never-inspected seeds

Full confirmation of the FINAL configuration adopted in 2026-09-27i
(A+B+C, `resets=False`, `runnable` load_model, calibrated detector
`arrival_rate_threshold=1.5`/`queue_growth_threshold=2`/`combine="and"`
-- all now plain defaults, zero kwargs needed) against the verified
baseline, on a **fresh seed range (20000-21529)** never inspected
before this run. n=30 paired, penalty in {0,2}, tie tolerance, the
corrected harm rule (`harms > wins` required, see 2026-09-27h),
per-seed CSV, workload-identity assertion on every pair.
`task6_confirmation_run.py <workload_key>` x 16 workloads (run as 16
parallel background jobs), merged and plotted by
`task6_confirmation_analyze.py`. Primary comparison: baseline vs FINAL.
Report-only secondary (no selection decision made here -- already
decided in 2026-09-27i): the original pre-calibration detector
(`q2_a0.8_or`) and the runner-up (`q4_a1.5_or`).

**Workloads:** `stacked_burst` low/medium/high; `bursty`/high sizes 24
and 64; `heavy_tail`/high (new to this investigation -- not in the
2026-09-27g calibration grid); the FULL 5-point arrival-rate sweep
(0.5, 0.75, 1.0, 1.5, 3.0 tasks/ms) x sizes {4, 12}, `inter_burst_
interval=60`, `burst_duration=size/rate` (exact, no truncation).

### Headline result: FINAL is harm-free on every workload in this run, including one (`heavy_tail`) it was never calibrated against

Full main table: `results_task6_confirmation_MAIN_TABLE.csv`. No cell
for the `final` variant trips the corrected harm rule on any of the 6
metrics (p95_wait, p99_wait, avg_wait, avg_slowdown, p95_slowdown,
makespan_excess), at either penalty, on any of the 16 workloads.

| workload | p95_wait % change (p0 / p2) | fires | notes |
|---|---|---|---|
| stacked_burst/low | 0.0% / 0.0% | 0 | never fires |
| stacked_burst/medium | -11.3% / -11.0% | 56-58 | sign_p<0.01 both |
| stacked_burst/high | -34.5% / -29.4% | 144-148 | sign_p<0.0001 both |
| bursty/high, size 24 | 0.0% / 0.0% | 0 | never fires |
| bursty/high, size 64 | -0.7% / +3.4% | 235-256 | neither significant |
| heavy_tail/high | 0.0% / 0.0% | 0 | never fires -- see below |
| rate 0.5/0.75/1.0, sizes 4 & 12 | 0.0% (all 12 cells) | 0 | never fires -- see below |
| rate 1.5, size 4 | 0.0% / 0.0% | 0 | never fires |
| rate 1.5, size 12 | -26.1% / -24.1% | 32 | sign_p<0.0001 both |
| rate 3.0, size 4 | 0.0% / 0.0% | 0 | never fires |
| rate 3.0, size 12 | -25.9% / -19.4% | 50 | sign_p<0.0001 both |

Magnitudes on `stacked_burst`/medium+high replicate the earlier
calibration grid's ballpark on an independent seed range (-11%/-34.5%
here vs -14.5%/-30.3% there at penalty=0 -- same sign, same order of
magnitude, expected seed-to-seed variation, not a discrepancy).

**`heavy_tail`/high is a genuinely new confirmatory data point** (not
part of the 2026-09-27g calibration grid at all): FINAL never fires on
it (0 detector fires, exact tie on every metric, both penalties) --
correctly avoiding harm on a workload it was never tuned against. The
ORIGINAL detector, by contrast, DOES fire on `heavy_tail`/high and
produces small but real, significant average-case harm there
(`avg_wait` +1.08%/+0.84%, `p99_wait` +7.1%/+4.0%, `avg_slowdown`
+0.09%/+0.42%, all `harms=17-26/30`, `wins=0/30` -- p95_wait itself is
an exact structural floor here, stdev=0 regardless of scheduler,
matching the earlier 2026-09-26 `heavy_tail` floor finding, so p95_wait
correctly shows 0.0% for both). This is exactly the small,
average-case-not-tail harm pattern from small/fast bursts established
earlier in Task 6's `bursty` investigation, now confirmed on a third,
previously-unexamined profile.

### The AND-gate's blind spot is broader than the 2-point (0.5/3.0) grid suggested

The full 5-point sweep (not run before this confirmation) shows FINAL
**never fires on ANY burst_size=4 workload across the entire rate range
0.5-3.0** -- not just the slow end. It only fires on size=12 at rate
>= 1.5. The ORIGINAL (`"or"`) detector, by contrast, captures
substantial benefit almost everywhere in this space:

| workload | original p95_wait % change | harm? |
|---|---|---|
| rate0.5_s12 | -30.7% / -30.5% | no |
| rate0.75_s4 | **-43.0% / -43.2%** | no |
| rate0.75_s12 | -39.0% / -40.9% | no |
| rate1.0_s4 | -14.3% / -14.4% | no |
| rate1.0_s12 | -23.3% / -19.9% | no |
| rate1.5_s4 | -11.3% / -10.5% | no |
| rate3.0_s4 | -30.4% / -29.5% | **yes** (only harmful size-4 cell) |

**The single largest benefit anywhere in this entire sweep --
-43% p95_wait -- occurs at rate=0.75/size=4 under the ORIGINAL
detector, a cell FINAL never fires on at all.** The relationship is
non-monotonic in rate (biggest at 0.75, smaller at both 0.5 and higher
rates) -- not investigated further this session; a candidate follow-up
if the mechanism matters for the paper's discussion. See
`figure_p95wait_vs_arrival_rate.png` for the full picture: FINAL (blue)
is flat at 0% across all of burst_size=4 and the low/mid rates of
burst_size=12, while ORIGINAL (red) tracks a real, mostly-beneficial,
non-monotonic curve almost everywhere. This sharpens (does not
reverse) the 2026-09-27i trade-off writeup: FINAL's AND-gate forfeits a
LARGER swath of the original detector's benefit than the two
previously-tested rate points (0.5, 3.0) indicated -- essentially all
of `burst_size=4`, at every rate tested, plus most of `burst_size=12`
below rate=1.5.

### RQ4 -- cost of the mechanism when it does fire

`sched_cores_scanned` (extra scanning work) rises modestly wherever
FINAL actually fires, and is exactly 0% everywhere it doesn't (by
construction -- no fires, no extra work):

| workload | extra scanning | p95_wait change | migrations change |
|---|---|---|---|
| stacked_burst/medium | +2.2% / +2.5% | -11.3% / -11.0% | -0.2% / -2.8% |
| stacked_burst/high | +6.0% / +2.2% | -34.5% / -29.4% | -7.0% / -10.2% |
| bursty/high, size 64 | +3.5% / +3.5% | -0.7% / +3.4% | +6.4% / +8.1% |
| rate1.5_s12 | +1.9% / +2.1% | -26.1% / -24.1% | -4.8% / -4.0% |
| rate3.0_s12 | +2.4% / +2.2% | -25.9% / -19.4% | -3.5% / -3.6% |

Every workload where FINAL fires meaningfully shows extra scanning work
in the low single-digit percent, paid for a p95_wait reduction one to
two orders of magnitude larger in percentage terms -- a favorable
cost/benefit ratio wherever the mechanism is actually active. The one
exception, `bursty`/size64/penalty=2, spends +3.5% extra scanning for a
non-significant +3.4% p95_wait change (net wash, not a win, not
flagged as harm). `burst_balance_levels_walked` (the domain-chain-walk
depth specifically attributable to burst triggers, as opposed to
periodic/newidle scanning) is reported as an absolute count rather than
a %% change, since the baseline structurally never runs this path at
all (0 by construction) and a %% change against a zero denominator is
undefined: 68 (medium), 52 (high), 132 (bursty/size64), 40 (rate1.5/
rate3.0 size12) domain-levels walked per run, scaling roughly with
`detector_fires`.

**Net:** the confirmation run holds up the 2026-09-27i decision --
FINAL is harm-free everywhere tested, including a new profile
(`heavy_tail`) and a wider rate sweep than the calibration grid used,
and its cost is modest whenever it fires. But the wider sweep also
shows the AND-gate's forfeited-benefit region is larger than previously
measured (all of `burst_size=4`, not just the slow end) -- this
sharpens, rather than changes, the already-logged 2026-09-27i
trade-off and future-work note (a capacity-relative queue-based trigger
as a way to recover some of this without reopening the harm cases).

Artifacts from this run: `results_task6_confirmation_<workload>_
perseed.csv` / `_summary.csv` (16 each), `results_task6_confirmation_
MAIN_TABLE.csv` (the paper's main table), `confirmation_run_analysis.txt`
(full condensed grid + RQ4 + secondary-variant tables),
`figure_p95wait_vs_arrival_rate.png`.

## Task 7 (2026-09-29) — Fix D: periodic-checker election didn't match should_we_balance()

**Fidelity gap, found by inspection, confirmed by direct measurement
before any code changed.** `LoadBalancer._find_checker()` elects from
`domain.cores()` -- the domain's WHOLE SPAN. Real `should_we_balance()`
(`fair.c:13162-13221`, v7.2) elects from `env->sd->groups` -- the
LOCAL GROUP containing the asking CPU (`sd->groups` is always oriented
per-CPU, local group first) -- restricted further to that group's
`group_balance_mask()` (`sched.h:2227-2262`, built by
`build_balance_mask()`, `topology.c:1199-1225`): the subset of the
group's span whose OWN per-CPU child-domain span equals the group's
span exactly, i.e. cores genuinely "home" to that group, not merely
counted in it. `group_balance_cpu(sg) = cpumask_first(group_balance_mask(sg))`
(`topology.c:1090-1092`) is the fallback when nobody's idle. Newly-idle:
every CPU allowed (`env->idle == CPU_NEWLY_IDLE` returns 1
unconditionally, `fair.c:13179-13183`) -- unaffected by this fix.

**Consequence, exactly as hypothesized before measuring:** at
onehop/machine levels, where `Domain.home_children` means a domain's
span legitimately includes ring-neighbor cores that never climb that
exact domain object (see the 2026-09-29 `build_topology()` CAVEAT this
investigation follows on from), `_find_checker()` can elect one of
those neighbor cores. Since `is_designated_checker()` is only ever
asked by cores that DO climb the domain, an invalid election means NO
core ever answers "yes" for that domain that cycle -- not "this core's
turn was skipped," but "this domain's periodic pass did not run at
all," silently, indefinitely (until idle-cpu-ordering luck changes it).

### STEP 1 — measured BEFORE (no code change)

`development/topology_audit/task7_checker_election_audit.py`: baseline
+ burst-aware, penalty=0, `stacked_medium`/`stacked_high`/`rate3.0_s12`/
`bursty_high_s64`, 10 seeds each (30000-30009). Read-only
instrumentation (monkey-patches `is_designated_checker` for the
duration of each run, restored after -- same pattern as
`diagnostics.py`), no simulator logic touched.

A single-seed spot check on `stacked_medium` alone showed 0% invalid --
misleadingly clean, because `Domain.cores()`'s iteration order happens
to list the home node's own cores first (an accidental side effect of
`build_topology()`'s `span_nodes = [nodes[i]] + neighbors` ordering),
so `idle[0]` picks a home core whenever ANY of them is idle. The bug
only shows up once the fallback path (nobody in the span idle -> lowest
core_id globally) actually fires, which needs real load. Checked
directly before trusting the light-load result: `stacked_high` showed
47.8% invalid, `bursty_high_s64` showed 70.6% invalid on that same
single seed -- confirming this is load-dependent, not absent.

**Full 80-run result** (2 schedulers x 4 workloads x 10 seeds,
`checker_audit_summary.csv`):

| level | events | invalid | % invalid |
|---|---|---|---|
| pair | 468,512 | 0 | 0.0% |
| node | 144,210 | 0 | 0.0% |
| onehop | 48,408 | 11,097 | 22.9% |
| machine | 36,525 | 8,478 | 23.2% |

Pair and node levels are exactly 0% -- `home_children` defaults to ALL
children there (pairs/nodes never overlap), so this bug structurally
cannot occur below onehop. Per-domain rates vary sharply within a
level: `onehop0`/`machine0` sit at 3.4%/5.4% (their home cores happen
to win the iteration-order coincidence above more often) vs. 28-31% for
`onehop1-3`/`machine1-3`. **0 domains were "fully starved"** (every
domain got a valid check at least once across the whole run) -- this is
a large, real efficiency/correctness loss, not a permanent freeze.

### STEP 2 — `checker_model="kernel"`, opt-in (`simulator/LoadBalancer.py`)

Ports the election above: `_find_group_containing(domain.groups(),
from_core)` for the local group (always unambiguous here, unlike
`Placement.py`'s fixed-root bug from the previous task -- `periodic_
balance()`'s `d` already comes from `core.parent`'s own climb, so
`from_core` is always genuinely home to `domain`); `_balance_mask()`
restricts to cores whose own `domain_chain()` passes through that exact
group object; degenerate case (`group` is a raw `Core`, at the pair
level) returns `[group]` -- matching real Linux, where the SMT-level
group is a single CPU and `should_we_balance()` trivially returns true
for whichever CPU asks (verified directly: pair-level `is_self` rate
under `checker_model="kernel"` is 100%, i.e. BOTH siblings
independently pass, not one elected over the other). `_is_core_idle()`
checks all of `core.parent.cores()` (the real SMT-sibling check).
`share_cpu_capacity = (domain.level == Domain.LEVEL_PAIR)` -- the only
level with `SD_SHARE_CPUCAPACITY` in this topology, verified already in
Fix C's ledger entry.

**`_find_checker(domain, from_core=None)`: legacy behavior is the
exact fallback whenever `checker_model != "kernel"` OR `from_core is
None`.** The burst path (`BurstScheduler.on_task_placed()`) calls
`self._find_checker(domain)` with no second argument -- per
instruction, left untouched, and its behavior is IDENTICAL regardless
of `checker_model`, since it never passes `from_core`.

**Q: is "the CPU's own child group" the right approximation for this
4-node ring, per `build_overlap_sched_groups()`/`find_descended_
sibling()` (`topology.c:1315-1420`)?** Yes, with one condition that
already holds here: it must be evaluated via the asking core's OWN
anchored `domain_chain()` (as `periodic_balance()` already does),
never a fixed foreign root. Real Linux's `sd->groups` is ALSO always
per-CPU-oriented -- every CPU has its own `sd` copy, so "the CPU's own
child group" is not an approximation there, it's the actual mechanism.
`find_descended_sibling()` only matters when a sibling's child-domain
span extends OUTSIDE the domain being built (diameter>=3 topologies,
per the kernel's own linear-chain example in that function's comment)
-- this sim's ring never hits that case: every machine domain's reused
far-onehop is, by `build_topology()`'s own construction, already a
subset of the machine's span. Not needed here, but worth knowing if
the ring topology is ever generalized to more hops.

**Q: should the burst path change too, for consistency?** Not done
(per instruction), but likely yes on the same reasoning: `_balance_
domain(domain, checker, ...)` uses `checker` to find "the local group"
for imbalance purposes (`_find_group_containing`), and a checker
elected from a non-climbing neighbor branch would misrepresent which
side is actually "local" to the arriving burst -- the same structural
risk as periodic's skipped passes, just manifesting as a possibly-wrong
imbalance direction instead of a silently-skipped pass. Unlike
periodic's fix, this changes actual scheduling decisions for every
burst-aware run, not just diagnostics, and deserves its own dedicated
verification (ablation, not just an audit) -- a separate follow-up.

### STEP 3 — measured AFTER, `checker_model="kernel"`, same seeds, baseline only

`development/topology_audit/task7_checker_election_after.py`.
**Invalid-checker rate: exactly 0.0% at every level** (pair, node,
onehop, machine -- `checker_audit_after_perseed.csv`), confirming the
fix works structurally, not just for the cases it was designed around.

**Legacy vs. kernel, paired by seed, penalty=0 (`checker_model_
compare_perseed.csv`):**

| metric | workload | legacy | kernel | change | sign_p |
|---|---|---|---|---|---|
| p95_wait | stacked_medium | 14.60 | 13.70 | -6.2% | 0.18 (n.s.) |
| p95_wait | stacked_high | 27.95 | 24.43 | -12.6% | 0.34 (n.s.) |
| p95_wait | rate3.0_s12 | 18.20 | 18.83 | **+3.5%** | 0.34 (n.s.) |
| p95_wait | bursty_high_s64 | 65.47 | 55.16 | **-15.8%** | **0.002**, 10/10 wins |
| migrations | all 4 | -- | -- | **+24% to +148%** | **0.002** every workload |
| cores_scanned | all 4 | -- | -- | **-20% to -47%** | **0.002** every workload |

More migrations AND less scanning work together make sense: legacy's
wasted (invalid-checker) opportunities never reach the backoff-update
code (`periodic_balance()` only grows `balance_interval` inside the
`if is_designated_checker` block), so those core+domain pairs keep
re-checking at `min_interval` forever without ever backing off --
real, if lower-value, overhead. `checker_model="kernel"`'s checks
mostly succeed, so backoff works as designed, and the extra migrations
come from onehop/machine levels actually running instead of being
silently skipped. One workload (`rate3.0_s12`) shows a small,
non-significant p95_wait REGRESSION -- not explained further here, a
candidate follow-up question if `checker_model="kernel"` is pursued.

**Not done, per instruction:** the default was NOT changed
(`checker_model="legacy"` remains it), and neither the calibration
grid nor the confirmation run was re-run under the new model. This
entry is a fidelity-gap finding and its opt-in fix, not a new baseline.

## 2026-09-29 — Task 8 pre-audit: full 13-area Linux-fidelity audit (no code changes)

Full audit requested BEFORE the Task 8 grid/confirmation re-run, explicitly
audit-and-measure-only (no simulator logic changed). Result:
`docs/FIDELITY_AUDIT.md` (one row per mechanism: sim function file:line |
Linux function file:line | status | bias direction | affects), covering
all 13 requested areas against fresh v7.2 kernel source (`fair.c`,
`core.c`, `topology.c`, `sched.h`, `pelt.c` -- `pelt.c` and `core.c` newly
fetched this session; `fair.c`/`topology.c`/`sched.h` reused from Task 7's
cache). Full report and citations live in that file, not duplicated here;
this entry is the ledger pointer plus the headline numbers.

**Three UNDOCUMENTED MISMATCHES were cheaply measurable** via monkeypatch
(`development/fidelity_audit/task8_pre_audit_impact_measurements.py`, no
simulator file edited, 10 seeds 50000-50009, `stacked_high` +
`bursty_high_s64`, baseline only, `checker_model="kernel"` held constant):

- **Missing `busy_factor` (real: x16 periodic-check interval scaling when
  the checking core is busy, `get_sd_balance_interval()`, `fair.c:13565-
  13586`, `sd_init()` busy_factor=16, `topology.c:1958`) -- LARGEST finding
  in the audit.** Correcting it costs +26% to +48% p95_wait, buys -29% to
  -69% fewer migrations, both highly significant (sign_p <= 0.022,
  wilcoxon_p <= 0.008) at both tested workloads. The current simulator's
  periodic path checks busy cores ~16x more often than real Linux would --
  meaning the CURRENT gap between burst-aware and baseline periodic is
  very likely an UNDER-estimate of the true gap in real Linux (an
  artificially fast periodic baseline can only narrow, not widen, an
  observed "burst-aware reacts earlier" effect). Ranked #1 to fix before
  the re-run.
- **`!idle` out_balanced gate** (`sched_balance_find_src_group()`,
  `fair.c:12871-12881`: on a busy checking CPU, real Linux refuses to
  balance at all unless busiest is genuinely overloaded) -- measured
  **zero effect** at both workloads (byte-identical across all 10 seeds
  each): busiest was essentially always `GROUP_OVERLOADED` whenever a
  periodic check actually ran under this much sustained/bursty load, so
  the gate never triggered. Real gap, dormant at these intensities;
  documented rather than ranked for an immediate fix.
- **`NUMA_IMBALANCE_MIN`/`NUMA_DST_BUSY_THRESHOLD`** (previously flagged
  in-code as "never opened this session, unverified stand-ins" -- now
  opened: real values are 2 and a topology-derived 3 for this sim's
  4-node/8-core-per-node/3-node-onehop layout, `fair.c:2177`,
  `topology.c:2870-2934`; the sim used 32 and 2, i.e. 16x too forgiving)
  -- measured **no significant effect** at either workload (small,
  non-significant swings at stacked_high; byte-identical at
  bursty_high_s64). Verified-wrong by a large factor but low measured
  leverage for these specific high-intensity conditions -- recommend
  fixing anyway (one-line, zero-risk) but don't expect it to move
  headline numbers.

**Also found, not cheaply measurable without new simulator state (marked
"needs code change to measure" in the full report, NOT implemented here):**
missing cache-hot/`task_hot()` gate on every migration path (`fair.c:
10291-10329`, `sysctl_sched_migration_cost`=0.5ms) -- ranked #2 to fix,
since it applies to every migration everywhere and can only bias toward
MORE migrations than real Linux, never fewer; `update_sd_pick_busiest()`'s
type-first-then-metric busiest selection vs. the sim's load-only `max()`
(`fair.c:11918-11990` vs `LoadBalancer.py:339`) -- entangled with the
already-accepted 3-type `group_classify()` simplification, documented not
fixed; NOHZ/idle-load-balancer tickless modeling (`fair.c:13920-14254`);
`sched_balance_newidle()`'s `avg_idle`/`max_newidle_lb_cost` cost-budget
gating (already partly documented in-code, magnitude now cited exactly,
`fair.c:14347-14420`); `sched_balance_find_src_rq()`'s `migrate_util` case
selecting by util rather than load (`fair.c:13024-13039` vs
`_migrate_util`'s load-based pick).

**Correction to an earlier-session finding, made in `docs/FIDELITY_AUDIT.
md` §11** (not a new bug, not a retraction -- see that file for the full
wording): the Phase 10 "Placement.py machines[0]-fixed-root" description
was re-checked against `Main.py` this session and found to describe the
mechanism imprecisely -- there is only ONE `machine` object in this sim
(no `machines[]` array to index wrong). The real, still-unfixed asymmetry
is in how `build_topology()`'s ring construction anchors node1/node3
`domain_chain()` climbs to a fixed-perspective onehop domain, not an
array-indexing bug. The underlying finding (the asymmetry is real and
unfixed) stands; only its description is corrected here.

**checker_model="kernel" (Task 7) reconfirmed complete for the periodic
path** by re-reading current `LoadBalancer.py`, but **the burst path was
found to NOT benefit from it at all** (`BurstScheduler.py:105` calls
`_find_checker(domain)` with no `from_core`, which always takes the
legacy whole-span-election branch regardless of `checker_model` --
`LoadBalancer.py:262`'s `from_core is None` guard). Already flagged as a
known follow-up in Task 7's entry; not actioned here (no code changes).

Ranked recommendation for the Task 8 re-run (full reasoning in
`docs/FIDELITY_AUDIT.md` §14): **fix busy_factor** (Rank 1, large +
significant + directly relevant to the paper's central claim) and
**consider fixing cache-hot** (Rank 2, not yet measurable) before
re-running the grid/confirmation; fix `NUMA_IMBALANCE_MIN` regardless
(Rank 3, cheap/zero-risk, low measured leverage); document the rest
(`update_sd_pick_busiest` ordering, the `!idle` gate, NOHZ/ILB, newidle
cost-gating, `migrate_util` rq-selection) as known simplifications rather
than fixing before the re-run.

**Not done, per instruction:** no simulator logic was changed, no fixes
were implemented, and neither the calibration grid nor the confirmation
run was re-run. This entry is the audit's findings, not a new baseline.

## 2026-09-29b — Task 8 pre-audit corrections pass (6 steps, 1 tracking-only code change)

Six directed corrections/verifications to the 2026-09-29 audit above,
requested before any fix is implemented. Still audit-only except one
explicitly authorized tracking-only addition (Step 4). Full detail and
citations in `docs/FIDELITY_AUDIT.md` (updated in place); this entry is
the ledger pointer plus headline outcomes.

**STEP 1 -- RETRACTION OF A RETRACTION.** The 2026-09-29 entry above
claimed the "Placement.py machines[0]-fixed-root" Phase-10 finding was a
mischaracterization ("there is only ONE machine object ... no machines[]
array"). **That claim was wrong** -- made without actually reading
`Topology.build_topology()`, only `Main.py`'s single-variable unpacking.
Re-read this session: `build_topology()` builds a `machines = []` list
with one `Domain` PER NODE (`Topology.py:434-456`) and returns only
`machines[0]` (`Topology.py:458`) to every caller -- exactly what the
ORIGINAL Phase-10 finding said. `home_children` correctly anchors each
onehop's `.parent` to its OWN node (`Topology.py:406-418`,
`home_children=[nodes[i]]`), so `domain_chain()`-based BALANCING is
unaffected, as both the original finding and `Topology.py`'s own
in-code CAVEAT (`Topology.py:369-387`, already present before this
audit task started) say -- the bug is PLACEMENT-only:
`machines[0].children == [onehop0, onehop2]`, and node1/node3-forked
tasks' first-match local domain is always `onehop0`, never their own.
**The original Phase-10 finding stands, unaltered. Last session's
"correction" of it is itself now retracted** -- this is a
correction-of-a-correction, logged per ledger discipline rather than
silently fixed in place. Lesson for future sessions: a claim about what
a function returns must be verified by reading THAT function, not
inferred from one call site's variable naming.

**STEP 2 -- `TIME_SLICE` vs the EFFECTIVE `base_slice`.** Fetched
`get_update_sysctl_factor()`/`update_sysctl()`/`sched_init_granularity()`
(`fair.c:192-226`). Confirmed: under the default `SCHED_TUNABLESCALING_
LOG`, `sysctl_sched_base_slice` is scaled at boot by `1+ilog2(min(ncpus,
8))` -- for any machine with >=8 online CPUs (including this sim's 32),
`factor=4`, so the EFFECTIVE base slice is **2.8ms**, not the raw 0.7ms
figure previously cited. Sim's `TIME_SLICE=4` is ~1.43x the real
effective value, not ~5.7x as previously stated. Also corrected: this
is NOT neutral for the paper's metrics -- `TIME_SLICE` is literally
`Core.run()`'s dispatch quantum (how long a core holds its current task
before re-picking), so it directly sets how long a queued task waits
behind a running one, i.e. it reaches p95_wait, this audit's own primary
metric. `docs/FIDELITY_AUDIT.md` §10 rewritten accordingly; not
independently measured (a `TIME_SLICE`-scaling toggle run the same way
as Toggle A/B/C would be needed).

**STEP 3 -- 1ms ticker reclassified MATCH (timing).** Re-examined
whether `rq->next_balance`'s single-pointer gate produces a genuine
timing difference from this sim's every-tick-every-core walk, or only a
work-accounting one. Confirmed the latter: `next_balance` is
constructed as the MINIMUM, over every domain, of that domain's own
due-time (`fair.c:13775-13835`), so `jiffies>=rq->next_balance` becomes
true at exactly the moment the soonest-due domain is due -- never later
than the naive scheme finds it -- and the softirq handler, once it
fires, walks every domain checking its OWN interval
(`fair.c:13798-13816`) exactly like `periodic_balance()`'s own loop.
Reclassified from UNDOCUMENTED MISMATCH to **MATCH (timing)**; only the
per-tick WORK differs (O(levels) every tick per core here vs. O(1) on
most real-Linux ticks). Does NOT affect the separate, still-real
busy_factor finding (§1 row 2) -- that's about the VALUE of the
interval, not whether the check happens every tick.

**STEP 4 -- cache-hot scope narrowed and MEASURED.** Verified
`task_hot()`'s exceptions before measuring: `__sched_fork()` sets
`p->se.exec_start=0` at fork (`core.c:4568`); only `update_stats_curr_
start()` (`fair.c:2150-2156`, on actually being picked to run)
overwrites it with a real timestamp -- so a freshly-forked, never-run
task is NEVER cache-hot in real Linux (huge `delta`), confirming the
original §7 framing ("applies to literally every migration") overstated
the gap's scope. Added a TRACKING-ONLY `Task.last_ran_until` field
(`Task.py`, set in `Core.run()`) -- the one simulator-file edit in this
whole audit, explicitly authorized for this step. **Verified
byte-identical before/after**: a throwaway fingerprint script (12 runs,
2 balancers x 2 workloads x 3 seeds, full migration-event sequences +
summary metrics, sha256) produced the identical hash
(`fe3a33c37f180f...`) before and after the addition, confirming zero
decisions changed; the script itself was scratch and not committed.
Measured (`development/fidelity_audit/task8_step4_cache_hot_scope.py`,
baseline + burst-aware, 5 workloads, 10 seeds 50000+): only **2.8%-
10.1%** of migrations across workloads would actually be refused as
cache-hot -- most sim migrations are either already-cold or, especially
on the burst path (88-99.8%), of tasks that have NEVER run since being
queued, which real Linux would never block either. An upper bound (does
not account for `task_hot()`'s own active-balance/NUMA-preferred/
nr_balance_failed exceptions). This is an order of magnitude smaller
than the un-measured "applies to every migration" framing this finding
originally got -- §14's Rank 2 demoted accordingly (see below).

**STEP 5 -- a missed §8 row.** Checked directly: within
`calculate_imbalance()` (`fair.c:12577-12753`), there is EXACTLY ONE
call to `adjust_numa_imbalance()` (`fair.c:12693`), inside the
`group_has_spare` branch only -- the "both overloaded" migrate_load
branch (`fair.c:12718-12753`) never calls it. But
`LoadBalancer._balance_domain`'s GENERIC migrate_load path
(`LoadBalancer.py:386-387`) calls `_adjust_numa_imbalance()`
unconditionally whenever `domain.is_numa`, reached from BOTH the
has-spare-legacy-fallthrough case AND the "both overloaded" case --
meaning the sim forgives small NUMA imbalances in the overloaded-vs-
overloaded scenario that real Linux never forgives there at all. New
row added to `docs/FIDELITY_AUDIT.md` §8; the previously-MATCH-labeled
"both overloaded" row in §5 now carries a caveat pointing to this.

**STEP 6 -- §14 Rank 1 framing corrected.** Removed the "directly in
the user's interest" justification for fixing `busy_factor` before the
re-run. The reason to fix it is fidelity -- the simulator claims to
model `get_sd_balance_interval()` (`fair.c:13565-13586`) and currently
implements only one of its two multiplicative factors -- not which
direction the fix happens to move any particular comparison.

**Not done, per instruction:** no fix was implemented for any of the
underlying mismatches; the calibration grid/confirmation run was not
re-run. The `last_ran_until` field is tracking-only and read by nothing
but this audit's own measurement script.

## 2026-09-29c — Task 8 pre-registration (committed BEFORE any code change)

Six fidelity fixes from `docs/FIDELITY_AUDIT.md` are being implemented
behind opt-in flags, measured on the BASELINE ONLY, then made the
defaults. This entry pre-registers the plan in full before any of it
happens, per this project's standing discipline. **Burst-aware is not
run anywhere in this task** -- no peeking at the baseline-vs-burst-aware
comparison before the actual v2 re-run (a separate, future, explicitly
user-triggered task).

**Baseline changes, each behind its own flag, all becoming defaults by
the end of this task:**

1. `checker_model="kernel"` -- `should_we_balance()` (Task 7, already
   implemented; only the DEFAULT changes in this task).
2. `busy_factor=16` -- `get_sd_balance_interval()` (`fair.c:13565-13586`).
3. `placement_root="own"` -- the entry core's own machine domain, not
   the fixed `machines[0]` (`docs/FIDELITY_AUDIT.md` §11).
4. `cache_hot=True` -- `task_hot()`/`can_migrate_task()` (`fair.c:
   10291-10329`, `10817-10832`), `migration_cost` = 0.5ms.
5. `numa_fix=True` -- `NUMA_IMBALANCE_MIN=2`, `imb_numa_nr=3` (this
   topology's derived value, `docs/FIDELITY_AUDIT.md` §8), and the
   NUMA adjustment removed from the generic migrate_load path
   (`_balance_domain`) -- real `calculate_imbalance()` only ever calls
   `adjust_numa_imbalance()` from the `group_has_spare` branch.
6. `TIME_SLICE=2.8` -- the effective, boot-scaled `base_slice` for a
   32-CPU machine under default tunable scaling (`docs/FIDELITY_AUDIT.
   md` §10), NOT the module constant `TIME_SLICE=4`.

**Burst path: UNCHANGED.** `BurstDetector`, its threshold grid
(calibrated 2026-09-27i), and its own checker election
(`_find_checker()` without `from_core` -- first idle core in the
domain's whole span) are the paper's own novel mechanism, not a Linux
analog being corrected for fidelity. None of the six fixes above touch
`BurstScheduler.py`; `_balance_domain`'s shared internals (busy_factor
does not apply to burst's own trigger, but cache_hot/numa_fix DO apply
to any migration the burst path performs through the shared pipeline,
same as periodic/newidle).

**Selection rule: UNCHANGED from the 2026-09-27h/i calibration.** (1) no
significant harm (the corrected `harms > wins` sign-test rule) on any
metric, workload, or penalty; (2) among the harm-free configurations,
the largest mean `stacked_medium` + `stacked_high` p95_wait reduction,
averaged over both penalties (0.0, 2.0).

**Seeds, kept disjoint from every prior seed range used in this repo:**
grid stays at its existing 10000+ range (unchanged, no re-run needed for
the grid itself beyond what Step 4 prepares); confirmation gets a FRESH
40000+ range for the v2 re-run (20000+ was the original confirmation
run, 30000+ was Task 7's checker-election audit, 50000+ was the Task 8
fidelity-audit's own measurement scripts, 60000+ is this task's own
Step-2 baseline-only ablation).

**Commitment, stated before seeing any Step 2 result:** whatever the v2
re-run shows is reported as-is, including if burst-aware's measured
benefit shrinks, disappears, or reverses relative to the original
confirmation run. This is a fidelity correction, not a search for a
result.
