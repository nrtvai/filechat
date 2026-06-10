# 0002 — Spreadsheet Workflow Automator extracted to its own repository

Date: 2026-06-11
Status: accepted
Supersedes: [0001-spreadsheet-lane-stays-in-repo.md](0001-spreadsheet-lane-stays-in-repo.md)

## Context

Decision 0001 kept the spreadsheet lane in this repository while the boundary
stayed enforceable, and listed split triggers. The triggers fired: the lane
needs its own release cadence and product direction (chat-driven workflow
interviews, opt-in coding-agent enhancement of the generated offline apps),
and an audit found the boundary already violated in three places:

- `backend/app/spreadsheet_mode.py` had become dual-use — FileChat ingest and
  `artifact_engine.py` depend on it to summarize uploaded spreadsheets.
- The `/api/workflows/*` route handlers and interview helpers lived in
  `backend/app/main.py`, outside the designated lane modules.
- Four retrieval modules carried stray `excel_workflow` imports.

## Decision

The lane now lives in `nrtvai/spreadsheet-automator` (clean copy; pre-split
history stays browsable here). In this repository:

- Removed: `src/spreadsheetAutomator/`, the `/workflows` route, the
  `dev:/build:spreadsheet-automator` scripts, `backend/app/excel_workflow.py`,
  the `/api/workflows/*` endpoints, the `_try_excel_workflow_answer` chat path
  in the retrieval orchestrator, and all lane tests and DoD scripts.
- Kept: `backend/app/spreadsheet_mode.py` (and its tests) as FileChat-owned
  ingest code. The extracted repo carries its own fork; divergence is expected.

## Consequences

- Spreadsheet workflow questions in FileChat chat now fall through to normal
  model-led document Q&A; FileChat makes no deterministic-reconciliation claim.
- `docs/four-lane-product-loop.md` lane 2 is owned by the new repository.
