# Stateful differential testing (Phase 2)

`careo scenario run` exercises two **trusted local HTTP services**, a reference
and a candidate, with the same generated sequences of operations, starting each
sequence from a verified, equivalent initial state. It compares responses and
persistent database state after every step.

## Lifecycle

```
prepare   start managed services on free loopback ports, create Careo-owned SQLite files
reset     per scenario: delete every row of every table, reload the fixture (one
          transaction); restart services if reset: restart, or if a service died;
          optionally call reset_request
verify    open each DB read-only and compare schema, every row of every table and
          AUTOINCREMENT counters with schema+fixture loaded into a private in-memory DB.
          Any mismatch -> RESET_FAILED: the whole run is void, never "equivalent"
execute   send each step to reference, then candidate; compare
inspect   snapshot the configured tables on both sides and compare (each_step | end | never)
teardown  SIGKILL each service's process group (also on SIGTERM/SIGINT and at exit)
```

`reset: sqlite` is fast but cannot clear state a service keeps **in memory**.
Reports therefore carry an `isolation` block and a warning in that case. Use
`reset: restart` when services cache state. The reset is verified on the database
only. Without a `state` block:

- **Managed services** are restarted before every scenario.
- **External services** require `unverified_initial_state: true`. Their reports warn
  that scenarios may not be independent.

An external service's database is reset **in place**. The file is never deleted,
so a running service keeps a valid handle.

## Spec (`careo-scenarios.yaml`)

```yaml
version: 1
services:
  reference:
    command: ["python3", "app.py", "--port", "{port}", "--db", "{db}"]   # managed (argv, no shell)
    base_url: http://127.0.0.1:{port}    # loopback only unless allow_remote: true
    ready_path: /health
  candidate:
    base_url: http://127.0.0.1:9000      # or an already-running service...
    db: candidate.db                     # ...whose test DB Careo may wipe
    reset_wipes_database: true           # required explicit consent
state:
  kind: sqlite
  schema: schema.sql
  fixture: seed.sql                      # no BEGIN/COMMIT/ATTACH
  reset: sqlite                          # sqlite | restart
  reset_request: {method: POST, path: /__reset}   # optional hook, still verified
  compare: each_step                     # each_step | end | never
  tables:
    - {name: bookings, id_columns: {id: booking}}
    - {name: events, ignore_columns: [id], id_columns: {booking_id: booking}, unordered: false}
entities: [booking]
operations:                              # or: openapi: {file: openapi.yaml, include: [...]}
  - name: create_booking
    method: POST
    path: /bookings
    body: {customer: {sampled_from: [ana, bo]}, slot: {sampled_from: ["09:00", "10:00"]}}
    produces: {entity: booking, pointer: /id}
    expect_status: [201, 409]
  - name: cancel_booking
    method: POST
    path: /bookings/{id}/cancel
    params: {id: {bundle: booking}}       # only runs once a booking exists
    ids: [{entity: booking, pointer: /id}]
http: {timeout_seconds: 5, max_response_bytes: 1048576, compare_headers: [content-type]}
comparison: {array_order: strict, numbers: strict, float_abs_tolerance: 0}
normalize: [{id: ts, path: /created_at, action: mask, reason: wall clock}]
generation: {seed: 1, max_scenarios: 200, max_steps: 25, shrink: true, replay_checks: 3,
             max_operations: null}
```

Value generators: a literal, `const`, `sampled_from`, `integers {min, max}`,
`text {min_size, max_size, alphabet}`, `booleans`, `bundle`, `one_of`,
`maybe_absent` (the field may be omitted).

## Logical identities

`produces` registers one logical id, such as `booking#1`, that maps to each side's
own actual id (for example `2` and `8000`). Later `{bundle: booking}` arguments send each
side its own id. Before comparison, values at `ids` pointers and `id_columns`
are rewritten to `<booking#1:integer>`. The marker keeps the JSON type of the actual
id, so an id that turns from `7` into `"7"` is still a difference. These rules keep
the mapping honest:

