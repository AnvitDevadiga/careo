# Report schemas

All reports are JSON, UTF-8, versioned, and written atomically. Environment
variable **values** are never written. Readers must reject unknown schema versions.

## `report.json` (one-shot `careo compare`), `schema_version: 1`

| Key | Meaning |
|---|---|
| `schema_version`, `tool` | `1`, `"careo"` |
| `started_at`, `finished_at` | ISO-8601 UTC |
| `config_path` | absolute path of the config used (for replay) |
| `policy_fingerprint` | SHA-256 of programs, limits, comparison policy, normalization rules |
| `cases_fingerprint`, `cases_source` | SHA-256 of case ids and inputs; where they came from |
| `environment` | Careo version, Python version, platform, machine |
| `reference`, `candidate` | name, command, cwd, env *names*, resolved executable, SHA-256 of executable, argument files and `identity_files` |
| `limits`, `comparison_policy`, `normalization_rules`, `normalization_totals`, `warnings` | the exact policy applied and how often each rule fired |
| `result` | `total_cases`, `outcomes` (count per outcome), `conclusion`, `summary`, `exit_code` |
| `cases[]` | `id`, `description`, `input`, `outcome`, `notes`, `differences[]`, `reference_runs[]`, `candidate_runs[]` |

A difference is `{path, kind, reference?, candidate?, detail?}`. A missing key means
that side had no value at the path. Kinds: `value_changed`, `type_changed`,
`missing_in_candidate`, `extra_in_candidate`, `null_vs_absent`, `array_order`,
`array_elements`, `exit_code`, `stderr`.

## `scenario-report.json` (`careo scenario run`), `schema: "careo.scenario-report"`, `schema_version: 1`

| Key | Meaning |
|---|---|
| `spec_path`, `spec_fingerprint`, `generation` | spec used and generation settings (seed, budgets, shrink, replay checks) |
| `environment`, `reference`, `candidate` | as above, plus `files_sha256` of service files |
| `state`, `isolation` | reset strategy and what was actually reset and verified |
| `comparison_policy`, `normalization_rules`, `normalization_totals` | the applied policy |
| `outcome`, `exit_code`, `summary`, `notes`, `warnings` | run verdict (see [stateful.md](stateful.md)) |
| `stats` | scenarios generated, operations executed, search operations, requests, verified resets, id translations, time and operations to first difference, runtime |
| `original_sequence` | first failing sequence (`length`, `steps`) or `null` |
| `minimized_sequence` | `length`, `steps`, `method`, `claim`, `hypothesis_shrunk_length`, `ddmin_replays` |
| `verification` | `checks`, `reproduced`, `signatures` from clean-state replays |
| `evidence[]` | per step of the last verification replay: `step`, `verdict`, `differences`, `notes`, `produced`, `reference`/`candidate` observations, `state` (compared rows after the step) |
| `id_mapping` | logical id → actual id on each side, from the final replay |

Steps use logical references, e.g. `{"op": "cancel_booking", "params": {"id": {"$ref": "booking#1"}}}`.

## `regression.json` (`schema: "careo.scenario"`, `schema_version: 1`)

`spec_path`, `spec_relative`, `spec_fingerprint`, `steps`, `recorded_outcome`,
`expected_failure_signature`. `careo scenario replay` reports drift if the spec
fingerprint changed and exits 0 only if the recorded signature reproduces.

## Benchmark records

`benchmarks/results/latest/raw-records.jsonl` has one JSON object per run (case, method,
seed, outcome, operations, timings, sequence lengths, verification).
`summary.json` is recomputed from it by `careo bench`, and checked in CI by
`benchmarks/check_results.py` and `scripts/build_site_data.py --check`.
