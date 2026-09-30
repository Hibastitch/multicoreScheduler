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

from task6_confirmation_analyze import (
    WORKLOAD_ORDER, METRICS, COST_METRICS, load_rows, fnum,
    V2_SUFFIX, V3, V4, SUFFIX, PENALTIES, HEADLINE_VARIANT, SECONDARY_VARIANTS,
)

# TASK 9 v3 / TASK 10 v4 (2026-09-29/30, Step 4/3): under v1/v2 this
# writes tables for the 2 non-headline variants (original_q2_a0.8_or,
# runner_up_q4_a1.5_or) -- "final" already has its own MAIN_TABLE from
# task6_confirmation_analyze.py. Under v3/v4 that generalizes to "every
# SECONDARY_VARIANTS entry except the headline" -- 3 variants instead
# of 2 either way.
if V3 or V4:
    VARIANT_TABLES = {
        v: f"results_task6_confirmation{SUFFIX}_TABLE_{v}.csv"
        for v in SECONDARY_VARIANTS if v != HEADLINE_VARIANT
    }
else:
    VARIANT_TABLES = {
        "original_q2_a0.8_or": f"results_task6_confirmation{V2_SUFFIX}_TABLE_original.csv",
        "runner_up_q4_a1.5_or": f"results_task6_confirmation{V2_SUFFIX}_TABLE_runnerup.csv",
    }

# Compact-table comparison pair: headline vs the original detector
# (ungated/unchecked, under v3/v4 -- the mechanism itself is what's
# being evaluated). Reuses PENALTIES[0]/[-1] as the two comparison
# points, same choice as task6_confirmation_analyze.py's plot section
# under v3/v4 (0.0 and 2.0 unchanged for v1/v2).
COMPACT_VARIANT_A = HEADLINE_VARIANT
if V4:
    COMPACT_VARIANT_B = "original_q2_a0.8_or_unchecked"
elif V3:
    COMPACT_VARIANT_B = "original_q2_a0.8_or_ungated"
else:
    COMPACT_VARIANT_B = "original_q2_a0.8_or"
COMPACT_PEN_LO, COMPACT_PEN_HI = PENALTIES[0], PENALTIES[-1]

# TASK 9 rule (e) (docs/NOTEBOOK.md 2026-09-29h): only penalties 0 and
# 0.5 disqualify a config; 2ms is a pessimistic stress test -- measured
# and reported in full, but does not by itself make a cell "harmful"
# for selection purposes. Mirrors RULE_PENALTIES in
# task6_threshold_grid_recompute_harm.py. v3-only.
DISQUALIFYING_PENALTIES = {"0.0", "0.5"}
PENALTY_LABELS = {"0.0": "p0", "0.5": "p0.5", "2.0": "p2"}

# Display wording for the mechanism-on/mechanism-off pair -- v3's is a
# per-domain gate, v4's is a machine-wide idle check. Only ever read
# when V3 or V4 (irrelevant, unused, for v1/v2).
MECH_LABEL = "checked" if V4 else "gated"
MECH_LABEL_OFF = "unchecked" if V4 else "ungated"


def write_variant_table(rows, variant, out_path):
    variant_rows = []
    for wl in WORKLOAD_ORDER:
        for pen in PENALTIES:
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


# ========================= v3/v4 only ("_v3" suffix throughout the
# function names below, reused as-is for v4 -- see MECH_LABEL/
# MECH_LABEL_OFF above for the wording swap, and SECONDARY_VARIANTS'
# fixed 4-element order for the variant-name swap) =========================
# v1/v2 hardcode a p0/p2 pair and 3 variants (final/original/runner_up).
# v3/v4 have 3 penalties (0/0.5/2) and 4 variants each (v3: selected
# gated/ungated, original ungated, runner-up ungated; v4: selected
# checked/unchecked, original unchecked, runner-up checked), plus rule
# (e)'s disqualifying-vs-stress-test distinction. Kept as separate
# functions rather than branching the v1/v2 ones apart, since the
# shapes differ enough (pair vs triple, 2 harm columns vs 4) that
# shared branches would be harder to read than the duplication.

def pct_str_multi(rows_by_pen, metric):
    return " / ".join(pct_str(rows_by_pen.get(p), metric) for p in PENALTIES)


def mean_fires(rows_by_pen):
    vals = [fnum(r, "detector_fires") for r in rows_by_pen.values() if r is not None]
    return sum(vals) / len(vals) if vals else 0.0


