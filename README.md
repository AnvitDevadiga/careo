# Careo

**Catch what the AI rewrite broke.**

Careo is a differential testing tool. Give it an original program, a rewritten
program, and a set of inputs. It runs both on identical inputs, compares what
they observably do (JSON output, exit codes, optionally stderr), and writes a
reproducible report of every difference.

## Problem

Rewrites (by AI or by people) usually pass the happy-path tests and break the
edges: an off-by-one at a boundary, a `null` that became a missing field, an
integer that became a string, an error that became a crash. Careo treats the
original as the oracle and shows exactly where the candidate diverges.

## Installation

Python 3.11+, Linux or macOS.

```sh
pip install git+https://github.com/AnvitDevadiga/careo
# or, from a checkout:
uv sync && uv run careo --version
```

## Quick start

Each program reads one JSON value on stdin and writes one JSON value on stdout.
Any language works: Careo only starts processes.

```yaml
# careo.yaml
version: 1
reference: {name: original,  command: ["python3", "original.py"], cwd: .,
            identity_files: [lib]}   # extra code to hash for replay drift detection
candidate: {name: rewrite,   command: ["./rewrite"],             cwd: .}
cases: {file: cases.jsonl}        # one {"id": ..., "input": ...} per line
comparison:
  accepted_exit_codes: [0]        # exit codes that count as "ran normally"
  stderr: ignore                  # ignore | exact
  array_order: strict             # strict | ignore
  numbers: strict                 # strict (1 != 1.0) | numeric
  float_abs_tolerance: 0
  repeat: 1                       # >1 runs each case repeatedly to detect nondeterminism
limits: {timeout_seconds: 10, max_output_bytes: 1048576, max_input_bytes: 1048576}
normalize:                        # explicit, visible in every report
  - {id: ts, path: /generated_at, action: mask, reason: wall-clock time}
  - {id: tmp, path: /files/*/path, action: regex, pattern: "/tmp/[^/]+", replacement: "<tmp>", reason: temp dirs}
```

```sh
careo validate --config careo.yaml               # check config, run nothing
careo compare  --config careo.yaml --output-dir careo-report
careo inspect  careo-report/report.json [--all] [--case ID]
careo replay   --report careo-report/report.json [--config careo.yaml] [--output-dir DIR]
```

Exit codes for `compare` and `inspect`: `0` every case matched · `1` at least
one difference or failure · `2` invalid config, report or usage · `3` a
program could not be executed, or Careo hit an internal error. `replay` returns `0` if every outcome and
difference reproduced, `1` if not.

## Example difference

`examples/bulk-pricing` has a correct original and an AI-rewrite-style
candidate with a **deliberately seeded** bug (`>` instead of `>=` on discount
tiers). It is a constructed demo, not a real-world finding.

```console
$ careo compare --config examples/bulk-pricing/careo.yaml
careo 0.2.0: comparing 10 case(s)
  MATCH             happy-small
  MATCH             happy-bulk
  MATCH             zero-quantity
  MATCH             below-tier-1
  MISMATCH          at-tier-1
  MATCH             above-tier-1
  MISMATCH          at-tier-2
  MATCH             negative-quantity
  MATCH             bool-quantity
  MATCH             huge-quantity
DIFFERENCES_FOUND: MATCH=8, MISMATCH=2
2 of 10 cases did not match.

$ careo inspect careo-report/report.json
[MISMATCH] at-tier-1  input={"quantity": 100, "unit_price_cents": 100}
    /discount_percent: value_changed: 10 -> 0
    /total_cents: value_changed: 9000 -> 10000
```

## Stateful HTTP testing

Some regressions only show up after several operations. In the demo, creating,
cancelling and then *retrying* the cancellation behaves differently.
`careo scenario run` generates operation sequences with Hypothesis
(`RuleBasedStateMachine` and bundles), runs each one against both services from a
**verified** identical database state, and compares responses and persistent state
after every step. It then shrinks the failure, re-verifies it, and exports a
regression test.

```console
$ careo scenario run --spec examples/booking-api/careo-scenarios.yaml --output-dir out
careo 0.2.0: stateful run, seed 20261102, up to 200 scenarios x 25 steps
DIFFERENCE_CONFIRMED: A behavioral difference was found and its minimized sequence reproduced on every verification replay from a clean, verified state.
scenarios=381 operations=2962 resets=384 runtime=3.0s
original sequence: 13 operations; minimized: 3 operations; verified 3/3
  0. {"op": "create_booking", "params": {}, "query": {}, "body": {"customer": "ana", "slot": "09:00"}}
  1. {"op": "cancel_booking", "params": {"id": {"$ref": "booking#1"}}, "query": {}}
  2. {"op": "cancel_booking", "params": {"id": {"$ref": "booking#1"}}, "query": {}}
     <status>: status_changed: 409 -> 200
     <body>/error: missing_in_candidate: "already_cancelled" -> (absent)
     ...
     <db>/events/3: extra_in_candidate: (absent) -> {"booking_id": "<booking#1>", "kind": "cancelled"}
exported: out/regression.json
exported: out/test_careo_regression.py
```

