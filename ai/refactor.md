# Iterative Refactor Log

## Baseline

- Goal: improve maintainability without changing user-visible behavior, API wire shapes, database semantics, environment variables, or download behavior.
- Baseline verification before refactoring:
  - `python -m pytest backend\tests -q` -> 75 passed
  - `cd frontend && npm test` -> 28 passed
  - `cd frontend && npm run build` -> passed
- Method: one focused refactor per iteration, targeted/full verification, then a small commit.

