# Calibration grid results (table form of Fig 0)

p95 wait % change vs baseline, mean over migration cost 0 and 0.5 ms, per (config, calibration workload). † marks a cell with disqualifying harm (any of p95_wait/avg_wait/avg_slowdown, corrected rule, penalty 0 or 0.5 ms only -- see calibration_harm_detail_v4.csv). "selection score" is the mean of stacked medium + stacked high at penalty 0 and 0.5 ms, the quantity the selection rule ranks configs by.

| # | config | stacked low | stacked medium | stacked high | bursty s24 | bursty s64 | rate sweep s4 @0.5 | rate sweep s12 @0.5 | rate sweep s4 @3.0 | rate sweep s12 @3.0 | selection score | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | q≥2 AND rate≥0.8 | +0.0 | -17.4 | -11.6 | +0.0 | +0.4 | +0.0 | +0.0 | -21.3 | -10.5 | -14.50 | harm-free |
| 2 | q≥2 OR rate≥0.8 | +3.0 | -16.7 | -17.9 | +6.6† | +0.1 | +14.7† | -25.1 | -29.8 | -2.7 | -17.26 | disqualified |
| 3 | q≥2 AND rate≥1.5 | +0.0 | -10.2 | -23.5 | +0.0 | +0.1 | +0.0 | +0.0 | +0.0 | -18.5 | -16.87 | harm-free (runner-up) |
| 4 | q≥2 OR rate≥1.5 | +3.0 | -16.7 | -11.4 | +0.0 | +0.4 | +14.7† | -25.1 | -21.3 | -4.2 | -14.05 | disqualified |
| 5 | q≥4 AND rate≥0.8 | +0.0 | -8.7 | -14.4 | +0.0 | +0.1 | +0.0 | +0.0 | +0.0 | -13.6 | -11.55 | harm-free |
| 6 | q≥4 OR rate≥0.8 | +0.0 | -16.9 | -19.1 | +6.6† | +0.1 | +0.0 | +0.0 | -29.8 | -7.3 | -18.01 | disqualified |
| 7 | q≥4 AND rate≥1.5 | +0.0 | -8.8 | -24.1 | +0.0 | +0.1 | +0.0 | +0.0 | +0.0 | -18.2 | -16.45 | harm-free |
| 8 | q≥4 OR rate≥1.5 | +0.0 | -7.9 | -13.8 | +0.0 | +0.1 | +0.0 | +0.0 | +0.0 | -13.5 | -10.86 | harm-free |
| 9 | q≥8 AND rate≥0.8 | +0.0 | +0.4 | -21.7 | +0.0 | -0.1 | +0.0 | +0.0 | +0.0 | -9.8 | -10.62 | harm-free |
| 10 | q≥8 OR rate≥0.8 | +0.0 | -16.9 | -19.1 | +6.6† | +0.1 | +0.0 | +0.0 | -29.8 | -7.3 | -18.01 | disqualified |
| 11 | q≥8 AND rate≥1.5 | +0.0 | +0.4 | -21.2 | +0.0 | -0.1 | +0.0 | +0.0 | +0.0 | -9.8 | -10.39 | harm-free |
| 12 | q≥8 OR rate≥1.5 | +0.0 | -10.2 | -23.6 | +0.0 | +0.2 | +0.0 | +0.0 | +0.0 | -18.5 | -16.91 | harm-free (SELECTED) |
