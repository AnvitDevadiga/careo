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
reference: {name: original,  command: ["python3", "original.py"], cwd: .}
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
program could not be executed. `replay` returns `0` if every outcome and
difference reproduced, `1` if not.

## Example difference

`examples/bulk-pricing` has a correct original and an AI-rewrite-style
candidate with a **deliberately seeded** bug (`>` instead of `>=` on discount
tiers). It is a constructed demo, not a real-world finding.

```console
$ careo compare --config examples/bulk-pricing/careo.yaml
careo 0.1.0: comparing 10 case(s)
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
  cli.py          argparse commands: compare, validate, inspect, replay
  config.py       strict YAML loading, path containment, executable resolution
  execution.py    one subprocess run: argv only, timeout, output caps, process-group kill
  jsonvalue.py    strict JSON parsing, canonical serialization
  normalize.py    JSON-Pointer-scoped mask/regex rules
  comparison.py   structural diff under a ComparisonPolicy
  engine.py       per-case runs, repeat checks, outcome classification, report dict
  report.py       report.json / report.md writing, loading, schema check
  replay.py       re-run recorded inputs, detect drift and divergence
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
- `array_order: ignore` compares elements by exact canonical JSON, so float
  tolerance does not apply inside unordered arrays.
- `mask` hides value changes (not type or presence changes) at that path, by design.
- Linux and macOS only. Windows is untested.

## Tests

```sh
uv sync
uv run pytest            # unit, integration and Hypothesis property tests
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## License

MIT. Copyright (c) 2026 Anvit Devadiga. See [LICENSE](LICENSE).

Created by **Anvit Devadiga**.

Development assistance: Claude Code was used during implementation.
