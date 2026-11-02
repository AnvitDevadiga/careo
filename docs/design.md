# Careo design (Phase 1)

## Trusted-local execution model

Careo is a local developer tool. It runs only the commands written in the
config file, as the invoking user. It does not isolate filesystem, network,
CPU or memory. Subprocesses are not a sandbox.

Safety measures that do exist:

- Commands are argument lists. A shell string is rejected and `shell=True` is never used.
- `command[0]` is resolved to an absolute path at validation time: a bare name via
  the child's `PATH`, a relative path against `cwd`. Symlinks are *not* followed for
  execution, so venv interpreters keep their identity.
- `cwd`, `cases.file` and relative executables must resolve, symlinks included,
  inside the directory that contains the config.
- The child environment is `inherit_env` (default `PATH SYSTEMROOT TMPDIR TEMP TMP`)
  plus explicit `env`. Nothing else is inherited.
- Each run gets a new session/process group. On exit, timeout, or output overflow
  the whole group is `SIGKILL`ed. The leader stays un-reaped (`waitid(WNOWAIT)`)
  until then, so its process-group id cannot be reused.
- stdout and stderr are each capped at `max_output_bytes`. Going over the cap
  kills the run and counts as a failure.
- Inputs are capped by `max_input_bytes`, and case files by `max_cases_file_bytes`.

## Comparison pipeline

1. Encode the case input as UTF-8 JSON + newline and write it to stdin.
2. Parse strictly: floats must not overflow or underflow, and literals with more
   than 17 significant digits must be exactly representable as a double.
   Classify each run as `ok`, `failed`, `timeout` or `spawn_error`. A run is `ok`
   only if the exit code is accepted and stdout is exactly one strict JSON value.
3. Apply normalization rules to `ok` outputs. Each rule records how many values it changed;
   a rule that changes nothing in the whole run produces a report warning.
4. Check that repeated runs agree (`repeat`). If they don't, the case is `INCONCLUSIVE`.
5. Diff the normalized values. Paths in differences are JSON Pointers; exit-code and
   stderr differences use `<exit_code>` and `<stderr>`.

`mask` replaces a value with `<careo:normalized:ID:TYPE>`, so a masked field
that changes type, or disappears, still shows up as a difference. With overlapping
rules the first matching mask wins, and its type tag is kept.

## Report schema (version 1)

Top-level keys: `schema_version`, `tool`, `started_at`, `finished_at`,
`config_path`, `policy_fingerprint`, `cases_fingerprint`, `cases_source`,
`environment`, `reference`, `candidate` (name, command, cwd, env names,
resolved executable, SHA-256 of the executable and of argument files),
`limits`, `comparison_policy`, `normalization_rules`, `normalization_totals`,
`warnings`, `result` (`total_cases`, `outcomes`, `conclusion`, `summary`,
`exit_code`), and `cases[]` (id, description, input, outcome, notes,
differences, `reference_runs[]`, `candidate_runs[]` with status, exit code,
duration, stdout/stderr (up to 64 KiB each in the report), parsed and
normalized output).

## Replay

`careo replay` loads the current config (recorded path or `--config`), replays
the **recorded** inputs, and reports drift separately from divergence:

- drift: policy fingerprint, cases file, executable path or hash, hashes of file
  arguments and of `identity_files` (files or directory trees),
  Careo/Python/platform version. Code outside these is not tracked.
- divergence: any case whose outcome or set of differences changed

Drift alone does not fail replay. Divergence does (exit 1).