- an id that maps to no logical entity is left unchanged, so wrong or unmapped ids
  are still reported. Ids are never ignored wholesale;
- if the two sides disagree about which entity a produced id refers to, that is an
  `identity_divergence` difference;
- the final mapping is printed in every report.

Raw ids of *generated* entities are implementation-specific. A spec that
fetches `/bookings/2` directly will legitimately differ between two
numbering schemes. Use bundles for generated entities, and raw ids only for
fixture rows or ids no implementation will generate.

## Bodies

Bodies are compared as JSON when both sides return JSON. If one side returns JSON
and the other doesn't, that is a `body_format_changed` difference. Two non-JSON
bodies are compared byte for byte (`raw_body_changed`), with no decoding and no
normalization.

## Verdicts

Step verdicts: `MATCH`, `MISMATCH`, `CANDIDATE_FAILED`, `REFERENCE_FAILED` (timeout,
connection error, partial or oversized response), `BOTH_TIMEOUT`, `BOTH_FAILED`.

Run outcomes (exit code):

| Outcome | Exit | Meaning |
|---|---|---|
| `NO_DIFFERENCES_OBSERVED` | 0 | bounded: no difference in the executed operations |
| `DIFFERENCE_CONFIRMED` | 1 | minimized sequence reproduced on every verification replay |
| `DIFFERENCE_FLAKY` | 1 | observed but did not reproduce reliably |
| `TIMEOUT` | 1 | both sides timed out; never treated as equivalence |
| `INCONCLUSIVE` | 1 | nothing executed |
| `RESET_FAILED` | 3 | equivalent initial state not established; results void |
| `EXECUTION_ERROR` | 3 | services could not be started or reached |

## Minimization

1. Hypothesis shrinks the failing sequence. Every attempt re-runs from a fresh,
   verified reset.
2. A bounded delta-debugging pass (at most 150 replays) removes more steps. A
   reduction is kept only if a replay reproduces the same failure signature
   (operation, verdict, difference paths and kinds).
3. The result is replayed `replay_checks` times from clean resets. Only an n/n
   reproduction is reported as `DIFFERENCE_CONFIRMED`.

The result is smaller, not proven globally minimal. `careo scenario minimize`
applies step 2 and step 3 to any recorded scenario file.

## Regression export and replay

A confirmed difference writes `regression.json` (logical steps plus the expected
failure signature) and `test_careo_regression.py`. The test fails while the
difference exists and passes once the candidate matches. `careo scenario replay`
replays a scenario file. It exits 0 if the recorded result reproduces and reports
spec drift.

## OpenAPI subset

See the module docstring of `careo/scenarios/openapi.py`. In short:

- **Supported:** 3.0 and 3.1 documents with `operationId`, path and query parameters,
  primitive and enum schemas, object JSON bodies of primitive properties,
  `#/components` refs, and the `x-careo-bundle`, `x-careo-produces` and `x-careo-ids`
  extensions for dependencies.
- **Rejected with an error:** anything else that an included operation uses.

This is not complete OpenAPI coverage.

## Security model

- **Trusted local programs only.** Managed services run as you. They are not sandboxed.
  Careo does not use Docker and never exposes a Docker socket.
- **Loopback only by default.** Targets must be loopback addresses unless `allow_remote: true`,
  and Careo never defaults to remote or production endpoints.
- **Contained paths.** Commands are argument lists, and every path (schema, fixture,
  OpenAPI document, cwd, external database) must resolve inside the spec directory.
- **Explicit wipe consent.** An external service's database is wiped before each scenario
  only with explicit `reset_wipes_database: true`. Managed services get Careo-owned
  database files under the output directory.

PostgreSQL is not supported in this release. The SQLite adapter and its
verification cover every integration test and the demo, so there was no
justified need for a second engine yet.
