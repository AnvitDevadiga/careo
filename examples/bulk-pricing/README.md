# Demo: bulk pricing

`original.py` implements a small pricing contract (10% off at >= 100 units,
20% off at >= 1000, reject negatives and non-integers with exit code 2).
`candidate.py` is an AI-rewrite-style refactor with a **deliberately seeded**
regression: tier checks use `>` instead of `>=`. This is a constructed fixture
used to demonstrate Careo, not a bug found in the wild.

Happy-path inputs (5 and 250 units) behave identically. Only the exact
boundaries (100 and 1000 units) differ.

```sh
careo compare --config examples/bulk-pricing/careo.yaml --output-dir careo-report
```

Expected: `DIFFERENCES_FOUND`, with `at-tier-1` and `at-tier-2` reported as
`MISMATCH` on `/discount_percent` and `/total_cents`; exit code 1.