The candidate's seeded bug: a retried cancellation succeeds and records a
second "cancelled" event. It was **deliberately planted** in
`examples/booking-api/app_candidate.py`, and the example's own happy-path tests
pass against both implementations. The candidate also numbers bookings
differently (8000, 8003, …). Logical-id mapping (`booking#1` → 2 / 8000) keeps
that from being reported as a difference, while a genuinely wrong id still is.

Other commands: `careo scenario validate`, `careo scenario replay --scenario
regression.json`, `careo scenario minimize`, and `careo bench`. See
[docs/stateful.md](docs/stateful.md) for the spec format, the reset lifecycle,
verdicts, the OpenAPI subset and the security model.

## Outcomes

| Outcome | Meaning |
|---|---|
| `MATCH` | Both ran normally; normalized outputs, exit codes (and stderr if `exact`) are identical |
| `MISMATCH` | Both ran normally but behaved differently |
| `CANDIDATE_FAILED` / `REFERENCE_FAILED` | One side crashed, was killed by a signal, exited with a non-accepted code, exceeded the output limit, or printed invalid JSON |
| `BOTH_FAILED` | Both failed. Never counted as a match, even if the failures are identical |
| `TIMEOUT` | Either side exceeded `timeout_seconds` |
| `INCONCLUSIVE` | A side gave different results across `repeat` runs of the same input |
| `EXECUTION_ERROR` | Careo could not start a program |

Comparison is strict by default: `1` vs `1.0`, `true` vs `1`, `0.0` vs `-0.0`,
`null` vs absent, array order, NFC vs NFD strings, and duplicate JSON keys are
all detected (duplicate keys and NaN/Infinity make output invalid).

## Architecture

```
src/careo/
  cli.py              compare | validate | inspect | replay | scenario … | bench
  config.py           Phase 1 config: strict YAML, path containment, executables
  engine.py replay.py report.py   Phase 1 one-shot comparison, replay, reports
  jsonvalue.py normalize.py       strict JSON, JSON-Pointer normalization rules
  comparison/         structural diff under a ComparisonPolicy
  execution/          one-shot runs (__init__) and managed services (service.py)
  adapters/           http.py (loopback HTTP client), sqlite.py (reset + verify + snapshot)
  scenarios/          spec.py, openapi.py, session.py (paired steps, logical ids),
                      machine.py (Hypothesis), runner.py (shrink, verify, replay, export)
  reporting/          scenario report rendering
  evaluation/         bench.py (seeded-regression benchmark harness)
```

See [docs/design.md](docs/design.md) for the execution model and report schema.

## Limitations

- **Not a sandbox.** Programs run as you, with your filesystem and network
  access. Only compare programs you own or are authorized to test.
- Bounded testing: a clean report covers the supplied inputs only. It is not
  a proof of equivalence.
- Phase 1 contract is JSON-on-stdin / JSON-on-stdout. Files, network,
  argv-based inputs and side effects are not compared.
- Process cleanup kills the program's process group. A descendant that calls
  `setsid` itself can escape it (reported as a failure if it holds the pipes).
- Environment variable values are never written to reports or fingerprints.
  As a result, replay cannot detect a change to an env value.
- Floats are compared as 64-bit doubles, so `0.1` and `0.10000000000000001`
  (the same double) match. A float literal that overflows (`1e400`) or
  underflows (`1e-400`), or that has more than 17 significant digits and is not
  exactly representable (`0.10000000000000000001`), counts as a failed run,
  never as a match. Integers are compared exactly at any size.
  Consequence: distinct float literals that round to the same double still
  match (e.g. `9007199254740992.0` vs `9007199254740993.0`, `5e-324` vs
  `4e-324`). Emit such values as integers or strings if the exact text matters.
- `array_order: ignore` compares elements by exact canonical JSON, so
  `float_abs_tolerance` and `numbers: numeric` do not apply inside unordered arrays.
- Replay drift detection covers the executable, file arguments, and any
  `identity_files` you list. Imported modules that are not listed are not tracked.
- `mask` hides value changes (not type or presence changes) at that path, by design.
- Linux and macOS only. Windows is untested.
- Stateful testing:
  - **Supported targets:** HTTP/JSON services with SQLite state only. PostgreSQL is
    not supported yet.
  - **Reset scope:** `reset: sqlite` resets the database, not in-memory caches.
    Use `reset: restart` if services keep state in memory.
  - **OpenAPI:** only a documented subset is supported.
  - **Generation bias:** Hypothesis favors simple values early on, so some defects
    need larger budgets (see benchmark results).
  - **Minimization:** results are smaller, not proven globally minimal.

## Tests

```sh
uv sync
uv run pytest            # unit, integration, property, stateful, security and benchmark tests
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## License

MIT. Copyright (c) 2026 Anvit Devadiga. See [LICENSE](LICENSE).

Created by **Anvit Devadiga**.

Development assistance: Claude Code was used during implementation.
