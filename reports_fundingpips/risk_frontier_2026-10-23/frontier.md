# FundingPips risk frontier (post-hoc, frozen v5 trades)

Generated: 2026-10-22T20:17:03.509825+00:00. Environment: **BACKTEST**, simulated. Attempts: 92,000 (942s).

This is a sensitivity analysis on frozen trades, **not** candidate selection.
Raising risk raises both pass and breach probability; it cannot create edge.

| Risk | Scenario | Cost | Attempts | Passes | Pass rate | Firm breaches | Breach rate | Median days to pass | p95 max DD |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 0.50% | firm_rules_only | fp10_base | 4,600 | 303 | 6.59% | 74 | 1.61% | 501 | 13.29% |
| 0.50% | firm_rules_only | fp10_stress1.5 | 4,600 | 169 | 3.67% | 162 | 3.52% | 448 | 13.58% |
| 0.50% | primary | fp10_base | 4,600 | 100 | 2.17% | 0 | 0.00% | 676 | 8.29% |
| 0.50% | primary | fp10_stress1.5 | 4,600 | 42 | 0.91% | 0 | 0.00% | 575 | 8.62% |
| 0.75% | firm_rules_only | fp10_base | 4,600 | 618 | 13.43% | 254 | 5.52% | 340 | 16.36% |
| 0.75% | firm_rules_only | fp10_stress1.5 | 4,600 | 526 | 11.43% | 394 | 8.57% | 364 | 16.71% |
| 0.75% | primary | fp10_base | 4,600 | 66 | 1.43% | 0 | 0.00% | 270 | 8.20% |
| 0.75% | primary | fp10_stress1.5 | 4,600 | 30 | 0.65% | 0 | 0.00% | 291 | 8.15% |
| 1.00% | firm_rules_only | fp10_base | 4,600 | 774 | 16.83% | 380 | 8.26% | 225 | 17.20% |
| 1.00% | firm_rules_only | fp10_stress1.5 | 4,600 | 658 | 14.30% | 580 | 12.61% | 234 | 17.39% |
| 1.00% | primary | fp10_base | 4,600 | 0 | 0.00% | 0 | 0.00% | nan | 5.27% |
| 1.00% | primary | fp10_stress1.5 | 4,600 | 0 | 0.00% | 0 | 0.00% | nan | 5.04% |
| 1.50% | firm_rules_only | fp10_base | 4,600 | 978 | 21.26% | 644 | 14.00% | 133 | 18.26% |
| 1.50% | firm_rules_only | fp10_stress1.5 | 4,600 | 821 | 17.85% | 888 | 19.30% | 126 | 18.33% |
| 1.50% | primary | fp10_base | 4,600 | 0 | 0.00% | 0 | 0.00% | nan | 3.89% |
| 1.50% | primary | fp10_stress1.5 | 4,600 | 0 | 0.00% | 0 | 0.00% | nan | 3.89% |
| 2.00% | firm_rules_only | fp10_base | 4,600 | 894 | 19.43% | 897 | 19.50% | 80 | 19.08% |
| 2.00% | firm_rules_only | fp10_stress1.5 | 4,600 | 789 | 17.15% | 1,182 | 25.70% | 80 | 19.07% |
| 2.00% | primary | fp10_base | 4,600 | 0 | 0.00% | 0 | 0.00% | nan | 4.76% |
| 2.00% | primary | fp10_stress1.5 | 4,600 | 0 | 0.00% | 0 | 0.00% | nan | 4.77% |

A simulated pass is not a FundingPips pass. No order was sent.
