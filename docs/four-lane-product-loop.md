# Four-Lane Product Loop

Owner: Assignment Manager / Planner  
Scope: planning only; no product implementation in this lane.  
Rule: do not merge lanes or rename them into generic chat modes.

## Correct taxonomy

1. **Filechat Original** — grounded local document/file chat with citations.
2. **Excel Workflow Automation** — separate spreadsheet automation product/mode for multi-CSV/XLSX workflows; not Filechat Q&A. If the UX/codebase cannot keep this distinct, split it into a new repository/product.
3. **Searchchat Original** — LLM-powered search chat using search/retrieval plus LLM synthesis; not evidence-only.
4. **ETF-Correlation Spotter** — Searchchat product/mode that finds ETF/asset/market correlations, signals, and alerts. Separate if needed.

## Repo ownership

- Filechat repo: `nrtvai/filechat` at `/Users/sungwanbae/filechat`.
- Searchchat repo: `nrtvai/searchchat` at `/Users/sungwanbae/searchchat`.
- Filechat-owned lanes: Filechat Original, Excel Workflow Automation.
- Searchchat-owned lanes: Searchchat Original, ETF-Correlation Spotter.

## GitHub labels to maintain in each repo

- `lane:filechat-original` — grounded local file chat work.
- `lane:excel-workflow-automation` — spreadsheet automation work.
- `lane:searchchat-original` — LLM search chat work.
- `lane:etf-correlation-spotter` — ETF/asset signal spotter work.
- `role:planner`, `role:dev`, `role:reviewer`, `role:qa-functional`, `role:qa-ux`, `role:feedback-analysis`, `role:pm-codex-comparison`, `role:interrogation`, `role:report-loop`.
- `type:smoke-test`, `type:acceptance-criteria`, `type:docs`, `priority:p0`, `priority:p1`.

## Filechat Original measurable goal and smoke tests

Goal: A user can upload/query local documents and receive answers grounded in retrieved local file evidence with visible citations.

Acceptance checks:
- Every non-trivial answer includes citations with file name and retrievable location/snippet.
- Unsupported claims are refused or qualified instead of synthesized.
- Spreadsheet files can be cited as source files when used as documents, but the app does not present multi-file spreadsheet transformation as Filechat Q&A.
- OpenRouter key, if used for live smoke, is read only from environment and never printed.

Smoke test script:
1. Start app from clean checkout.
2. Upload at least one PDF/TXT/MD fixture containing a known answer and one distractor file.
3. Ask a direct fact question; verify answer quotes/cites the correct file.
4. Ask a question not present in files; verify the app says the evidence is insufficient.
5. Export/inspect response payload/UI for citation fields.

## Excel Workflow Automation measurable goal and smoke tests

Goal: A user can run a distinct spreadsheet workflow over multiple CSV/XLSX files and receive deterministic artifacts/results, not a conversational document answer.

Acceptance checks:
- Product/mode is visibly separated from Filechat Original in naming, UI entry, route, docs, and tests.
- Accepts at least 2 spreadsheet files in one workflow.
- Produces a downloadable or copyable artifact: cleaned table, joined table, validation report, transformation log, or generated formula/workbook.
- Shows deterministic transformation steps and row/column counts.
- Does not require users to ask Filechat-style Q&A to trigger automation.

Smoke test script:
1. Provide `sales_orders.csv` and `customer_feedback.csv` or equivalent fixtures.
2. Run a workflow: join/summarize/validate with explicit expected output.
3. Verify artifact exists, row counts match, and transformation log is shown.
4. Verify UI/product name is Excel Workflow Automation or another clearly separate name, not Filechat Original.
5. If separation is not practical in this repo, create a split-product decision issue.

## Assignment prompts

### Planner prompt
You are the tactical planner. Maintain the four-lane taxonomy exactly: Filechat Original, Excel Workflow Automation, Searchchat Original, ETF-Correlation Spotter. Convert user/product complaints into GitHub issues with measurable acceptance criteria, smoke tests, owner role, lane label, and blocker list. Do not implement product code. Reject any plan that collapses Excel into Filechat Q&A or Searchchat into evidence-only retrieval.

### Dev prompt
You are the implementation dev for exactly one assigned lane. Read the lane issue, preserve the taxonomy, implement only the scoped acceptance criteria, and add/adjust smoke tests. Do not touch other lanes except shared infrastructure required by the issue. Never print secrets; use OpenRouter only from environment variables. Return changed files, test commands, and evidence.

### Reviewer prompt
You are the code/product reviewer. Verify the PR against the lane taxonomy, acceptance criteria, and smoke tests. Block merges that blur lanes, remove citations from Filechat, make Searchchat evidence-only, or expose secrets. Require test evidence and a rollback note.

### Functional QA prompt
You are functional QA. Run the lane smoke test exactly, record commands, inputs, outputs, pass/fail, screenshots/log paths, and defects. Do not infer success from implementation notes. Verify user-visible behavior and API payloads where available.

### UX QA prompt
You are UX QA. Check whether a normal user can identify which lane/product they are in and complete the primary task without taxonomy confusion. Flag copy, route, navigation, empty-state, and result-display issues. For Excel, verify it feels like workflow automation, not document Q&A. For ETF Spotter, verify it feels like signals/alerts, not generic search.

### Feedback analysis prompt
You are feedback analysis. Convert user frustration, QA notes, and review comments into ranked issues. Separate product-taxonomy failures from implementation defects. Produce a top-5 fix list with severity, reproduction, affected lane, and suggested owner.

### PM/Codex comparison prompt
You are PM/Codex comparison. Compare the PM intent, issue acceptance criteria, implementation diff, and QA results. Identify mismatch, overbuild, underbuild, and taxonomy drift. Recommend merge, revise, split product, or reject.

### Interrogation prompt
You are interrogation. Challenge the plan/PR by asking: What lane is this? What user job is solved? What measurable proof exists? What broke? What secret or dependency risk exists? What would Sungwan call bullshit on? Return blockers first, then required evidence.

### Report loop prompt
You are report loop. Produce a controller-ready status with STATUS, FILES/ISSUES/PRS, MEASURABLE GOALS, BLOCKERS, NEXT ASSIGNMENTS. Keep it compact and tactical. Include only verified facts and links.

## Assignment cadence

1. Planner opens/updates lane issues with smoke tests.
2. Dev implements one issue on a feature branch.
3. Reviewer checks taxonomy and code quality.
4. Functional QA executes smoke tests.
5. UX QA checks product clarity.
6. Feedback analysis ranks defects.
7. PM/Codex comparison decides merge/revise/split/reject.
8. Interrogation stress-tests the decision.
9. Report loop sends controller summary and next assignments.
