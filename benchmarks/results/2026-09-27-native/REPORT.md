# Graf versus native Codex — September 26–27, 2026

The tables below show **Graf alongside all 35 tested native Codex configurations**, across documentation and source-code questions. **32 configurations completed both workloads; 835 native answers were graded.** Native Codex used its own file/shell tools, with no Graf, MCP, plugins, personal skills or memory. GPT-6-astra high, max and ultra were excluded as requested; xhigh was included.

For the same requested model and effort, **GPT-6-astra medium**, Graf used **16,340 mean tokens for documents** versus **56,113 for native Codex**, with **100% strict passes in both runs**. On code, Graf used **64,613 mean tokens** versus **75,524 for native Codex**; strict passes were **8/9 for Graf** and **10/10 for native Codex**. The tables include every other model and effort, including configurations that performed better or worse.

*Comparison context:* Graf is the existing hybrid-engine pilot; native Codex is the September 26–27 baseline run. Graf's validated denominators are 15 document and 9 code questions; native Codex uses 16 and 10 corrected questions. The source snapshots contain 189 and 192 files respectively. Graf's pilot used a six-response controller; native Codex had native tools and a 20-minute timeout. These observed results compare the workflows tested; they do not isolate Graf as the only changed variable.

**Reading the numbers:** strict passes require correctness, completeness, source support, exact quotations and appropriate abstention. Tokens include all accounted native child sessions. **Total tokens = input + output**; cached input is already included in input. Mean tokens include unsuccessful graded answers. Times are seconds, excluding separate grading and Graf's one-time setup costs (listed in the [Graf pilot report](../2026-09-26/REPORT.md)). Total time is the sum of case durations, not the parallel batch's wall-clock duration.

#### Documents — 1,000 operational records

