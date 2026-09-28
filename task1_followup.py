"""
Follow-up to Task 1: baseline's makespan stdev (53.8) is much larger than
burst_aware's (32.5) at heavy_tail/high/penalty=0 -- check whether
burst_aware wins big on a few seeds while losing slightly on many (a
"tail-clipping" pattern), which the sign test (counts, ignores magnitude)
and the mean/CI (dominated by a few big values) each tell only half of.
Prints sorted per-seed diffs and adds a Wilcoxon signed-rank test (rank-
aware, unlike the sign test) for a fuller picture. Same 30 paired seeds as
task1_heavytail_check.py -- deterministic, so this reproduces those exact
numbers rather than drawing a fresh sample.
"""

from paired_compare import run_pair, seed_base, wilcoxon_signed_rank, format_p

N_REPS = 30
PROFILE = "heavy_tail"
INTENSITY = "high"


def main():
    base = seed_base(PROFILE, INTENSITY)
    for penalty in [0.0, 2.0]:
        rows = [
            run_pair(PROFILE, INTENSITY, base + rep, balancer_kwargs={"migration_penalty": penalty})
            for rep in range(N_REPS)
        ]
        diffs = [r["burst_aware"]["makespan"] - r["baseline"]["makespan"] for r in rows]

        print(f"\n=== heavy_tail/high penalty={penalty}ms makespan diffs (burst_aware - baseline), sorted ===")
        for d in sorted(diffs):
            print(f"  {d:+8.2f}")

        wr = wilcoxon_signed_rank(diffs)
        print(f"Wilcoxon signed-rank: n={wr['n']} (zeros dropped={wr['n_zeros_dropped']})  "
              f"W+={wr['W_pos']:.1f}  W-={wr['W_neg']:.1f}  z={wr['z']:+.3f}  p={format_p(wr['p'])}")

        big_wins = [d for d in diffs if d < -10]  # d = burst_aware - baseline; large negative = big win
        losses = [d for d in diffs if 0 < d]
        print(f"seeds where burst_aware wins by >10ms: {len(big_wins)}/{N_REPS}  "
              f"(sum={sum(big_wins):.1f})")
        print(f"seeds where burst_aware loses (any amount): {len(losses)}/{N_REPS}  "
              f"(sum={sum(losses):.1f})")


if __name__ == "__main__":
    main()
