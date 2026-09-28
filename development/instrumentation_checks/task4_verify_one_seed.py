"""
Slimmed Task 4: verify every new diagnostic on ONE bursty/high seed before
committing to a full grid. Calls Main.run_simulation() directly (not
paired_compare.run_pair()) for each scheduler separately, since the live
samplers (diagnostics.py) each need their OWN fresh state dict per run --
run_pair() shares one `extra_processes` list across both of its calls, so
this direct-call approach avoids the two runs' idle/queue-imbalance state
bleeding into each other. Task 5's actual paired grid will need run_pair
extended to hand each side its own state (a per-call factory), not solved
here since this step is diagnostic verification only, not a real
experiment.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "simulator"))
from Main import run_simulation
from LoadBalancer import LoadBalancer
from BurstScheduler import BurstAwareLoadBalancer
import diagnostics

PROFILE = "bursty"
INTENSITY = "high"
SEED = 1200  # bursty/high's usual base seed


def run_one(cls, seed):
    idle_state = {}
    imbalance_state = {}
    extra = [
        diagnostics.idle_while_waiting_sampler(idle_state),
        diagnostics.queue_imbalance_sampler(imbalance_state),
    ]
    m, b, gt, migs, logger, plan = run_simulation(
        PROFILE, cls, intensity_level=INTENSITY, seed=seed,
        balancer_kwargs={"migration_penalty": 0.0, "newidle_mode": "transition"},
        extra_processes=extra,
    )
    s = m.summary(balancer=b, ground_truth_bursts=gt, migration_events=migs)
    return dict(metrics=m, summary=s, balancer=b, ground_truth_bursts=gt,
                migration_events=migs, idle_state=idle_state, imbalance_state=imbalance_state)


def main():
    baseline = run_one(LoadBalancer, SEED)
    burst_aware = run_one(BurstAwareLoadBalancer, SEED)

    print(f"=== {PROFILE}/{INTENSITY} seed={SEED}, transition mode, penalty=0 ===")
    print(f"ground_truth_bursts: {baseline['ground_truth_bursts']}")
    assert baseline["ground_truth_bursts"] == burst_aware["ground_truth_bursts"], (
        "ground truth differs between runs -- workload isolation regression!"
    )

    for name, run in [("baseline", baseline), ("burst_aware", burst_aware)]:
        s = run["summary"]
        print(f"\n--- {name} ---")
        print(f"makespan={s['makespan']:.1f}  makespan_excess={s['makespan_excess']:.2f}  "
              f"avg_wait={s['avg_wait']:.3f}  p95_wait={s['p95_wait']:.3f}  p99_wait={s['p99_wait']:.3f}  "
              f"avg_slowdown={s['avg_slowdown']:.3f}  p95_slowdown={s['p95_slowdown']:.3f}")

        idle_t = run["idle_state"]["idle_while_waiting_time"]
        print(f"idle_while_waiting_time={idle_t:.1f} ms")

        qsum = diagnostics.summarize_queue_imbalance(
            run["imbalance_state"]["queue_imbalance_samples"], run["ground_truth_bursts"],
        )
        print(f"queue_imbalance: overall mean={qsum['overall_mean']:.3f} max={qsum['overall_max']} | "
              f"during-burst mean={qsum['during_burst_mean']:.3f} max={qsum['during_burst_max']} "
              f"(n_samples={qsum['n_samples']}, during_burst={qsum['n_during_burst_samples']})")

        if name == "burst_aware":
            b = run["balancer"]
            print(f"detector_fires={b.detector_fires}  burst_balance_attempts={b.burst_balance_attempts}  "
                  f"burst_triggered_migrations={b.burst_triggered_migrations}")
            dq = diagnostics.detector_quality(b.detector_fire_times, run["ground_truth_bursts"])
            print(f"detector_quality: n_bursts={dq['n_bursts']} n_fires={dq['n_fires']} "
                  f"n_detected={dq['n_detected']} recall={dq['recall']} precision={dq['precision']} "
                  f"avg_detection_latency={dq['avg_detection_latency']}")

    import statistics
    lt = diagnostics.lead_time_per_burst(
        baseline["migration_events"], burst_aware["migration_events"], baseline["ground_truth_bursts"],
    )
    print(f"\nlead_time_per_burst: n_bursts={lt['n_bursts']}  "
          f"no-baseline-migration={lt['n_bursts_no_baseline_migration']}  "
          f"no-burst-tagged-migration={lt['n_bursts_no_burst_tagged_migration']}  "
          f"no-any-burst_aware-migration={lt['n_bursts_no_any_burst_aware_migration']}")
    print(f"  vs_burst_tagged: {lt['vs_burst_tagged']}"
          + (f"  mean={statistics.mean(lt['vs_burst_tagged']):.3f}" if lt['vs_burst_tagged'] else ""))
    print(f"  vs_any_migration: {lt['vs_any_migration']}"
          + (f"  mean={statistics.mean(lt['vs_any_migration']):.3f}" if lt['vs_any_migration'] else ""))


if __name__ == "__main__":
    main()
