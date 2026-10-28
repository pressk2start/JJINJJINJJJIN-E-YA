# H18 cache audit — user-run result, 2026-10-28 KST

## Provenance and scope

- User-pasted output from pinned cache auditor c54580707e46dfe50be82db49ce6efa54205789e (discovery-cache-audit-v1).
- Cache folder: edge_discovery_result_d880; input state: READY.
- 390,327 sampled decision rows; four walk-forward folds.
- The archive date range was not included in this brief and remains to be read from discovery_report.json.
- This cohort must not be merged with the previously reported 415,550-row/five-fold cohort or its last-TRAIN winner count of 219.
- No raw archive, account, or order evidence was downloaded for this review. The result below is a cache-internal diagnostic, not independent validation of historical execution.

## Resolution and training

| Item | Count |
| --- | ---: |
| 15s observation rows | 390,327 |
| Resolved labels | 188,286 |
| Unresolved labels | 202,041 (51.762%) |
| Unresolved without entry evidence | 124,591 |
| Unresolved after entry evidence | 77,450 |
| Last TRAIN resolved rows | 130,860 |
| Last TRAIN winners | 196 (0.1498%) |
| Actual model-fit sample | 4,000, containing 6 winners |
| Actual interaction-screen sample | 800, containing 1 winner |

The uniform reservoir represents the rare positive class approximately at its original frequency, but retains too few positive cases to strongly assess multifeature winner patterns. This does not establish absence of predictive information. A winner-preserving training experiment must account for changed sample prevalence and keep evaluation at natural frequency.

"No entry evidence" does not establish a confirmed unfilled order. Unknown outcomes must not be changed to zero, losses, or recycled free capital.

## Cost diagnostics on resolved 15s labels

The flat-book cost is derived from the observed decision-time spread and configured fees under unchanged top-level prices, with no additional depth cost. It is not an independent reconstruction of delayed entry and exit.

| Market | Resolved | Unresolved | Spread median (bp) | Entry-hint median (bp) | Flat-book mean cost (%) | Mean 15s net (%) | Entry-VWAP median (KRW) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KRW-ADA | 10,926 | 43,002 | 33.333 | 21.647 | 0.44075 | -0.439 | 301 |
| KRW-DOGE | 24,027 | 50,679 | 92.593 | 51.106 | 1.00865 | -1.007 | 109 |
| KRW-XLM | 18,108 | 43,350 | 40.984 | 25.460 | 0.50430 | -0.504 | 245 |
| KRW-XPL | 5,974 | 33,750 | 91.743 | 50.685 | 0.90219 | -0.904 | 107 |
| KRW-XRP | 129,251 | 31,260 | 5.650 | 7.827 | 0.16096 | -0.162 | 1,775 |

All five mean label returns are within approximately 0.2 bp of the negative flat-book cost benchmark. This strongly supports a cost-dominated unconditional mean in these resolved samples; it neither proves all label calculations correct nor rules out a conditional predictive pattern. Mean comparisons may conceal adverse-selection or missingness effects.

XPL's observed historical spread and median entry VWAP support the high-tick-cost explanation. A later 97-KRW quote is a different observation and cannot substitute for historical book evidence.

The auditor checked 390,327 entry-hint/touch-floor pairs and found zero lower-bound violations. This establishes that specific internal numerical consistency check only.

## Supported condition and account limitations

- Supported-condition counts: [0, 0, 1, 0].
- Support means at least 20 completed simulated trades across at least two selection dates. It does not require positive profit.
- The third fold contains one supported condition. Its subsequent CHECK CSV has now been provided; the result is incomplete, as detailed below.
- The last unfiltered 15s baseline closed zero trades and selected five unknown exposures.
- With one open position per market, five unresolved exposures can block all five markets even while cash remains. This is incomplete account evaluation, not a proven zero-profit or loss result.
- Rejected candidates' detailed selection summaries were not retained by discovery v1, so their individual rejection causes cannot be reconstructed from the brief.

## Selected condition CHECK result — supplied 2026-10-28 KST

The user supplied these two rows from this cache's conditions.csv:

| Fold | Family | Hold | TRAIN sample score quantile | Selection reason | Capital (KRW) | Closed | Unknown selected | Complete | Net PnL / win rate |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- | --- |
| 3 | score | 60s | 0.9 | validation_net | 1,000,000 | 0 | 5 | False | unavailable |
| 3 | score | 60s | 0.9 | validation_net | 2,000,000 | 0 | 5 | False | unavailable |

The threshold is the 90th percentile of model scores in the fitted TRAIN sample. It is not a 90% win probability and does not imply that exactly 10% of CHECK rows pass it.

Both capital scenarios have zero completed trades and five unknown exposures. Blank PnL and win-rate fields mean unavailable, not zero. These are two funding scenarios on the same data and policy, not two independent validation results.

The 15s last-TRAIN fit/screen winner counts of 6/1 above must not be attributed to this 60s condition. Its own horizon-specific sampling counts are separate.

The account model reserves cash before reading future outcome availability and allows one open position per market. Unresolved exposures can lock all five markets while cash remains; increasing nominal capital alone does not resolve missing execution evidence. Do not retroactively treat these unknown exposures as confirmed unfilled orders or release them as free cash.

## Bounded disposition and next experiment

This run establishes a cost-dominated unconditional mean in resolved labels and an incomplete subsequent funded evaluation of the one supported condition. It does not establish profitable alpha, a loss on that condition, correctness of every historical execution label, or impossibility of high-frequency trading.

No additional raw replay or repeated H17 run is needed merely to interpret these two rows. The archive calendar range remains unconfirmed in the pasted brief; do not assign the earlier cohort's dates to it.

The next experiment should prioritize empirically lower-cost markets and adequate entry/exit observation quality before treating funded account results as complete. Winner-preserving training is a separate improvement that must retain natural-frequency evaluation and account for sampling prevalence.

Keep H3 unchanged and LIVE entry blocked. No new strategy or live listing guard is approved by these diagnostics.
