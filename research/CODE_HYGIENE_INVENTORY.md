# Runtime retirement inventory — initial static review

Basis: bot.py blob 312f5701d43a3ccb9df5aa71ce17c90e2a351917, main 07932c5.
Scope: reference inspection, not runtime verification. No claim of complete dead-code detection.

| Component | Observed dependency | Retirement action |
|---|---|---|
| A_CLEAN / A×A2 registry | bot.py:13780/13790; enabled=False, shadow_enabled=True; shared clean exit profile and paired/export state | Preserve evidence, stop NEW shadow generation in a separate tested change, drain existing VPs |
| A2 audit | definition 2262, report call 2855 | Extract into explicit offline/archive report with snapshot input |
| Common cohort report | definition 2421, report call 2859; includes CONTROL and integrity checks | Split integrity checks from archival presentation; retain active measurement checks |
| EC_A | deprecated registry 13691; profile 11834; report lists 16575/16598/16628 and diagnostic 16788 | Already lacks shadow_enabled in registry; archive presentation before removing definitions; shared check_fn stays |
| PP30/PP40 | deprecated entries 13676/13683, no shadow_enabled | Verify persistence/report consumers, then retire registry entries; do not infer absence of references from no new trades |
| OBSLIP | deprecated entry 13925, shared check_fn/profile, historical report naming 11281 | Separate historical data/report, retain shared helpers; size/field contract must be reviewed |
| Survival | analysis 17059 writes scoring cache 17171; predictor 17199 called at 19789, writes pre survival_score | NOT isolated report-only code; trace pre/log consumers before extraction; cache producer/consumer must stay consistent |
| C1 | literal C1_v1 absent from inspected bot.py | Absence of string is NOT proof of dead code; inspect standalone research scripts and scheduler before retirement |
| Export | definition 15869, runtime report call 15929 | KEEP until C2/F2 data source and server persistence are verified |
| A purity | module import line 19, report classification 2231 | Keep classifier available for archive/regression; avoid unconditional import after all runtime consumers retired |
| Orders/positions/exit/P1 | shared runtime and accounting | No deletion in first hygiene change |

## Findings that constrain cleanup

- BLOCK/ENABLE/SHADOW are retrospective labels, not execution flags.
- Negative historical PnL or small n is not a dependency analysis.
- trade_records is bounded, not an append-only full archive; preserve original server files separately.
- Existing purity tests cover classifier behavior, not order/exit/report equivalence.
- Moving code into a module does not remove runtime cost if it is still imported/called.
- Archived verdict remains evidence of the original experiment even if later diagnostics change.

## First implementation boundary

A/A2 new shadow generation shutdown only, after data preservation is verified.
Verify registry skip, no new VP, existing VP drainage, CONTROL unchanged, old state loading,
report handles archived routes and no fresh-pair target requests. Use offline fixtures, not live orders.
Do not clear legacy records or remove shared factory/exit helpers.

Next separate changes: archival presentation extraction; deprecated registry retirement; Survival extraction
only after its predictor consumers are mapped. No percentage line-reduction forecast.
