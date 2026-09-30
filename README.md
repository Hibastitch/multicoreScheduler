# multicoreScheduler

## What this is

This is a [SimPy](https://simpy.readthedocs.io/) discrete-event simulator of Linux's EEVDF process scheduler and its load-balancing pipeline (fork-time placement, periodic balancing, and newly-idle balancing), running on a simulated 32-core machine: 4 NUMA nodes on a ring, each node built from SMT (hyperthread) pairs, with the kernel's real `sched_domain` hierarchy (pair → node → one-hop → machine) modeled on top. The baseline scheduler — placement, periodic balancing, newly-idle balancing, the EEVDF run queue itself — is not a loose approximation; it was built and then individually audited against the actual Linux v7.2 kernel source (`kernel/sched/fair.c`, `core.c`, `topology.c`), file:line cited throughout `docs/FIDELITY_AUDIT.md`, with six real fidelity gaps found and fixed. On top of that verified baseline, the project adds exactly one new mechanism: a **burst-aware trigger** — a detector watching two observable signals (arrival rate and queue-growth on a pair-level domain) plus a machine-wide idle-capacity check, which together decide when to call the kernel's own existing balancing walk _early_ instead of waiting for the next periodic tick. The mechanism is evaluated the same way an A/B test would be: a pre-registered calibration grid picks one configuration on 9 workloads, then a completely fresh set of seeds confirms (or doesn't) that the picked configuration actually works — including on 7 workloads the grid never saw.

## Headline result

From the real, committed `final_results/2_confirmation/results_task6_confirmation_v4_MAIN_TABLE.csv` (16 workloads x 3 migration-cost penalties = 48 cells, n=30 paired seeds each). The penalty is the simulated cost charged per migration: 0 ms, 0.5 ms (≈ Linux's `sysctl_sched_migration_cost`), and 2 ms as a deliberate stress test. The confirmed configuration (`queue_growth_threshold=8, arrival_rate_threshold=1.5, combine="or"`, plus the machine-wide idle check) is **harm-free on all 48 cells** — no metric significantly worse than baseline under the paired sign test over the 30 seeds, on any workload or penalty (the harm rule is in `docs/PIPELINE.md`). Where it fires, it helps: p95 wait time drops 14.7%-30.9% on four dense-burst workloads (`stacked_medium`, `stacked_high`, `rate1.5_s12`, `rate3.0_s12`, all `sign_p<0.0001`). On 11 workloads it is silent — identical to baseline, not worse (`p95_wait_pct=0.0000%` exactly, every penalty): the detector's thresholds are never crossed at all on 10 of them, and on the 11th (`bursty_high_s24`) they're crossed only 0.03-0.07 times per run, too rarely to move any metric. On `bursty_high_s64` it is neutral: the detector fires heavily there, but the idle check skips almost every walk (the burst alone saturates the machine, so no core is ever idle while it's landing) — the one measurable effect is ~18% fewer migrations than the same detector run without the check, not a wait-time change. The original, unfiltered detector (`q2_a0.8_or`, no idle check) helps on more workloads — a significant `p95_wait` reduction on 10 vs the confirmed configuration's 4 — but is harmful on 3 (`rate0.5_s4`, `heavy_tail_high`, `bursty_high_s64`); the confirmed configuration trades that broader coverage for no harm anywhere. Full numbers: `docs/NOTEBOOK.md`'s `2026-09-30g` entry.

## The mechanism, in one paragraph

`BurstDetector` watches two observable signals on each pair-level domain — arrival rate (tasks/ms in a sliding window) and queue growth (how much a core's run queue has grown over a sliding window) — and fires when _either_ crosses its threshold (`combine="or"`): `queue_growth_threshold=8` or `arrival_rate_threshold=1.5`. When it fires, `BurstAwareLoadBalancer` runs one more check before doing anything: `burst_idle_check` asks whether any core anywhere on the 32-core machine is currently idle — `Core.is_idle()`: no task currently running *and* nothing queued (`current_task is None and not self.rq`) — via a loop over all cores that stops at the first idle one (behaviorally identical to `any(...)`; it also counts each core read for the cost measurement). If none is idle, the walk is skipped entirely (counted, not acted on). If one is, the balancer climbs the same `domain_chain` (pair → node → one-hop → machine) the kernel's own periodic balancer would eventually climb on its own schedule anyway, calling the unchanged `_balance_domain()`/`_find_checker()` logic at each level — no new destination rule, no gap arithmetic, just an _earlier_ call to code that already exists and is already audited.

### Cost of the idle check in a real kernel

The idle-core scan only runs when the detector actually fires and passes its cooldown (`BurstScheduler.on_task_placed()`, called once per task arrival, not on every tick) — bounded in practice by the v4 confirmation's own fire counts, e.g. ~151/run on `stacked_high`. An exact equivalent is a scan of `idle_cpu()`/`available_idle_cpu()` over all CPUs: O(N) but cheap per-CPU reads (N=32 here), and Linux already runs exactly this shape of scan on its own wakeup path — `select_idle_cpu()` (`kernel/sched/fair.c:8535-8596`) walks candidate CPUs with `for_each_cpu_wrap()`, calling `__select_idle_cpu()` → `choose_idle_cpu()` (`kernel/sched/fair.c:7763-7767`) → `available_idle_cpu()` per candidate, the identical per-CPU test this simulator uses. A cheaper, approximate alternative exists too: testing `nohz.idle_cpus_mask` (`kernel/sched/fair.c`) instead of scanning is fast, but that mask only tracks CPUs that have gone fully tickless-idle and is documented in the kernel source itself as lagging real idle entry/exit, not a live signal — so a real implementation would be trading exactness for cost, not getting both for free.

Measured on the published runs (not estimated, all 48 confirmation cells covered): where the check runs at all, it adds 1-1085 cheap `Core.is_idle()` reads per run — 0.002%-1.91% of that same run's own baseline `sched_cores_scanned` — reported separately from `cores_scanned` itself, not folded into it. Full table and the safety check verifying these are the identical published runs: `final_results/3_idle_check_cost/`.

> **Reproduce the headline result** (the confirmed configuration is already selected — `final_results/1_calibration_grid/selected_config_v4.json` is committed, so this reproduces the _confirmation_ run, not the calibration grid; same loop as `docs/PIPELINE.md`'s Confirmation step):
>
> ```powershell
> cd final_results/2_confirmation
> $env:TASK10_V4=1
> foreach ($wl in @(
>     "stacked_low","stacked_medium","stacked_high","bursty_high_s24","bursty_high_s64",
>     "heavy_tail_high","rate0.5_s4","rate0.5_s12","rate0.75_s4","rate0.75_s12",
>     "rate1.0_s4","rate1.0_s12","rate1.5_s4","rate1.5_s12","rate3.0_s4","rate3.0_s12"
> )) {
>     python task6_confirmation_run.py $wl
>     if ($LASTEXITCODE -ne 0) { Write-Error "FAILED: $wl"; break }
> }
> (Get-ChildItem "results_task6_confirmation_v4_*_summary.csv").Count   # expect 16
> python task6_confirmation_analyze.py
> python task6_confirmation_tables.py
> ```
>
> Rough estimate, not precisely re-measured (see `docs/PIPELINE.md`): a multi-hour background job, dominated by the 16-workload loop. Result: `results_task6_confirmation_v4_MAIN_TABLE.csv` (the Headline result above is read straight from it).

## Repository layout

```
simulator/              the scheduler itself — every module Main.run_simulation() wires together
final_results/
  1_calibration_grid/    Step 1 of the pipeline: pick a detector configuration
  2_confirmation/        Step 2: confirm the pick on fresh seeds and workloads
  3_idle_check_cost/     the idle check's own scanning cost, measured on the published runs
development/             one-off diagnosis/verification scripts — real findings, not the final numbers
superseded/              early results known to be invalid or replaced — kept for provenance, never cite
docs/                    pipeline docs, project history, and the full dated lab notebook
tests/                   automated invariant checks (bookkeeping, not scheduling-fidelity)
```

Full per-file breakdown of every folder: `docs/FILES.md`.

## Workloads

| workload                             | how the burst arrives                                                                                                                                                                                            | size    | rate/gap              | what it tests                                                                                                                                                                       |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `stacked_low`/`_medium`/`_high`      | **stacked**: every task in a burst is force-placed onto the SAME randomly-chosen core, bypassing placement entirely — the deliberately worst case for a balancer, since nothing at arrival time spreads the load | 4/12/30 | 0.5/1.5/3.75 tasks/ms | low/medium/high burst intensity, no arrival-rate/size confound                                                                                                                      |
| `bursty_high_s24`                    | **bursty**: every task in a burst shares one entry core but goes through NORMAL top-down placement (still allowed to spread)                                                                                     | 24      | 3.75 tasks/ms         | a real, less-adversarial burst shape at moderate size                                                                                                                               |
| `bursty_high_s64`                    | bursty, same as above                                                                                                                                                                                            | 64      | 3.75 tasks/ms         | `64 > 32` cores — the burst alone saturates the entire machine, so no core is ever idle while it's landing — this is why `burst_idle_check` mostly skips it (see `docs/HISTORY.md`) |
| `heavy_tail_high`                    | no burst; heavy-tailed CPU-time distribution at high intensity                                                                                                                                                   | —       | —                     | load imbalance from task-SIZE variance, not arrival clustering                                                                                                                      |
| `rate{0.5,0.75,1.0,1.5,3.0}_s{4,12}` | stacked (as above), varying arrival rate independently of size                                                                                                                                                   | 4 or 12 | 0.5-3.0 tasks/ms      | isolates arrival rate from burst size — the calibration grid only ever saw `rate0.5`/`rate3.0`; `rate0.75`/`rate1.0`/`rate1.5` are confirmation-only, unseen by the selection       |

Grid workloads are the 9-workload subset: `stacked_{low,medium,high}`, `bursty_high_s24`, `bursty_high_s64`, `rate0.5_s4`, `rate0.5_s12`, `rate3.0_s4`, `rate3.0_s12`. Confirmation adds `heavy_tail_high` and the three untested rates (`0.75`, `1.0`, `1.5`) at both sizes, for 16 total — see `docs/PIPELINE.md` for both steps' exact commands.

## Known limitations

One line each; full explanation of each (including audit citations) in `docs/LIMITATIONS.md`:

- **No NOHZ / idle load balancer** — every core ticks every cycle; real Linux only wakes idle CPUs in one batched pass.
- **No newly-idle cost budget** — always exactly one attempt per busy→idle edge, no self-measured cost gating.
- **Three-type group classification** (`HAS_SPARE`/`FULLY_BUSY`/`OVERLOADED`), not Linux's eight.
- **No sleeping/blocking tasks** — every task is CPU-bound from arrival to completion.
- **A flat migration-cost assumption** — real Linux has no such parameter at all; `penalty_model="ran_only"` at least charges it only to tasks that have already run.
- **The `_s4` blind spot** — `queue_growth_threshold=8` can never fire on a 4-task burst.
- **The idle check is exact in the simulator** — a kernel implementation must choose between an exact O(N) scan and a cheaper but lagging `nohz.idle_cpus_mask` test; not evaluated here.

This is the fourth version of the mechanism; how it got here (v1-v3, what failed and why) is in `docs/HISTORY.md`.

## Further documentation

- `docs/PIPELINE.md` — every pipeline step (invariant tests, calibration grid, selection, confirmation, analysis), exact commands, the harm rule, the 4 confirmation variants, and how to read an output line.
- `docs/HISTORY.md` — the full task/version history (v1 through v4: what changed, what broke, how it was found, what fixed it) and every seed range this project has ever used.
- `docs/FILES.md` — full per-file breakdown of every folder.
- `docs/LIMITATIONS.md` — the complete known-limitations writeup.
- `docs/FIDELITY_AUDIT.md` — the 13-area, file:line-cited comparison against real Linux v7.2 source.
- `docs/NOTEBOOK.md` — the complete dated lab notebook: every hypothesis, fix, correction, and result, in the order it actually happened.