def harm_info_v3(rows_by_pen, metrics=METRICS):
    """Which metric(s) triggered {metric}_harm=True for this variant, at
    which penalty -- same idea as harm_metrics_str(), generalized to 3
    penalties and annotated per rule (e): a penalty=2ms-only hit is
    marked (stress) and does NOT set any_disqualifying. Returns
    (any_disqualifying: bool, full_str, disqualifying_only_str)."""
    parts, disq_parts = [], []
    any_disqualifying = False
    for m in metrics:
        pens_hit = [p for p in PENALTIES if rows_by_pen.get(p) is not None
                    and rows_by_pen[p].get(f"{m}_harm") == "True"]
        if not pens_hit:
            continue
        labelled, disq_labelled = [], []
        for p in pens_hit:
            lbl = PENALTY_LABELS.get(p, p)
            if p in DISQUALIFYING_PENALTIES:
                any_disqualifying = True
                disq_labelled.append(lbl)
            else:
                lbl += "(stress)"
            labelled.append(lbl)
        parts.append(f"{m}@{','.join(labelled)}")
        if disq_labelled:
            disq_parts.append(f"{m}@{','.join(disq_labelled)}")
    return any_disqualifying, "; ".join(parts), "; ".join(disq_parts)


def sign_flip_multi(rows_by_pen, metric):
    vals = [fnum(r, f"{metric}_pct") for r in rows_by_pen.values() if r is not None]
    return any(v > 1e-9 for v in vals) and any(v < -1e-9 for v in vals)


def avg_pct_marked_multi(rows_by_pen, metric):
    return avg_pct(rows_by_pen, metric), sign_flip_multi(rows_by_pen, metric)


def classify_group_v3(gated_by_pen, gated_disqualifying_harm):
    if gated_disqualifying_harm:
        return f"{MECH_LABEL} harmful (p0/p0.5)"
    if mean_fires(gated_by_pen) < 0.5:
        return f"{MECH_LABEL} silent"
    sig_improve = any(
        r is not None and fnum(r, "p95_wait_wins") is not None and fnum(r, "p95_wait_harms") is not None
        and fnum(r, "p95_wait_wins") > fnum(r, "p95_wait_harms")
        and fnum(r, "p95_wait_sign_p") is not None and fnum(r, "p95_wait_sign_p") < 0.05
        for r in gated_by_pen.values()
    )
    return f"{MECH_LABEL} helps" if sig_improve else "fires without benefit"


def build_compact_table_v3(rows):
    def get(workload, penalty, variant):
        for r in rows:
            if r["workload"] == workload and r["penalty"] == penalty and r["variant"] == variant:
                return r
        return None

    # SECONDARY_VARIANTS is positional -- [0]=headline (gated/checked),
    # [1]=same thresholds with the mechanism OFF, [2]=original detector
    # (mechanism off), [3]=runner-up -- true for both v3's 4 names and
    # v4's, so index into it rather than hardcode v3's own variant
    # names (which would silently mismatch v4's "_checked"/"_unchecked"
    # names and read all-None columns).
    variant_gated, variant_ungated, variant_orig, variant_runnerup = SECONDARY_VARIANTS

    compact_rows = []
    missing_workloads = []
    for wl in WORKLOAD_ORDER:
        gated_by_pen = {pen: get(wl, pen, variant_gated) for pen in PENALTIES}
        if all(v is None for v in gated_by_pen.values()):
            missing_workloads.append(wl)
            continue
        ungated_by_pen = {pen: get(wl, pen, variant_ungated) for pen in PENALTIES}
        orig_by_pen = {pen: get(wl, pen, variant_orig) for pen in PENALTIES}
        runnerup_by_pen = {pen: get(wl, pen, variant_runnerup) for pen in PENALTIES}

        gated_disq, gated_harm_str, gated_disq_str = harm_info_v3(gated_by_pen)
        ungated_disq, ungated_harm_str, _ = harm_info_v3(ungated_by_pen)
        orig_disq, orig_harm_str, _ = harm_info_v3(orig_by_pen)

        group = classify_group_v3(gated_by_pen, gated_disq)

        avg_wait_gated, avg_wait_gated_flip = avg_pct_marked_multi(gated_by_pen, "avg_wait")
        avg_wait_orig, avg_wait_orig_flip = avg_pct_marked_multi(orig_by_pen, "avg_wait")
        migrations_gated, migrations_gated_flip = avg_pct_marked_multi(gated_by_pen, "total_migrations")
        scanwork_gated, scanwork_gated_flip = avg_pct_marked_multi(gated_by_pen, "sched_cores_scanned")
        runnerup_p95_avg, runnerup_p95_flip = avg_pct_marked_multi(runnerup_by_pen, "p95_wait")

        compact_rows.append(dict(
            workload=wl, group=group,
            gated_fires=mean_fires(gated_by_pen),
            gated_p95=pct_str_multi(gated_by_pen, "p95_wait"),
            gated_any_disqualifying_harm=gated_disq,
            gated_harm_metrics=gated_harm_str,
            gated_disqualifying_harm_metrics=gated_disq_str,
            ungated_p95=pct_str_multi(ungated_by_pen, "p95_wait"),
            ungated_any_disqualifying_harm=ungated_disq,
            ungated_harm_metrics=ungated_harm_str,
            orig_fires=mean_fires(orig_by_pen),
            orig_p95=pct_str_multi(orig_by_pen, "p95_wait"),
            orig_any_disqualifying_harm=orig_disq,
            orig_harm_metrics=orig_harm_str,
            runnerup_p95=pct_str_multi(runnerup_by_pen, "p95_wait"),
            avg_wait_gated=avg_wait_gated, avg_wait_gated_flip=avg_wait_gated_flip,
            avg_wait_orig=avg_wait_orig, avg_wait_orig_flip=avg_wait_orig_flip,
            migrations_gated=migrations_gated, migrations_gated_flip=migrations_gated_flip,
            scanwork_gated=scanwork_gated, scanwork_gated_flip=scanwork_gated_flip,
        ))
    if missing_workloads:
        print(f"WARNING: {len(missing_workloads)}/{len(WORKLOAD_ORDER)} workload(s) have NO "
              f"{HEADLINE_VARIANT} confirmation rows at all -- run task6_confirmation_run.py for "
              f"them first: {missing_workloads}")
    else:
        print(f"All {len(WORKLOAD_ORDER)} workloads present in the v3 compact table.")
    return compact_rows


