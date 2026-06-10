# 0001 — Spreadsheet Workflow Automator stays in this repository (for now)

Status: superseded by [0002-spreadsheet-lane-extracted.md](0002-spreadsheet-lane-extracted.md) (2026-06-11)

## Context

`docs/four-lane-product-loop.md` defines FileChat Original and Excel Workflow
Automation as separate product lanes, and says the spreadsheet lane should be
split into its own repository if the UX or codebase cannot keep the two
distinct.

## Decision

Keep both lanes in `nrtvai/filechat` while the boundary remains enforceable:

- Frontend code for the spreadsheet lane lives only in `src/spreadsheetAutomator/`
  and is reached via the `/workflows` route or the standalone
  `npm run dev:spreadsheet-automator` entry point.
- Backend code for the spreadsheet lane lives in `backend/app/excel_workflow.py`
  and `backend/app/spreadsheet_mode.py`.
- FileChat Q&A surfaces must not present multi-file spreadsheet transformation
  as document chat, and spreadsheet UI must not import FileChat chat components.

## Split triggers

Revisit and split into a separate repository if any of these happen:

- Shared modules start accumulating lane-specific branches (`if spreadsheet`)
  outside the two designated backend modules.
- The lanes need divergent release cadences or versioning.
- The spreadsheet lane gains its own persistent storage schema that migrations
  in the FileChat schema would constrain.
- Either lane's CI requirements meaningfully slow the other down.
