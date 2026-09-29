"""
Build the per-variant MAIN_TABLE-format CSVs for the two secondary
confirmation-run variants (original_q2_a0.8_or, runner_up_q4_a1.5_or),
plus a compact paper-ready table (final vs original, one row per
workload). No new simulations -- everything comes from the existing
results_task6_confirmation_*_summary.csv files.

Harm rule: reuses the summary CSVs' own {metric}_harm / any_harm
columns, which task6_confirmation_run.py already computes with the
CORRECTED rule (harms > wins required -- see Readme.md 2026-09-27h).
Nothing here recomputes harm with the old, direction-blind rule.
"""

import csv
import glob
import os

from task6_confirmation_analyze import WORKLOAD_ORDER, METRICS, COST_METRICS, load_rows, fnum, V2_SUFFIX

VARIANT_TABLES = {
    "original_q2_a0.8_or": f"results_task6_confirmation{V2_SUFFIX}_TABLE_original.csv",
    "runner_up_q4_a1.5_or": f"results_task6_confirmation{V2_SUFFIX}_TABLE_runnerup.csv",
}


def write_variant_table(rows, variant, out_path):
    variant_rows = []
    for wl in WORKLOAD_ORDER:
        for pen in ["0.0", "2.0"]:
            r = next((r for r in rows if r["workload"] == wl and r["penalty"] == pen
                       and r["variant"] == variant), None)
            if r is not None:
                variant_rows.append(r)
    if not variant_rows:
        print(f"WARNING: no rows found for variant {variant!r}, skipping {out_path}")
        return

    cols = ["workload", "penalty", "any_harm", "detector_fires", "recall", "precision"]
    for m in METRICS:
        cols += [f"{m}_base", f"{m}_var", f"{m}_pct", f"{m}_sign_p", f"{m}_harm", f"{m}_floored"]
    for cm in COST_METRICS:
        cols += [f"{cm}_base", f"{cm}_var", f"{cm}_pct"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(variant_rows)
    print(f"Wrote {out_path} ({len(variant_rows)} rows)")


def stars(p):
    if p is None:
        return ""
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return ""


def signed_stars(r, metric="p95_wait"):
    """Stars from the sign test, using the wins/harms split to recover
    direction -- sign_test_p() is direction-blind by construction (see
    Readme.md 2026-09-27h), so the p-value alone can't say which way."""
    if r is None:
        return "", None
    p = fnum(r, f"{metric}_sign_p")
    wins, harms = fnum(r, f"{metric}_wins"), fnum(r, f"{metric}_harms")
    s = stars(p)
    if wins is not None and harms is not None and wins == harms:
        s = ""  # exact 50/50 split -- never actually significant, but guard anyway
    return s, p


def pct_str(r, metric, penalty_suffix=""):
    if r is None:
        return "n/a"
    pct = fnum(r, f"{metric}_pct")
    s, _ = signed_stars(r, metric)
    return f"{pct:+.1f}%{s}"


def avg_pct(rows_by_pen, metric):
    vals = [fnum(r, f"{metric}_pct") for r in rows_by_pen.values() if r is not None]
    return sum(vals) / len(vals) if vals else None


def sign_flip(r_p0, r_p2, metric):
    p0 = fnum(r_p0, f"{metric}_pct") if r_p0 is not None else None
    p2 = fnum(r_p2, f"{metric}_pct") if r_p2 is not None else None
    if p0 is None or p2 is None:
        return False
    return (p0 > 1e-9 and p2 < -1e-9) or (p0 < -1e-9 and p2 > 1e-9)


def avg_pct_marked(r_p0, r_p2, metric):
    """Mean pct across p0/p2, with a dagger if the two penalties disagree
    in sign -- averaging a +x%/-y% pair silently hides a real reversal."""
    val = avg_pct({"p0": r_p0, "p2": r_p2}, metric)
    flipped = sign_flip(r_p0, r_p2, metric)
    return val, flipped


def harm_metrics_str(r_p0, r_p2):
    """Which metric(s) triggered any_harm=True for ORIGINAL, at which
    penalty -- p95_wait improving does not preclude harm on a DIFFERENT
    metric (e.g. avg_slowdown), so this is what actually explains an
    'orig_any_harm=yes' row whose p95 column looks like an improvement."""
    parts = []
    for m in METRICS:
        pens = []
        for label, r in [("p0", r_p0), ("p2", r_p2)]:
            if r is not None and r.get(f"{m}_harm") == "True":
                pens.append(label)
        if pens:
            parts.append(f"{m}@{','.join(pens)}")
    return "; ".join(parts)


def classify_group(final_p0, final_p2):
    fires = [r["fires"] for r in (final_p0, final_p2) if r is not None]
    mean_fires = sum(fires) / len(fires) if fires else 0.0
    if mean_fires < 0.5:
        return "final silent"
    sig_improve = any(
        r is not None and r["wins"] is not None and r["harms"] is not None
        and r["wins"] > r["harms"] and r["sign_p"] is not None and r["sign_p"] < 0.05
        for r in (final_p0, final_p2)
    )
    return "final helps" if sig_improve else "fires without benefit"


def build_compact_table(rows):
    def get(workload, penalty, variant):
        for r in rows:
            if r["workload"] == workload and r["penalty"] == penalty and r["variant"] == variant:
                return r
        return None

    compact_rows = []
    for wl in WORKLOAD_ORDER:
        fin_p0, fin_p2 = get(wl, "0.0", "final"), get(wl, "2.0", "final")
        orig_p0, orig_p2 = get(wl, "0.0", "original_q2_a0.8_or"), get(wl, "2.0", "original_q2_a0.8_or")
        if fin_p0 is None and fin_p2 is None:
            continue

        def summarize(r):
            if r is None:
                return dict(fires=0.0, wins=None, harms=None, sign_p=None)
            return dict(fires=fnum(r, "detector_fires"), wins=fnum(r, "p95_wait_wins"),
                        harms=fnum(r, "p95_wait_harms"), sign_p=fnum(r, "p95_wait_sign_p"))

        fin_p0s, fin_p2s = summarize(fin_p0), summarize(fin_p2)
        group = classify_group(fin_p0s, fin_p2s)

        final_fires = sum(x["fires"] for x in (fin_p0s, fin_p2s)) / 2
        orig_fires_vals = [fnum(r, "detector_fires") for r in (orig_p0, orig_p2) if r is not None]
        orig_fires = sum(orig_fires_vals) / len(orig_fires_vals) if orig_fires_vals else 0.0
        orig_any_harm = any(r is not None and r["any_harm"] == "True" for r in (orig_p0, orig_p2))

        avg_wait_final, avg_wait_final_flip = avg_pct_marked(fin_p0, fin_p2, "avg_wait")
        avg_wait_orig, avg_wait_orig_flip = avg_pct_marked(orig_p0, orig_p2, "avg_wait")
        migrations_final, migrations_final_flip = avg_pct_marked(fin_p0, fin_p2, "total_migrations")
        scanwork_final, scanwork_final_flip = avg_pct_marked(fin_p0, fin_p2, "sched_cores_scanned")

        compact_rows.append(dict(
            workload=wl, group=group,
            final_fires=final_fires,
            final_p95_p0=pct_str(fin_p0, "p95_wait"), final_p95_p2=pct_str(fin_p2, "p95_wait"),
            orig_fires=orig_fires,
            orig_p95_p0=pct_str(orig_p0, "p95_wait"), orig_p95_p2=pct_str(orig_p2, "p95_wait"),
            orig_any_harm=orig_any_harm,
            orig_harm_metrics=harm_metrics_str(orig_p0, orig_p2) if orig_any_harm else "",
            avg_wait_final=avg_wait_final, avg_wait_final_flip=avg_wait_final_flip,
            avg_wait_original=avg_wait_orig, avg_wait_original_flip=avg_wait_orig_flip,
            migrations_final=migrations_final, migrations_final_flip=migrations_final_flip,
            scanwork_final=scanwork_final, scanwork_final_flip=scanwork_final_flip,
        ))
    return compact_rows


def write_compact_csv(compact_rows, out_path):
    cols = ["group", "workload", "final_fires", "final_p95_p0", "final_p95_p2",
            "orig_fires", "orig_p95_p0", "orig_p95_p2", "orig_any_harm", "orig_harm_metrics",
            "avg_wait_final", "avg_wait_final_flip", "avg_wait_original", "avg_wait_original_flip",
            "migrations_final", "migrations_final_flip", "scanwork_final", "scanwork_final_flip"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(compact_rows)
    print(f"Wrote {out_path}")


def fmt_avg(v, flipped=False):
    if v is None:
        return "n/a"
    dagger = "†" if flipped else ""
    return f"{v:+.1f}%{dagger}"


def write_compact_markdown(compact_rows, out_path):
    lines = []
    lines.append("| workload | final fires | final p95 Δ% (p0/p2) | orig fires | "
                  "orig p95 Δ% (p0/p2) | orig harm metric(s) | avg_wait Δ% (final/orig) | "
                  "migrations Δ% (final) | scan work Δ% (final) |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    group_order = ["final helps", "final silent", "fires without benefit"]
    for group in group_order:
        group_rows = [r for r in compact_rows if r["group"] == group]
        if not group_rows:
            continue
        lines.append(f"| **{group}** | | | | | | | | |")
        for r in group_rows:
            harm_col = r["orig_harm_metrics"] if r["orig_any_harm"] else "no"
            lines.append(
                f"| {r['workload']} | {r['final_fires']:.1f} | "
                f"{r['final_p95_p0']} / {r['final_p95_p2']} | "
                f"{r['orig_fires']:.1f} | {r['orig_p95_p0']} / {r['orig_p95_p2']} | "
                f"{harm_col} | "
                f"{fmt_avg(r['avg_wait_final'], r['avg_wait_final_flip'])} / "
                f"{fmt_avg(r['avg_wait_original'], r['avg_wait_original_flip'])} | "
                f"{fmt_avg(r['migrations_final'], r['migrations_final_flip'])} | "
                f"{fmt_avg(r['scanwork_final'], r['scanwork_final_flip'])} |"
            )

    footer = ("\n_Stars: \\* p<0.05, \\*\\* p<0.01, \\*\\*\\* p<0.001 "
              "(exact sign test, n=30 paired, tie-tolerant). "
              "avg_wait/migrations/scan-work %% are averaged across penalty 0 and 2; "
              "† marks a cell where penalty 0 and penalty 2 have OPPOSITE signs, so the "
              "average shown understates or masks a real per-penalty reversal -- see the "
              "per-penalty appendix tables (results_task6_confirmation_MAIN_TABLE.csv, "
              "_TABLE_original.csv, _TABLE_runnerup.csv) for the exact p0/p2 values. "
              "'orig harm metric(s)' names which metric(s) triggered any_harm=True for "
              "the ORIGINAL detector and at which penalty (p0/p2) -- this is independent "
              "of the p95_wait column, so a row can show p95_wait improving and still be "
              "flagged harmful because of a DIFFERENT metric (see the worked explanation "
              "in the session notes). "
              "Groups: **final helps** = significant p95_wait improvement at either "
              "penalty; **final silent** = detector never fires (<0.5 fires/run "
              "average); **fires without benefit** = fires but no significant "
              "p95_wait improvement._\n")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n" + footer)
    print(f"Wrote {out_path}")


def main():
    rows = load_rows()
    print(f"Loaded {len(rows)} summary rows from "
          f"{len(glob.glob(f'results_task6_confirmation{V2_SUFFIX}_*_summary.csv'))} files")

    for variant, out_path in VARIANT_TABLES.items():
        write_variant_table(rows, variant, out_path)

    compact_rows = build_compact_table(rows)
    write_compact_csv(compact_rows, f"results_task6_confirmation{V2_SUFFIX}_COMPACT_TABLE.csv")
    write_compact_markdown(compact_rows, f"results_task6_confirmation{V2_SUFFIX}_COMPACT_TABLE.md")

    print("\nGroup counts:")
    from collections import Counter
    print(Counter(r["group"] for r in compact_rows))


if __name__ == "__main__":
    main()
