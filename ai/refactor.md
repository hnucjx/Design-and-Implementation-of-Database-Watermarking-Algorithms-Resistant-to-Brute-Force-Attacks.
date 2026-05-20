# Iterative Refactor Log

## Baseline

- Goal: improve maintainability without changing user-visible behavior, API wire shapes, database semantics, environment variables, or download behavior.
- Baseline verification before refactoring:
  - `python -m pytest backend\tests -q` -> 75 passed
  - `cd frontend && npm test` -> 28 passed
  - `cd frontend && npm run build` -> passed
- Method: one focused refactor per iteration, targeted/full verification, then a small commit.

## Iteration 1 - Centralize Resolution Fallback Policy

- Problem: fallback reason constants and response-message branching were split across job execution and API serialization, making future fallback changes easy to duplicate incorrectly.
- Reason: fallback policy is domain logic, not route wiring; centralizing it makes behavior reviewable without touching task execution.
- Change: added an internal fallback policy module for reason constants and `ResolutionFallback` response construction; `JobManager` records reasons and `main.py` delegates response construction.
- Verification: `python -m pytest backend\tests -q` -> 75 passed.
- Functional invariance: no API fields, messages, database columns, or download behavior changed.

## Iteration 2 - Extract Job Read Model Projection

- Problem: FastAPI route wiring also contained job serialization, aggregate resolution/format calculation, fallback projection, and elapsed-time calculation.
- Reason: read-model projection is pure API response assembly; separating it keeps route handlers focused on HTTP concerns and makes response behavior easier to test/review.
- Change: added an internal job read-model module and left `main.py` with a thin 404 wrapper around it.
- Verification: `python -m pytest backend\tests -q` -> 75 passed.
- Functional invariance: job list/detail response fields and values are unchanged.

## Iteration 3 - Extract Browser Cookie Importer

- Problem: the yt-dlp service mixed download/analyze behavior with browser-cookie import details, including Edge lock handling, DPAPI fallback, CDP cookie extraction, and cookie-domain filtering.
- Reason: browser-cookie import is a separate integration boundary with OS/browser-specific failure modes; isolating it keeps the download service easier to reason about and preserves a smaller public surface for tests.
- Change: moved cookie import result/error types, auto browser candidates, YouTube cookie filtering, Edge process shutdown, and CDP extraction into an internal browser-cookie importer; `YtDlpService.import_browser_cookies()` remains the compatibility entry point.
- Verification: `python -m pytest backend\tests -q` -> 75 passed.
- Functional invariance: import API responses, error detail shapes, browser fallback order, and cookie file output are unchanged.

## Iteration 4 - Extract yt-dlp Format Helpers

- Problem: selector construction, actual format/resolution extraction, and fallback-resolution calculation were embedded inside the service that also owns network/download orchestration.
- Reason: these operations are deterministic transformations over options, metadata, and format lists; moving them into pure helpers reduces service size and makes format policy easier to review independently.
- Change: added an internal yt-dlp format helper module and kept the existing `YtDlpService` methods as compatibility proxies.
- Verification: `python -m pytest backend\tests -q` -> 75 passed.
- Functional invariance: generated selectors, actual format strings, resolution extraction, and fallback suggestions are unchanged.

## Iteration 5 - Extract Frontend Utilities

- Problem: `App.tsx` mixed application state orchestration with formatting helpers and quality-selection calculations.
- Reason: display formatting and quality option construction are pure UI helpers; extracting them reduces component noise and makes future UI changes less likely to touch unrelated state logic.
- Change: moved duration/date/filesize/progress formatting into a formatting module and quality option/fallback label helpers into a quality module.
- Verification: `cd frontend && npm test` -> 28 passed.
- Functional invariance: visible labels, selected quality behavior, and request payloads are unchanged.

## Iteration 6 - Extract Task Center Component

- Problem: task-center rendering, playlist expansion state, fallback notices, item metrics, and action buttons made the main application component difficult to scan.
- Reason: the task center is a cohesive display component with a clear props boundary; moving it out keeps `App.tsx` focused on state orchestration and API calls.
- Change: moved `JobQueue` and its fallback notice subcomponent into `frontend/src/components/JobQueue.tsx`.
- Verification: `cd frontend && npm test` -> 28 passed; `cd frontend && npm run build` -> passed.
- Functional invariance: task center labels, controls, expansion behavior, fallback buttons, and callbacks are unchanged.
