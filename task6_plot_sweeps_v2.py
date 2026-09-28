import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# --- Sweep A: burst_size ---
rows_a = list(csv.DictReader(open("results_task6_sweepA_burstsize_v2.csv")))

fig, axes = plt.subplots(1, 2, figsize=(15, 6))
colors = {"resets_true": "#1f77b4", "resets_false": "#d62728"}
markers = {"0.0": "o", "2.0": "s"}

for ax, profile in zip(axes, ["stacked_burst", "bursty"]):
    for variant in ["resets_true", "resets_false"]:
        for penalty in ["0.0", "2.0"]:
            sub = [r for r in rows_a if r["profile"] == profile and r["variant"] == variant
                   and r["penalty"] == penalty]
            sub.sort(key=lambda r: float(r["x_value"]))
            xs = [float(r["x_value"]) for r in sub]
            ys = [float(r["pct_change"]) for r in sub]
            errs = [float(r["pct_ci_half"]) for r in sub]
            ax.errorbar(xs, ys, yerr=errs, marker=markers[penalty], color=colors[variant],
                        label=f"{variant} (p={penalty}ms)", capsize=3, linewidth=1.3, markersize=5,
                        alpha=0.6 if penalty == "2.0" else 1.0)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xlabel("burst_size (burst_duration scaled so requested size is actually emitted)")
    ax.set_ylabel("p95_wait % change (negative = burst-aware better)")
    ax.set_title(f"{profile}: p95_wait vs burst_size (corrected, A+B+C)")
    ax.legend(fontsize=8, loc="best")
    ax.grid(True, alpha=0.3)

fig.tight_layout()
fig.savefig("task6_sweepA_burstsize_v2.png", dpi=150)
print("Wrote task6_sweepA_burstsize_v2.png")

# --- Sweep B: inter_burst_interval ---
rows_b = list(csv.DictReader(open("results_task6_sweepB_interval_v2.csv")))

fig2, ax2 = plt.subplots(figsize=(9, 6))
size_style = {"interval@size4": "-", "interval@size12": "--"}
for x_label in ["interval@size4", "interval@size12"]:
    for variant in ["resets_true", "resets_false"]:
        for penalty in ["0.0", "2.0"]:
            sub = [r for r in rows_b if r["x_label"] == x_label and r["variant"] == variant
                   and r["penalty"] == penalty]
            sub.sort(key=lambda r: float(r["x_value"]))
            xs = [float(r["x_value"]) for r in sub]
            ys = [float(r["pct_change"]) for r in sub]
            errs = [float(r["pct_ci_half"]) for r in sub]
            size_label = x_label.split("size")[1]
            ax2.errorbar(xs, ys, yerr=errs, marker=markers[penalty], color=colors[variant],
                         linestyle=size_style[x_label],
                         label=f"size={size_label} {variant} (p={penalty}ms)",
                         capsize=3, linewidth=1.3, markersize=5, alpha=0.6 if penalty == "2.0" else 1.0)

ax2.axhline(0, color="black", linewidth=0.8)
ax2.set_xlabel("inter_burst_interval (ms)")
ax2.set_ylabel("p95_wait % change (negative = burst-aware better)")
ax2.set_title("stacked_burst: p95_wait vs inter_burst_interval, at fixed burst_size 4/12 (A+B+C)")
ax2.legend(fontsize=7, loc="best")
ax2.grid(True, alpha=0.3)

fig2.tight_layout()
fig2.savefig("task6_sweepB_interval_v2.png", dpi=150)
print("Wrote task6_sweepB_interval_v2.png")
