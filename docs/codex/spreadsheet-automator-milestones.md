# Spreadsheet Workflow Automator — Milestone Ledger
STATUS: DONE
DoD ACCEPTANCE: scripts/dod/spreadsheet-automator.mjs   # author in M0 if absent; must exit 0 when done

## Definition of Done
A product surface CLEARLY SEPARATE from Filechat (its own route/CLI + docs/package wording) where
the user describes a recurring dependent-spreadsheet workflow. Vague/under-specified requests return
INTERVIEW QUESTIONS (never fabricated steps). Sufficiently-specified requests produce a downloadable
DETERMINISTIC local HTML app with: a file-input slot per source file, duplicate/ambiguous-input
rejection, deterministic transform steps, no network dependency in the runtime, and a final-output
download — built via the existing `src/spreadsheetAutomator/workflowValidation.ts` generator/validator
and `backend/app/excel_workflow.py`. Tests cover vague-request rejection + valid-generation, e2e.

## Ladder
- [x] M0 — Author DoD acceptance script (`scripts/dod/spreadsheet-automator.mjs`): asserts a separate surface exists, runs `workflowValidation.test.ts`, the excel_workflow pytest, and the new e2e; exits 0 only when all hold. Done 2026-05-31 — script + self-test added; build-lab green.
- [x] M1 — Backend endpoints `/api/workflows/interview` + `/api/workflows/generate` wired to `excel_workflow.py` + `workflowValidation.ts`, enforcing the interview-required gate (vague → required questions, never fabricated steps). Done 2026-05-31 — API tests cover vague interview gating and specified HTML generation; build-lab green.
- [x] M2 — Separate frontend entry: a distinct route/app shell (e.g. `/workflows`) that is NOT a tab inside the Filechat chat UI; interview → generate → download local HTML app. Done 2026-05-31 — `/workflows` renders a standalone app shell with interview/generate/download flow; build-lab green.
- [x] M3 — Make separation explicit in docs + package wording (name it the Spreadsheet Workflow Automator, not "Filechat spreadsheet mode"). Done 2026-05-31 — docs and package scripts name the separate product surface; build-lab green.
- [x] M4 — E2E: vague request → interview questions (no app); fully-specified request → downloadable deterministic local HTML app with duplicate-input rejection. Wire into build-lab. Done 2026-05-31 — Playwright e2e covers vague, specified, and duplicate-input paths; DoD and build-lab green.

## Backlog
(ideas only — NOT executed until promoted to the Ladder)
- Extract into a sibling repo `/Users/sungwanbae/spreadsheet-automator` once the in-repo split is stable.
- Workflow template library for common reconciliations.

## Log
- 2026-05-31 · M0 DoD acceptance script · green via build-lab 31/31 · /Users/sungwanbae/build-lab/reports/build_lab_2026-05-30T16-24-27-687Z.md
- 2026-05-31 · M1 backend workflow endpoints · green via build-lab 31/31 · /Users/sungwanbae/build-lab/reports/build_lab_2026-05-30T16-31-01-368Z.md
- 2026-05-31 · M2 separate frontend entry · green via build-lab 31/31 · /Users/sungwanbae/build-lab/reports/build_lab_2026-05-30T16-37-24-096Z.md
- 2026-05-31 · M3 docs and package separation · green via build-lab 31/31 · /Users/sungwanbae/build-lab/reports/build_lab_2026-05-30T16-41-18-067Z.md
- 2026-05-31 · M4 e2e and final DoD · DoD green + build-lab 31/31 · /Users/sungwanbae/build-lab/reports/build_lab_2026-05-30T16-45-59-553Z.md