| Approach / model | Effort | Strict passes ↑ | Mean tokens ↓ | Input tokens | Cached input | Output tokens | Total tokens | Median time (s) ↓ | Total time (s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **Graf + GPT-6-astra** | medium | 15/15 (100.0%) | 16,340 | 241,778 | 100,992 | 3,324 | 245,102 | 13.91 | 218.77 |
| Controlled file tools + GPT-6-astra | medium | 15/15 (100.0%) | 57,567 | 858,944 | 381,184 | 4,565 | 863,509 | 30.53 | 476.91 |
| gpt-6-astra | low | 16/16 (100.0%) | 54,829 | 870,435 | 600,192 | 6,829 | 877,264 | 23.02 | 352.40 |
| gpt-6-astra | medium | 16/16 (100.0%) | 56,113 | 890,971 | 623,232 | 6,837 | 897,808 | 21.32 | 352.65 |
| gpt-6-astra | xhigh | 16/16 (100.0%) | 69,168 | 1,095,305 | 748,800 | 11,381 | 1,106,686 | 29.76 | 490.40 |
| gpt-6-sol | low | 16/16 (100.0%) | 49,037 | 777,836 | 531,968 | 6,755 | 784,591 | 15.99 | 271.27 |
| gpt-6-sol | medium | 15/16 (93.8%) | 65,122 | 1,032,562 | 806,528 | 9,391 | 1,041,953 | 19.42 | 381.69 |
| gpt-6-sol | high | 16/16 (100.0%) | 63,989 | 1,012,482 | 772,736 | 11,335 | 1,023,817 | 22.00 | 336.57 |
| gpt-6-sol | xhigh | 14/16 (87.5%) | 70,114 | 1,108,047 | 849,024 | 13,769 | 1,121,816 | 23.72 | 459.81 |
| gpt-6-sol | max | 16/16 (100.0%) | 73,247 | 1,152,513 | 886,400 | 19,444 | 1,171,957 | 30.93 | 735.78 |
| gpt-6-sol | ultra | Halted | — | — | — | — | — | — | — |
| gpt-6-luna | low | 7/16 (43.8%) | 36,574 | 581,258 | 403,712 | 3,934 | 585,192 | 11.38 | 153.25 |
| gpt-6-luna | medium | 14/16 (87.5%) | 59,767 | 948,504 | 702,208 | 7,772 | 956,276 | 15.11 | 252.20 |
| gpt-6-luna | high | 15/16 (93.8%) | 62,644 | 991,916 | 742,656 | 10,389 | 1,002,305 | 17.59 | 296.61 |
| gpt-6-luna | xhigh | 15/16 (93.8%) | 66,765 | 1,048,828 | 814,080 | 19,407 | 1,068,235 | 32.91 | 536.52 |
| gpt-6-luna | max | 15/16 (93.8%) | 62,804 | 982,075 | 729,600 | 22,792 | 1,004,867 | 35.10 | 591.18 |
| gpt-5.6-sol | low | 16/16 (100.0%) | 43,604 | 689,123 | 415,104 | 8,536 | 697,659 | 16.84 | 267.57 |
| gpt-5.6-sol | medium | 15/16 (93.8%) | 53,243 | 842,229 | 510,592 | 9,656 | 851,885 | 16.89 | 277.74 |
| gpt-5.6-sol | high | 14/16 (87.5%) | 52,014 | 821,718 | 548,736 | 10,510 | 832,228 | 19.59 | 303.58 |
| gpt-5.6-sol | xhigh | 15/16 (93.8%) | 66,621 | 1,051,198 | 721,664 | 14,742 | 1,065,940 | 28.68 | 448.44 |
| gpt-5.6-sol | max | 15/16 (93.8%) | 76,494 | 1,202,182 | 893,952 | 21,714 | 1,223,896 | 32.61 | 545.28 |
| gpt-5.6-sol | ultra | Halted | — | — | — | — | — | — | — |
| gpt-5.6-terra | low | 13/16 (81.2%) | 53,547 | 848,046 | 622,336 | 8,699 | 856,745 | 16.36 | 261.51 |
| gpt-5.6-terra | medium | 16/16 (100.0%) | 56,433 | 893,017 | 621,312 | 9,904 | 902,921 | 17.64 | 284.15 |
| gpt-5.6-terra | high | 16/16 (100.0%) | 59,596 | 943,255 | 645,632 | 10,286 | 953,541 | 18.19 | 299.01 |
| gpt-5.6-terra | xhigh | 15/16 (93.8%) | 65,456 | 1,034,615 | 779,776 | 12,681 | 1,047,296 | 20.54 | 340.17 |
| gpt-5.6-terra | max | 16/16 (100.0%) | 100,693 | 1,577,072 | 1,239,296 | 34,016 | 1,611,088 | 42.58 | 733.28 |
| gpt-5.6-terra | ultra | Halted | — | — | — | — | — | — | — |
| gpt-5.6-luna | low | 15/16 (93.8%) | 56,432 | 892,849 | 579,840 | 10,067 | 902,916 | 20.74 | 322.20 |
| gpt-5.6-luna | medium | 13/16 (81.2%) | 78,207 | 1,238,524 | 784,640 | 12,787 | 1,251,311 | 21.67 | 369.17 |
| gpt-5.6-luna | high | 12/16 (75.0%) | 77,334 | 1,220,240 | 844,032 | 17,099 | 1,237,339 | 27.66 | 441.83 |
| gpt-5.6-luna | xhigh | 14/16 (87.5%) | 86,186 | 1,356,465 | 950,784 | 22,505 | 1,378,970 | 33.12 | 589.71 |
| gpt-5.6-luna | max | 14/16 (87.5%) | 98,583 | 1,539,881 | 1,063,680 | 37,448 | 1,577,329 | 52.59 | 828.82 |
| gpt-5.5 | low | 15/16 (93.8%) | 49,918 | 784,594 | 582,144 | 14,089 | 798,683 | 22.45 | 352.50 |
| gpt-5.5 | medium | 16/16 (100.0%) | 59,774 | 937,064 | 685,056 | 19,315 | 956,379 | 27.88 | 464.47 |
| gpt-5.5 | high | 15/16 (93.8%) | 81,126 | 1,271,571 | 888,832 | 26,453 | 1,298,024 | 38.20 | 578.88 |
| gpt-5.5 | xhigh | 16/16 (100.0%) | 94,251 | 1,473,309 | 1,107,456 | 34,707 | 1,508,016 | 46.58 | 759.14 |

#### Source code — real repository files

| Approach / model | Effort | Strict passes ↑ | Mean tokens ↓ | Input tokens | Cached input | Output tokens | Total tokens | Median time (s) ↓ | Total time (s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **Graf + GPT-6-astra** | medium | 8/9 (88.9%) | 64,613 | 576,290 | 199,680 | 5,226 | 581,516 | 41.17 | 490.35 |
| Controlled file tools + GPT-6-astra | medium | 5/9 (55.6%) | 80,823 | 722,811 | 329,472 | 4,592 | 727,403 | 44.99 | 402.99 |
| gpt-6-astra | low | 9/10 (90.0%) | 66,238 | 654,361 | 472,320 | 8,018 | 662,379 | 32.91 | 358.63 |
| gpt-6-astra | medium | 10/10 (100.0%) | 75,524 | 746,340 | 561,280 | 8,901 | 755,241 | 29.03 | 366.63 |
| gpt-6-astra | xhigh | 10/10 (100.0%) | 90,280 | 883,768 | 612,992 | 19,032 | 902,800 | 65.96 | 701.37 |
| gpt-6-sol | low | 9/10 (90.0%) | 77,901 | 770,098 | 587,136 | 8,912 | 779,010 | 27.96 | 274.55 |
| gpt-6-sol | medium | 9/10 (90.0%) | 102,583 | 1,012,026 | 779,264 | 13,802 | 1,025,828 | 45.33 | 479.65 |
| gpt-6-sol | high | 8/10 (80.0%) | 121,626 | 1,197,261 | 937,216 | 18,995 | 1,216,256 | 48.09 | 475.49 |
| gpt-6-sol | xhigh | 9/10 (90.0%) | 171,608 | 1,688,425 | 1,386,752 | 27,659 | 1,716,084 | 57.65 | 625.73 |
| gpt-6-sol | max | 9/10 (90.0%) | 145,787 | 1,418,588 | 1,107,840 | 39,286 | 1,457,874 | 82.69 | 974.74 |
| gpt-6-sol | ultra | Halted | — | — | — | — | — | — | — |
| gpt-6-luna | low | 4/10 (40.0%) | 72,633 | 716,650 | 514,304 | 9,678 | 726,328 | 23.70 | 237.25 |
| gpt-6-luna | medium | 5/10 (50.0%) | 91,844 | 907,177 | 675,840 | 11,258 | 918,435 | 27.20 | 293.98 |
| gpt-6-luna | high | 9/10 (90.0%) | 123,873 | 1,222,106 | 950,272 | 16,622 | 1,238,728 | 39.12 | 400.79 |
| gpt-6-luna | xhigh | 8/10 (80.0%) | 176,741 | 1,723,973 | 1,359,104 | 43,441 | 1,767,414 | 86.84 | 957.65 |
| gpt-6-luna | max | 9/10 (90.0%) | 182,847 | 1,773,964 | 1,423,872 | 54,504 | 1,828,468 | 115.16 | 1250.38 |
| gpt-5.6-sol | low | 7/10 (70.0%) | 78,515 | 773,487 | 540,416 | 11,660 | 785,147 | 31.21 | 314.71 |
| gpt-5.6-sol | medium | 7/10 (70.0%) | 120,494 | 1,187,901 | 907,008 | 17,041 | 1,204,942 | 42.35 | 421.11 |
| gpt-5.6-sol | high | 7/10 (70.0%) | 127,160 | 1,248,962 | 981,760 | 22,633 | 1,271,595 | 52.27 | 585.56 |
| gpt-5.6-sol | xhigh | 7/10 (70.0%) | 212,198 | 2,088,316 | 1,684,864 | 33,661 | 2,121,977 | 62.86 | 799.73 |
| gpt-5.6-sol | max | 7/10 (70.0%) | 345,999 | 3,395,681 | 2,904,320 | 64,310 | 3,459,991 | 122.19 | 1366.90 |
| gpt-5.6-sol | ultra | Halted | — | — | — | — | — | — | — |
| gpt-5.6-terra | low | 8/10 (80.0%) | 116,102 | 1,149,745 | 869,632 | 11,275 | 1,161,020 | 30.51 | 288.01 |
| gpt-5.6-terra | medium | 6/10 (60.0%) | 106,283 | 1,050,620 | 799,232 | 12,206 | 1,062,826 | 27.73 | 295.12 |
| gpt-5.6-terra | high | 7/10 (70.0%) | 138,988 | 1,374,059 | 1,066,496 | 15,820 | 1,389,879 | 35.14 | 367.73 |
| gpt-5.6-terra | xhigh | 9/10 (90.0%) | 185,106 | 1,826,232 | 1,454,592 | 24,832 | 1,851,064 | 51.24 | 553.00 |
| gpt-5.6-terra | max | 7/10 (70.0%) | 338,407 | 3,315,797 | 2,844,928 | 68,269 | 3,384,066 | 120.92 | 1363.22 |
| gpt-5.6-terra | ultra | Halted | — | — | — | — | — | — | — |
| gpt-5.6-luna | low | 5/10 (50.0%) | 109,950 | 1,087,303 | 753,152 | 12,197 | 1,099,500 | 31.79 | 316.05 |
| gpt-5.6-luna | medium | 6/10 (60.0%) | 138,469 | 1,367,827 | 950,272 | 16,866 | 1,384,693 | 40.03 | 416.62 |
| gpt-5.6-luna | high | 6/10 (60.0%) | 202,586 | 1,996,990 | 1,539,072 | 28,866 | 2,025,856 | 65.18 | 679.25 |
| gpt-5.6-luna | xhigh | 6/10 (60.0%) | 340,924 | 3,356,339 | 2,752,768 | 52,899 | 3,409,238 | 108.15 | 1111.45 |
| gpt-5.6-luna | max | 6/10 (60.0%) | 440,437 | 4,315,879 | 3,635,200 | 88,487 | 4,404,366 | 156.97 | 1770.11 |
| gpt-5.5 | low | 7/10 (70.0%) | 117,279 | 1,153,807 | 894,464 | 18,980 | 1,172,787 | 44.38 | 436.44 |
| gpt-5.5 | medium | 9/10 (90.0%) | 157,980 | 1,556,757 | 1,290,240 | 23,042 | 1,579,799 | 52.99 | 514.60 |
| gpt-5.5 | high | 7/10 (70.0%) | 231,474 | 2,277,739 | 1,922,048 | 37,006 | 2,314,745 | 77.98 | 792.66 |
| gpt-5.5 | xhigh | 10/10 (100.0%) | 207,225 | 2,027,523 | 1,669,632 | 44,731 | 2,072,254 | 93.60 | 916.24 |

**Three halted configurations:** GPT-6-sol ultra, GPT-5.6-sol ultra and GPT-5.6-terra ultra each had an unfinished native child without a final token receipt. Their full-workload scores and totals are unavailable, not zero or estimated. Of 910 scheduled native questions, 835 completed and were graded, 3 failed accounting, and 72 were skipped after their configuration halted. No failed attempt was retried; partial per-case evidence is retained.

**Measurement notes:** the first 29 native attempts were sequential; the rest used up to six concurrent configurations. Per-case data identifies that split; the table's observed timing includes shared-host/provider contention. This is one repetition with authored questions and automated GPT-6-astra medium judging; human review remains pending. Graf setup and grading costs are documented in the [Graf pilot report](../2026-09-26/REPORT.md); native judging used 13,136,842 input tokens (6,641,664 cached), 89,436 output tokens, and 6,187.38 summed seconds across 835 calls, separately from the answering numbers above.

[**Exact native totals (CSV)**](summary.csv) · [**Every native case and execution cohort**](per-case-execution.csv) · [**Report and provenance**](REPORT.md) · [**Native methodology**](../../NATIVE.md)

## Evidence

[Provenance and exported-file hashes](provenance.json) · [Separate judge usage](grading-usage.json). All retained answer and judge artifact hashes were verified before export. Full raw sessions remain in the local ignored evidence directory; this export contains summaries and per-case measurements, not the full raw distribution.
