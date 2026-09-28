"""
Plot p95_wait % change vs requested burst_size, from
results_task6_burst_size_sweep.csv. Marks the zero-crossing and the
point (burst_size=13, confirmed via WorkloadGenerator._plan_stacked_burst
directly) beyond which burst_duration silently caps the ACTUAL emitted
tasks per burst -- every requested size from 16 up is testing the exact
same effective workload, so the plot marks that boundary rather than
implying real saturation.
"""

import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

EFFECTIVE_CAP_SIZE = 13  # confirmed: burst_duration=8, arrival_rate_during_burst=1.5 -> gap=0.667ms -> 13 tasks max

rows = list(csv.DictReader(open("results_task6_burst_size_sweep.csv")))

fig, ax = plt.subplots(figsize=(9, 6))
colors = {"stacked_burst_0.0": "#1f77b4", "stacked_burst_2.0": "#7fb3e0",
          "bursty_0.0": "#d62728", "bursty_2.0": "#f0928f"}
markers = {"0.0": "o", "2.0": "s"}

for profile in ["stacked_burst", "bursty"]:
    for penalty in ["0.0", "2.0"]:
        sub = [r for r in rows if r["profile"] == profile and r["penalty"] == penalty]
        sub.sort(key=lambda r: float(r["burst_size"]))
        xs = [float(r["burst_size"]) for r in sub]
        ys = [float(r["pct_change"]) for r in sub]
        errs = [float(r["pct_ci_half"]) for r in sub]
        label = f"{profile} (penalty={penalty}ms)"
        ax.errorbar(xs, ys, yerr=errs, marker=markers[penalty],
                    color=colors[f"{profile}_{penalty}"], label=label,
                    capsize=3, linewidth=1.5, markersize=6)

ax.axhline(0, color="black", linewidth=0.8, linestyle="-")
ax.axvline(EFFECTIVE_CAP_SIZE, color="gray", linewidth=1, linestyle="--")
ax.text(EFFECTIVE_CAP_SIZE + 0.5, ax.get_ylim()[1] * 0.9,
        f"burst_duration caps actual\ntasks/burst at {EFFECTIVE_CAP_SIZE}\n(sizes beyond this are\nidentical workloads)",
        fontsize=8, color="gray", va="top")

ax.set_xlabel("requested burst_size")
ax.set_ylabel("p95_wait % change (burst_aware vs baseline)\nnegative = burst-aware better")
ax.set_title("stacked_burst / bursty: p95_wait effect vs burst size (A+B+C, medium-intensity timing)")
ax.legend(loc="lower right", fontsize=9)
ax.grid(True, alpha=0.3)

fig.tight_layout()
fig.savefig("task6_burst_size_sweep.png", dpi=150)
print("Wrote task6_burst_size_sweep.png")
