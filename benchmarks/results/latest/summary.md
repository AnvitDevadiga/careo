# Careo benchmark results

All regressions are **deliberately seeded** into fixtures. These numbers describe Careo on these fixtures only and do not predict effectiveness on real migrations.

- Careo 0.2.0 · commit `b489acee4bed8bb93310274c1e6c45bdee880929` · 2026-11-02T09:53:25.214204+00:00
- Seeds per case: 5 (method A is deterministic: 1 run per case) · operation budget per run (B, C, D): 400 · max steps (C, D): 25

| Method | Seeded detection | Cases found | False alarms (clean) | Median ops to 1st | Median len before → after shrink | Replay success | Search ops (budgeted) | Total ops incl. shrink+verify | Runtime s |
|---|---|---|---|---|---|---|---|---|---|
| A_unit_tests | 1/6 | 1/6 | 0/2 | n/a | n/a → n/a | n/a | 72 | 72 | 0.561 |
| B_single_operation | 5/30 | 1/6 | 0/10 | 20 | 1 → 1 | 5/5 | 14098 | 14150 | 43.779 |
| C_stateful | 30/30 | 6/6 | 0/10 | 104.0 | 11.5 → 11.5 | 30/30 | 7872 | 9260 | 20.363 |
| D_stateful_shrinking | 30/30 | 6/6 | 0/10 | 104.0 | 11.5 → 2.5 | 30/30 | 7872 | 117362 | 132.991 |

## Detection per case (detected runs / runs)

| Case | A_unit_tests | B_single_operation | C_stateful | D_stateful_shrinking |
|---|---|---|---|---|
| retry_cancel | 0/1 | 0/5 | 5/5 | 5/5 |
| slot_not_released | 0/1 | 0/5 | 5/5 | 5/5 |
| lost_update | 0/1 | 0/5 | 5/5 | 5/5 |
| pagination | 0/1 | 0/5 | 5/5 | 5/5 |
| missing_error | 1/1 | 5/5 | 5/5 | 5/5 |
| status_transition | 0/1 | 0/5 | 5/5 | 5/5 |
| clean_rewrite | 0/1 | 0/5 | 0/5 | 0/5 |
| reference_vs_self | 0/1 | 0/5 | 0/5 | 0/5 |

How to read this:

- B, C and D share the same budget of *search* operations (operations spent looking for the first difference). Shrinking and verification replays are extra work, shown in the total column.
- A is NOT budget-equal: it is the example project's fixed happy-path suite (its HTTP calls are counted as operations).
- D runs the same seeded search as C, so it detects exactly what C detects by construction. What D adds is shorter, verified counterexamples, at extra cost.
- Runs that could not reset or execute are counted in denominators as not detected and are listed by id in summary.json (`invalid_run_ids`).
