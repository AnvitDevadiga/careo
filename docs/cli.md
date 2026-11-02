# Careo CLI reference (one-shot comparison)

## Configuration and commands

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

## Case outcomes

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


For stateful HTTP testing see [stateful.md](stateful.md); for report formats see [report-schema.md](report-schema.md).