def write_compact_csv_v3(compact_rows, out_path):
    cols = ["group", "workload",
            "gated_fires", "gated_p95", "gated_any_disqualifying_harm",
            "gated_harm_metrics", "gated_disqualifying_harm_metrics",
            "ungated_p95", "ungated_any_disqualifying_harm", "ungated_harm_metrics",
            "orig_fires", "orig_p95", "orig_any_disqualifying_harm", "orig_harm_metrics",
            "runnerup_p95",
            "avg_wait_gated", "avg_wait_gated_flip", "avg_wait_orig", "avg_wait_orig_flip",
            "migrations_gated", "migrations_gated_flip", "scanwork_gated", "scanwork_gated_flip"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(compact_rows)
    print(f"Wrote {out_path}")


def write_compact_markdown_v3(compact_rows, out_path, harm_summary_lines):
    lines = []
    if harm_summary_lines:
        lines.append("**Disqualifying harm at penalty 0 or 0.5 (rule (e)): YES -- see rows below.**")
        for l in harm_summary_lines:
            lines.append(f"- {l}")
    else:
        lines.append("**Disqualifying harm at penalty 0 or 0.5 (rule (e)): NO** -- "
                      f"{HEADLINE_VARIANT} (the pre-registered selection) showed no significant "
                      "harm on any metric, any of the 16 confirmation workloads, at penalty "
                      "0 or 0.5. (Penalty=2ms stress-test harm, if any, is reported per-row below.)")
    lines.append("")
    lines.append(f"| workload | {MECH_LABEL} fires | {MECH_LABEL} p95 Δ% (p0/p0.5/p2) | "
                  f"{MECH_LABEL} harm metric(s) | "
                  f"{MECH_LABEL_OFF} p95 Δ% (p0/p0.5/p2) | {MECH_LABEL_OFF} harm metric(s) | orig fires | "
                  "orig p95 Δ% (p0/p0.5/p2) | orig harm metric(s) | runner-up p95 Δ% (p0/p0.5/p2) | "
                  f"avg_wait Δ% ({MECH_LABEL}/orig) | migrations Δ% ({MECH_LABEL}) | "
                  f"scan work Δ% ({MECH_LABEL}) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")

    group_order = [f"{MECH_LABEL} harmful (p0/p0.5)", f"{MECH_LABEL} helps", f"{MECH_LABEL} silent",
                   "fires without benefit"]
    n_cols = 13
    for group in group_order:
        group_rows = [r for r in compact_rows if r["group"] == group]
        if not group_rows:
            continue
        lines.append(f"| **{group}** " + "| " * (n_cols - 1) + "|")
        for r in group_rows:
            gated_harm_col = r["gated_harm_metrics"] if (r["gated_any_disqualifying_harm"]
                                                           or r["gated_harm_metrics"]) else "no"
            ungated_harm_col = r["ungated_harm_metrics"] if (r["ungated_any_disqualifying_harm"]
                                                              or r["ungated_harm_metrics"]) else "no"
            orig_harm_col = r["orig_harm_metrics"] if (r["orig_any_disqualifying_harm"]
                                                        or r["orig_harm_metrics"]) else "no"
            lines.append(
                f"| {r['workload']} | {r['gated_fires']:.1f} | {r['gated_p95']} | {gated_harm_col} | "
                f"{r['ungated_p95']} | {ungated_harm_col} | "
                f"{r['orig_fires']:.1f} | {r['orig_p95']} | {orig_harm_col} | "
                f"{r['runnerup_p95']} | "
                f"{fmt_avg(r['avg_wait_gated'], r['avg_wait_gated_flip'])} / "
                f"{fmt_avg(r['avg_wait_orig'], r['avg_wait_orig_flip'])} | "
                f"{fmt_avg(r['migrations_gated'], r['migrations_gated_flip'])} | "
                f"{fmt_avg(r['scanwork_gated'], r['scanwork_gated_flip'])} |"
            )

    appendix_tables = ", ".join([f"results_task6_confirmation{SUFFIX}_MAIN_TABLE.csv"]
                                 + list(VARIANT_TABLES.values()))
    footer = ("\n_Stars: \\* p<0.05, \\*\\* p<0.01, \\*\\*\\* p<0.001 (exact sign test, n=30 "
              "paired, tie-tolerant). p95 Δ%% triples are (penalty=0 / penalty=0.5 / penalty=2), "
              "rule (e)'s full sweep; avg_wait/migrations/scan-work %% are averaged across all 3 "
              "penalties -- † marks a cell where at least two penalties disagree in sign, so the "
              "average shown understates or masks a real per-penalty reversal -- see the "
              f"per-penalty appendix tables ({appendix_tables}) for the exact per-penalty values. "
              "'harm metric(s)' names which metric(s) triggered {metric}_harm=True for that "
              "variant and at which penalty -- (stress) marks a penalty=2ms-only hit, which "
              "per rule (e) does NOT disqualify (it is measured and reported, not selected "
              "against); an unmarked penalty (p0/p0.5) does disqualify. A row can show "
              "p95_wait improving and still list a harm metric because harm is evaluated "
              "per-metric, not just on p95_wait. "
              f"Groups: **{MECH_LABEL} harmful (p0/p0.5)** = {HEADLINE_VARIANT} itself showed "
              "disqualifying harm on this confirmation workload (a rule (e) violation, "
              f"reported regardless); **{MECH_LABEL} helps** = significant p95_wait improvement at "
              f"some penalty, no disqualifying harm; **{MECH_LABEL} silent** = detector never fires "
              "(<0.5 fires/run average); **fires without benefit** = fires but no significant "
              "p95_wait improvement._\n")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n" + footer)
    print(f"Wrote {out_path}")


def build_compact_table(rows):
    def get(workload, penalty, variant):
        for r in rows:
            if r["workload"] == workload and r["penalty"] == penalty and r["variant"] == variant:
                return r
        return None

    compact_rows = []
    for wl in WORKLOAD_ORDER:
        fin_p0, fin_p2 = get(wl, COMPACT_PEN_LO, COMPACT_VARIANT_A), get(wl, COMPACT_PEN_HI, COMPACT_VARIANT_A)
        orig_p0, orig_p2 = get(wl, COMPACT_PEN_LO, COMPACT_VARIANT_B), get(wl, COMPACT_PEN_HI, COMPACT_VARIANT_B)
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
          f"{len(glob.glob(f'results_task6_confirmation{SUFFIX}_*_summary.csv'))} files")

    for variant, out_path in VARIANT_TABLES.items():
        write_variant_table(rows, variant, out_path)

    if V3 or V4:
        compact_rows = build_compact_table_v3(rows)
        write_compact_csv_v3(compact_rows, f"results_task6_confirmation{SUFFIX}_COMPACT_TABLE.csv")

        # rule (e) confirmatory check (v3) / Task 10 equivalent (v4):
        # the headline variant was CHOSEN to be harm-free at penalties
        # 0/0.5 on the grid's 9 workloads -- the confirmation run uses
        # fresh seeds AND 16 workloads (7 more than the grid), so "no
        # disqualifying harm here too" is an empirical result, not a given.
        harmed = [r for r in compact_rows if r["gated_any_disqualifying_harm"]]
        harm_summary_lines = [f"{r['workload']}: {r['gated_disqualifying_harm_metrics']}" for r in harmed]
        write_compact_markdown_v3(compact_rows, f"results_task6_confirmation{SUFFIX}_COMPACT_TABLE.md",
                                   harm_summary_lines)

        print(f"\n{HEADLINE_VARIANT} disqualifying harm (penalty 0 or 0.5) on any of the "
              f"{len(compact_rows)} confirmation workloads: {'YES' if harmed else 'NO'}")
        if harmed:
            for l in harm_summary_lines:
                print(f"  {l}")
    else:
        compact_rows = build_compact_table(rows)
        write_compact_csv(compact_rows, f"results_task6_confirmation{SUFFIX}_COMPACT_TABLE.csv")
        write_compact_markdown(compact_rows, f"results_task6_confirmation{SUFFIX}_COMPACT_TABLE.md")

    print("\nGroup counts:")
    from collections import Counter
    print(Counter(r["group"] for r in compact_rows))


if __name__ == "__main__":
    main()
