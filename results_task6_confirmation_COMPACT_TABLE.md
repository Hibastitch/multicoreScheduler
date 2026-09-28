| workload | final fires | final p95 Δ% (p0/p2) | orig fires | orig p95 Δ% (p0/p2) | orig harm metric(s) | avg_wait Δ% (final/orig) | migrations Δ% (final) | scan work Δ% (final) |
|---|---|---|---|---|---|---|---|---|
| **final helps** | | | | | | | | |
| stacked_medium | 56.8 | -11.3%** / -11.0%** | 158.1 | -15.3%*** / -15.5%*** | no | -16.2% / -21.4% | -1.5% | +2.4% |
| stacked_high | 146.0 | -34.5%*** / -29.4%*** | 180.6 | -26.4%** / -20.4%** | no | -32.3% / -22.9% | -8.6% | +4.1% |
| rate1.5_s12 | 31.9 | -26.1%*** / -24.1%*** | 95.8 | -28.3%*** / -26.3%*** | no | -27.4% / -30.4% | -4.4% | +2.0% |
| rate3.0_s12 | 49.8 | -25.9%*** / -19.4%*** | 91.8 | +0.1% / -2.2% | no | -32.9% / -15.6% | -3.5% | +2.3% |
| **final silent** | | | | | | | | |
| stacked_low | 0.0 | +0.0% / +0.0% | 49.9 | +6.2%** / +7.1%*** | p95_wait@p0,p2; p99_wait@p0,p2 | +0.0% / -0.0%† | +0.0% | +0.0% |
| bursty_high_s24 | 0.0 | +0.0% / +0.0% | 11.0 | +9.2% / +10.7%** | p95_wait@p2; avg_wait@p2; avg_slowdown@p0,p2 | +0.0% / +2.4% | +0.0% | +0.0% |
| heavy_tail_high | 0.0 | +0.0% / +0.0% | 12.0 | +0.0% / +0.0% | p99_wait@p0,p2; avg_wait@p0,p2; avg_slowdown@p0,p2; p95_slowdown@p0,p2 | +0.0% / +1.0% | +0.0% | +0.0% |
| rate0.5_s4 | 0.0 | +0.0% / +0.0% | 10.0 | +0.9% / +3.6% | no | +0.0% / +0.2%† | +0.0% | +0.0% |
| rate0.5_s12 | 0.0 | +0.0% / +0.0% | 23.1 | -30.7%*** / -30.5%*** | no | +0.0% / -19.6% | +0.0% | +0.0% |
| rate0.75_s4 | 0.0 | +0.0% / +0.0% | 20.0 | -43.0%*** / -43.2%*** | no | +0.0% / -41.3% | +0.0% | +0.0% |
| rate0.75_s12 | 0.0 | +0.0% / +0.0% | 99.9 | -39.0%*** / -40.9%*** | no | +0.0% / -33.8% | +0.0% | +0.0% |
| rate1.0_s4 | 0.0 | +0.0% / +0.0% | 19.9 | -14.3%** / -14.4%*** | no | +0.0% / -25.8% | +0.0% | +0.0% |
| rate1.0_s12 | 0.0 | +0.0% / +0.0% | 99.6 | -23.3%*** / -19.9%*** | no | +0.0% / -22.7% | +0.0% | +0.0% |
| rate1.5_s4 | 0.0 | +0.0% / +0.0% | 20.0 | -11.3%*** / -10.5%** | no | +0.0% / -17.7% | +0.0% | +0.0% |
| rate3.0_s4 | 0.0 | +0.0% / +0.0% | 10.0 | -30.4%*** / -29.5%*** | avg_slowdown@p0,p2; p95_slowdown@p2 | +0.0% / -26.5% | +0.0% | +0.0% |
| **fires without benefit** | | | | | | | | |
| bursty_high_s64 | 245.3 | -0.7% / +3.4% | 431.2 | +0.4% / +3.7% | avg_slowdown@p2 | +0.5%† / -0.4%† | +7.2% | +3.5% |

_Stars: \* p<0.05, \*\* p<0.01, \*\*\* p<0.001 (exact sign test, n=30 paired, tie-tolerant). avg_wait/migrations/scan-work %% are averaged across penalty 0 and 2; † marks a cell where penalty 0 and penalty 2 have OPPOSITE signs, so the average shown understates or masks a real per-penalty reversal -- see the per-penalty appendix tables (results_task6_confirmation_MAIN_TABLE.csv, _TABLE_original.csv, _TABLE_runnerup.csv) for the exact p0/p2 values. 'orig harm metric(s)' names which metric(s) triggered any_harm=True for the ORIGINAL detector and at which penalty (p0/p2) -- this is independent of the p95_wait column, so a row can show p95_wait improving and still be flagged harmful because of a DIFFERENT metric (see the worked explanation in the session notes). Groups: **final helps** = significant p95_wait improvement at either penalty; **final silent** = detector never fires (<0.5 fires/run average); **fires without benefit** = fires but no significant p95_wait improvement._
