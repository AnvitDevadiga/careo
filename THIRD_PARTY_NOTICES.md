# Third-party notices

Careo's Python package does not vendor third-party code. It depends on:

| Package | License | Use |
|---|---|---|
| PyYAML | MIT | configuration parsing |
| Hypothesis | MPL-2.0 | stateful scenario generation and shrinking (used as an unmodified dependency) |

The website (`site/`) bundles:

| Package | License |
|---|---|
| react, react-dom, scheduler | MIT, Copyright (c) Meta Platforms, Inc. and affiliates |

Build and test tools (Vite, TypeScript, Vitest, Playwright, axe-core, Ruff, mypy,
pytest, uv) are development dependencies and are not distributed.
