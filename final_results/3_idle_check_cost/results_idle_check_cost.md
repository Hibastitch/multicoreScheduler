Measurement only -- see this script's module docstring. Safety check PASSED: every recomputed value (3240 total) matched the committed v4 confirmation per-seed CSVs exactly; these are the same runs behind the published results, not a fresh sample.

| workload | penalty | idle_check_runs | idle_check_cores_read | mean cores/check | baseline sched_cores_scanned | variant sched_cores_scanned | idle-check as % of baseline |
|---|---|---|---|---|---|---|---|
| stacked_medium | 0.0 | 17.000 | 24.1 | 1.42 | 47130.4 | 49461.6 | 0.0511% |
| stacked_medium | 0.5 | 17.000 | 24.3 | 1.43 | 47157.6 | 49417.0 | 0.0515% |
| stacked_medium | 2.0 | 17.000 | 25.1 | 1.48 | 46803.6 | 49263.6 | 0.0537% |
| stacked_high | 0.0 | 13.000 | 185.6 | 14.28 | 12263.9 | 14032.7 | 1.5134% |
| stacked_high | 0.5 | 13.000 | 196.4 | 15.11 | 12344.6 | 13694.3 | 1.5910% |
| stacked_high | 2.0 | 13.000 | 226.7 | 17.44 | 11865.4 | 13131.4 | 1.9103% |
| rate1.5_s12 | 0.0 | 10.000 | 11.7 | 1.17 | 44491.0 | 45784.1 | 0.0262% |
| rate1.5_s12 | 0.5 | 10.000 | 11.6 | 1.16 | 44487.6 | 45756.7 | 0.0261% |
| rate1.5_s12 | 2.0 | 10.000 | 12.0 | 1.20 | 44529.7 | 45747.8 | 0.0269% |
| rate3.0_s12 | 0.0 | 10.000 | 11.6 | 1.16 | 42568.6 | 43802.8 | 0.0273% |
| rate3.0_s12 | 0.5 | 10.000 | 11.2 | 1.12 | 42704.5 | 43804.8 | 0.0263% |
| rate3.0_s12 | 2.0 | 10.000 | 11.2 | 1.12 | 42655.8 | 43769.9 | 0.0263% |
| bursty_high_s64 | 0.0 | 33.833 | 1077.7 | 31.85 | 83471.6 | 83436.7 | 1.2911% |
| bursty_high_s64 | 0.5 | 33.367 | 1064.3 | 31.90 | 83118.2 | 83160.9 | 1.2805% |
| bursty_high_s64 | 2.0 | 33.967 | 1084.6 | 31.93 | 83294.7 | 83256.5 | 1.3021% |
| bursty_high_s24 | 0.0 | 0.000 | 0.0 | 0.00 | 48563.8 | 48563.8 | 0.0000% |
| bursty_high_s24 | 0.5 | 0.033 | 1.1 | 32.00 | 48438.0 | 48438.0 | 0.0022% |
| bursty_high_s24 | 2.0 | 0.033 | 1.1 | 32.00 | 48464.2 | 48464.2 | 0.0022% |
