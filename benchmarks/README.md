# Benchmarks

`booking/` measures four testing methods on one booking API fixture:

- the reference: `examples/booking-api/app_reference.py`
- an independent rewrite: `booking/rewrite.py`, with six **deliberately seeded** regressions
  selectable by `--defect`
- two clean candidates: the clean rewrite, and the reference compared against itself

```sh
careo bench --suite benchmarks/booking/suite.yaml --seeds 5 --output-dir benchmarks/results/latest
```

`results/` holds the committed raw records (`raw-records.jsonl`, one line per run),
`summary.json` (with configuration fingerprints, commit and environment) and
`summary.md`. Re-running with the same seeds reproduces the same per-run outcomes
and operation counts. Timings vary.

These seeded fixtures say nothing about how well Careo finds real regressions in
real migrations. No real-world migration case is included, because none could be
independently verified for this release.
