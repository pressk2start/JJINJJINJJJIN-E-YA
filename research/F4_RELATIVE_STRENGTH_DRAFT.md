# F4 — Cross-sectional relative strength
Status: DRAFT / implementation HOLD. Independent offline hypothesis. No LIVE changes.

Hypothesis: coin returns exceeding a contemporaneous, point-in-time market basket may contain
persistent relative demand not explained by the common market move. Absolute pump thresholds alone
do not test this. Alternative: residual strength is mean-reverting or disappears after costs.

Decision-time inputs: past coin returns minus a frozen market reference, rank among eligible coins,
and past liquidity/spread. Basket weights, return window and eligibility rules must be fixed.
No future constituents, volume or closing prices may determine an earlier decision.

Data gate: synchronized historical prices for all eligible coins, point-in-time listing/universe
information and executable quote/depth coverage. Inspect repository collectors first; do not infer
data availability from scripts. Missing required data => DATA_INSUFFICIENT.

Outcome: same purchased quantity liquidated at a predefined future horizon, with depth, fees and
latency. Compare common-market exposure and a liquid baseline as well as raw net expectancy.

Before freezing: dataset manifest/dates, universe/basket weights, event/dedup rules, notional,
feature/search budget, horizons, TRAIN/OOS dates/purge, cost stress and acceptance thresholds.
Coordinate and record OOS reuse across F2/F3/F4; cross-sectional observations are not independent.

Discovery: TRAIN only; frozen candidate cap. OOS once, no best-result repicking.
Report day/episode-cluster uncertainty, concentration by coin/date/top winners, drawdowns,
net per event and opportunity frequency. Portfolio metrics require position/capital rules.

Result: DATA_INSUFFICIENT / ZERO / CANDIDATE / SURVIVE within this procedure only.
SURVIVE qualifies for fresh forward shadow; no LIVE promotion.
