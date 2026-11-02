<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/careo-wordmark-dark.svg">
    <img alt="careo." src="assets/careo-wordmark-light.svg" width="260">
  </picture>
</p>

<p align="center"><strong>Catch what the AI rewrite broke.</strong><br>
Behavioral comparison for software rewrites.<br>
<a href="https://anvitdevadiga.github.io/careo/">Website and interactive demo</a></p>

## What Careo does

Careo runs an original and a rewritten implementation on identical inputs, starting
from equivalent states. It compares what they observably do and turns any difference
into a minimal, reproducible counterexample and a regression test.

- **One-shot programs**: any executable that reads JSON on stdin and writes JSON on stdout.
- **Stateful HTTP services**: Hypothesis-generated operation sequences against two local
  services. Each scenario starts from a verified SQLite reset. Careo compares responses
  and persistent state after every step.

## Why it exists

Rewrites, especially AI-assisted ones, tend to pass the happy-path tests and break
the edges. Common breaks: a boundary off by one, a retried request that now has a
second side effect, an error turned into a success. Careo treats the original as the
oracle and searches for the first place the rewrite behaves differently.

## Quick start

Linux or macOS, Python 3.11+.

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install git+https://github.com/AnvitDevadiga/careo
git clone https://github.com/AnvitDevadiga/careo && cd careo

careo compare --config examples/bulk-pricing/careo.yaml
careo scenario run --spec examples/booking-api/careo-scenarios.yaml --output-dir out
careo scenario replay --scenario out/regression.json
```

Exit codes: `0` no difference observed, `1` difference or failure, `2` invalid
configuration, `3` execution or reset error.

## Reproducible example

`examples/booking-api` contains a booking API and an AI-rewrite-style candidate. The
candidate has a **deliberately seeded** bug: retrying a cancellation returns 200 and
records a second event instead of 409. The example's own happy-path tests pass on
both. A real run:

```text
DIFFERENCE_CONFIRMED
original sequence: 13 operations; minimized: 3 operations; verified 3/3
  0. create_booking(customer="ana", slot="09:00")
  1. cancel_booking(id=booking#1)
  2. cancel_booking(id=booking#1)
     <status>: status_changed: 409 -> 200
     <db>/events/3: extra_in_candidate: (absent) -> {"booking_id": "<booking#1:integer>", "kind": "cancelled"}
exported: out/regression.json, out/test_careo_regression.py
```

The recorded evidence is in [`benchmarks/results/demo/`](benchmarks/results/demo/).
`python scripts/capture_demo.py --verify` re-runs it.

## How comparison works

- **Strict by default.** `1` vs `1.0`, `true` vs `1`, `null` vs absent, array order,
  duplicate JSON keys and non-JSON bytes all count.
- **Explicit normalization.** Mask or regex rules are scoped by JSON Pointer and
  reported with how often each fired.
- **Identity mapping.** Generated ids are translated to logical entities
  (`booking#1` → `2` / `8000`), keeping their JSON type. Ids are never ignored wholesale.
- **Failures are never matches.** Crashes, timeouts and failures on both sides are
  separate outcomes. A broken reset voids the run.

## Stateful discovery

1. Each operation in the spec (or the supported OpenAPI subset) becomes a Hypothesis
   rule. Bundles carry logical entities between steps.
2. Before every scenario, the database is reset and independently verified against
   the fixture. Services can also be restarted.
3. A difference is shrunk by Hypothesis and then by bounded delta debugging. It is
   **confirmed** only if every replay from a clean state fails the same way.

## Actual evaluation results

Six regressions were deliberately seeded into an independent rewrite of the booking
API, and two clean candidates measure false alarms. Each case ran 5 seeds, and
methods B–D had an equal budget of 400 search operations. These results come from
[`benchmarks/results/latest`](benchmarks/results/latest/summary.md):

| Method | Seeded detection | False alarms | Median length before → after shrink |
|---|---|---|---|
| A. Example happy-path tests | 1/6 cases | 0/2 | n/a |
| B. Single-operation random | 5/30 runs | 0/10 | 1 → 1 |
| C. Stateful differential | 30/30 runs | 0/10 | 11.5 → 11.5 |
| D. Stateful + shrinking | 30/30 runs | 0/10 | 11.5 → 2.5 |

A is not budget-equal. D runs C's search, so it detects the same runs; what it
adds is shorter, replay-verified counterexamples (30/30). Smaller budgets missed a
defect in trial runs. These are seeded fixtures. They do not measure effectiveness
on real migrations.

## Documentation

- [CLI reference and one-shot config](docs/cli.md)
- [Stateful testing, spec format, OpenAPI subset](docs/stateful.md)
- [Execution model](docs/design.md)
- [Report schemas](docs/report-schema.md)
- [Benchmarks and how to reproduce them](benchmarks/README.md)

## Security and limitations

- **Trusted programs only.** Careo runs the commands you configure as your user.
  Subprocesses are not a sandbox, so only compare software you own or are authorized
  to test.
- **Local by default.** HTTP targets must be loopback unless you set `allow_remote: true`.
  Paths must stay inside the config directory. Commands are argument lists, never shell
  strings. Careo wipes a database only if it owns it or you confirm with
  `reset_wipes_database: true`.
- **Bounded testing.** Finite coverage, and no proof of equivalence. The reference can
  itself be wrong.
- **Scope.** SQLite state only, and a database reset does not clear in-memory caches
  (use `reset: restart`). Only a subset of OpenAPI is supported. Linux and macOS only.

## License

MIT. Copyright (c) 2026 Anvit Devadiga. See [LICENSE](LICENSE).

## Credits

Created by **Anvit Devadiga**.

Development assistance: Claude Code was used during implementation.
