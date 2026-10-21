# F3 — Order-flow persistence
Status: DRAFT / implementation HOLD. Independent offline hypothesis. No LIVE changes.

Hypothesis: in liquid tight-spread markets, sustained aggressive buying coupled with ask depletion
and limited replenishment may predict continuation that exceeds round-trip costs.
Opposite case: pressure is already priced in, reverses, or is too small after costs.

Decision-time inputs: signed executed trade value over predefined past windows, depth changes,
replenishment/depletion and microprice displacement. Exchange and receive timestamps required.
Only information received before the decision is eligible. Sign classification must be documented.

Data gate: confirm synchronized trades/orderbook history, depth coverage, sequence gaps and retention.
Current repo contains recorder/feature tooling under scalp/research; that is not evidence that suitable
server data exists. Missing data => DATA_INSUFFICIENT, not ZERO.

Outcome: same purchased quantity sold at a fixed future horizon using size-aware depth prices and
explicit fees/latency. Insufficient depth cannot silently receive best-level fills.

Before freezing: dataset manifest/dates, event start/dedup, feature windows, notional,
candidate/search budget, horizons, TRAIN/OOS boundary/purge, cost stress and acceptance thresholds.
F3 must record shared data/OOS use with F2/F4 and all attempted candidates.

Discovery: TRAIN only, small predefined candidate set. OOS untouched; no threshold rescue.
Report day/episode dependence, coin and winner concentration, tail loss, net per event and frequency.
Capital-day return requires explicit portfolio simulation.

Result: DATA_INSUFFICIENT / ZERO / CANDIDATE / SURVIVE within this procedure only.
SURVIVE qualifies for fresh forward shadow; no LIVE promotion.
