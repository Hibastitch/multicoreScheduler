| workload | final fires | final p95 Δ% (p0/p2) | orig fires | orig p95 Δ% (p0/p2) | orig harm metric(s) | avg_wait Δ% (final/orig) | migrations Δ% (final) | scan work Δ% (final) |
|---|---|---|---|---|---|---|---|---|
| **final helps** | | | | | | | | |
| stacked_medium | 24.8 | -25.2%*** / -22.1%*** | 159.7 | -23.4%*** / -16.6%*** | no | -23.6% / -21.9% | +3.2% | +2.0% |
| stacked_high | 140.0 | -16.9%* / -12.8% | 180.9 | -22.5%*** / -18.3%** | no | -18.6% / -20.6% | -1.3% | +5.3% |
| rate1.5_s12 | 15.9 | -23.2%*** / -23.0%*** | 95.8 | -25.5%*** / -25.8%*** | no | -25.0% / -24.4% | +2.7% | +1.1% |
| rate3.0_s12 | 42.0 | -23.4%*** / -20.1%*** | 91.9 | -2.6% / -2.2% | no | -28.7% / -13.2% | +2.2% | +1.4% |
| **final silent** | | | | | | | | |
| stacked_low | 0.0 | +0.0% / +0.0% | 49.9 | +5.9% / +1.3% | no | +0.0% / -1.0% | +0.0% | +0.0% |
| bursty_high_s24 | 0.0 | +0.0% / +0.0% | 10.7 | +0.0% / +7.2% | p99_wait@p0 | +0.0% / +1.1% | +0.0% | +0.0% |
| heavy_tail_high | 0.0 | +0.0% / +0.0% | 9.9 | +0.0% / +0.0% | p99_wait@p0,p2; avg_wait@p2; avg_slowdown@p0,p2; p95_slowdown@p0,p2 | +0.0% / +0.3% | +0.0% | +0.0% |
| rate0.5_s4 | 0.0 | +0.0% / +0.0% | 10.0 | +17.2%* / +20.0%** | p95_wait@p0,p2; p99_wait@p0,p2; avg_wait@p2 | +0.0% / +7.0% | +0.0% | +0.0% |
| rate0.5_s12 | 0.0 | +0.0% / +0.0% | 24.4 | -24.8%*** / -22.5%*** | no | +0.0% / -18.0% | +0.0% | +0.0% |
| rate0.75_s4 | 0.0 | +0.0% / +0.0% | 20.0 | -18.6%** / -12.7% | no | +0.0% / -30.4% | +0.0% | +0.0% |
| rate0.75_s12 | 0.0 | +0.0% / +0.0% | 99.9 | -38.9%*** / -38.3%*** | no | +0.0% / -32.3% | +0.0% | +0.0% |
| rate1.0_s4 | 0.0 | +0.0% / +0.0% | 20.0 | -12.0% / -13.1%* | no | +0.0% / -20.9% | +0.0% | +0.0% |
| rate1.0_s12 | 0.0 | +0.0% / +0.0% | 99.9 | -16.9%* / -17.3%*** | no | +0.0% / -17.8% | +0.0% | +0.0% |
| rate1.5_s4 | 0.0 | +0.0% / +0.0% | 20.0 | -21.6%*** / -18.5%** | no | +0.0% / -20.1% | +0.0% | +0.0% |
| rate3.0_s4 | 0.0 | +0.0% / +0.0% | 10.0 | -29.6%*** / -29.0%*** | no | +0.0% / -32.0% | +0.0% | +0.0% |
| **fires without benefit** | | | | | | | | |
| bursty_high_s64 | 216.7 | -2.1% / -0.2% | 420.8 | -0.8% / -0.0% | avg_slowdown@p2; p95_slowdown@p2; makespan_excess@p2 | +4.0% / +0.3%† | +14.5% | +3.1% |

_Stars: \* p<0.05, \*\* p<0.01, \*\*\* p<0.001 (exact sign test, n=30 paired, tie-tolerant). avg_wait/migrations/scan-work %% are averaged across penalty 0 and 2; † marks a cell where penalty 0 and penalty 2 have OPPOSITE signs, so the average shown understates or masks a real per-penalty reversal -- see the per-penalty appendix tables (results_task6_confirmation_MAIN_TABLE.csv, _TABLE_original.csv, _TABLE_runnerup.csv) for the exact p0/p2 values. 'orig harm metric(s)' names which metric(s) triggered any_harm=True for the ORIGINAL detector and at which penalty (p0/p2) -- this is independent of the p95_wait column, so a row can show p95_wait improving and still be flagged harmful because of a DIFFERENT metric (see the worked explanation in the session notes). Groups: **final helps** = significant p95_wait improvement at either penalty; **final silent** = detector never fires (<0.5 fires/run average); **fires without benefit** = fires but no significant p95_wait improvement._
