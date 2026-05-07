# FileChat Reliability CLI, Smoke, And Notion Plan

## Summary

Make FileChat reliable and easy to test from the command line by adding a Python CLI, correlated business fixture documents, full artifact/chart/report smoke coverage, OpenRouter model-matrix testing, and Notion output support.

Notion support includes both importable outputs and live publishing. Importable outputs should work without Notion credentials. Live publishing is required when `NOTION_API_KEY` and `NOTION_PARENT_PAGE_ID` are present in `.env`.

## Goals

- Add a first-class `filechat` CLI that exercises the same backend runtime as the UI.
- Use the OpenRouter API key already present in `.env` for real-provider testing without printing secrets.
- Generate and commit correlated business test documents suitable for multiple chart and report formats.
- Verify all supported data, chart, artifact, and report paths through deterministic tests and real-provider smoke runs.
- Add Notion-format document support:
  - Notion-safe Markdown and datatable import bundle.
  - Live Notion page/database publishing using `.env` credentials.
- Use parallel child agents for independent development and test work when execution begins.

## Implementation Plan

### CLI

- Create `backend/app/cli.py`.
- Register the CLI in `pyproject.toml`:

```toml
[project.scripts]
filechat = "backend.app.cli:main"
```

- Add commands:
  - `filechat verify`
  - `filechat session create --title "..."`
  - `filechat upload <session_id> <paths...>`
  - `filechat ask <session_id> "<prompt>" --auto-answer --export-dir reports/cli`
  - `filechat export <session_id> <artifact_id> --format md|json|notion`
  - `filechat smoke fixtures --data-dir .filechat-smoke`
  - `filechat smoke models --models <csv> --export reports/openrouter-smoke.json`
  - `filechat notion publish --fixture correlated_business --title "FileChat Smoke Report"`

- Default CLI mode should use the local app runtime without requiring a manually started server. Optional `--api-url` can target a running API.
- Normalize OpenRouter model URL inputs like `https://openrouter.ai/qwen/qwen3.6-flash` into model IDs like `qwen/qwen3.6-flash`.
- Never print `OPENROUTER_API_KEY`, `NOTION_API_KEY`, or full authorization headers.

### Correlated Test Documents

Create fixture documents under `test_documents/correlated_business/`:

- `warehouse_stock_units.csv`
  - SKU, warehouse, category, units_on_hand, reorder_point, unit_cost, expiry_date.
- `sales_orders.csv`
  - order_id, SKU, channel, region, units_sold, revenue, discount_rate, order_date.
- `purchase_orders.csv`
  - SKU, supplier, lead_time_days, units_ordered, expected_arrival.
- `stock_movements.tsv`
  - SKU, date, movement_type, quantity, warehouse.
- `customer_feedback.csv`
  - customer_segment, rating, churn_risk, feedback_text.
- `monthly_financials.csv`
  - month, revenue, cogs, gross_margin, operating_expense.
- `product_catalog.md`
  - SKU descriptions, categories, launch status, and strategic notes.
- `operations_roadmap.txt`
  - Dated milestones suitable for timeline/roadmap output.

The documents must correlate through shared SKU/category/month/region fields so FileChat can answer cross-file questions, such as comparing warehouse inventory against sales demand and purchase-order lead times.

### Smoke Runner

Create `backend/app/smoke_runner.py`.

The runner should:

- Create isolated FileChat sessions.
- Upload correlated fixtures.
- Wait for files to reach `ready`.
- Run prompt matrices for:
  - grounded Q&A
  - bar chart
  - line chart
  - pie chart
  - table
  - file draft/report
  - summary panel
  - decision cards/artifact discovery
  - Mermaid flowchart
  - timeline-as-JSON-render
  - Notion export
  - Notion live publish
- Auto-answer broad planning questions with the `automatic` option unless a prompt explicitly tests interview mode.
- Export artifacts to Markdown, JSON, and Notion formats.
- Produce a JSON report containing:
  - model ID
  - prompt
  - run status
  - artifact kinds
  - chart types
  - citation count
  - exported file paths
  - Notion page/database IDs when live publish succeeds
  - provider errors or validation failures.

### OpenRouter Model Matrix

Use the following default model matrix:

- `openrouter/owl-alpha`
- `x-ai/grok-4.3`
- `qwen/qwen3.6-flash`
- `deepseek/deepseek-v4-flash`
- `tencent/hy3-preview:free`
- `xiaomi/mimo-v2.5-pro`
- `deepseek/deepseek-v4-pro`
- `moonshotai/kimi-k2.6`

Before a smoke run:

- Fetch OpenRouter model metadata.
- Record unavailable or unsupported model IDs as explicit failures.
- Continue testing other models unless the failure is global provider authentication.

### Notion Import Bundle

Add Notion as an export format for existing `file_draft` artifacts instead of creating a new artifact kind.

- Update `backend/app/main.py` artifact export query from `md|json` to `md|json|notion`.
- Add helper functions in `backend/app/notion_export.py`.
- For `format=notion`, return a Notion import bundle:
  - Notion-safe Markdown content.
  - CSV/datatable payload when table-like artifact data exists.
  - Metadata with title, source artifact ID, source chunks, and export timestamp.
- Keep persisted `file_draft` artifact content canonical as Markdown or JSON so current validation and rendering remain stable.

### Notion Live Publisher

Add `backend/app/notion_client.py` using existing `httpx`.

Read from `.env`:

- `NOTION_API_KEY`
- `NOTION_PARENT_PAGE_ID`

Live publishing should:

- Create a Notion page under `NOTION_PARENT_PAGE_ID` for report/draft content.
- Create a Notion database or data-source-backed table for datatable artifacts.
- Insert rows derived from the fixture/artifact table data.
- Return created page/database IDs in CLI output and smoke reports.
- Fail clearly on missing credentials or Notion API errors.

Live publishing is a real side effect. If credentials are present but publishing fails, the Notion live portion should fail verification rather than silently skip.

### Frontend

- Update `src/api.ts` so `exportArtifactUrl` accepts `md | json | notion`.
- Add a `Notion` link in `src/artifacts.tsx` for inline file draft artifact cards.
- Add a `Notion` link in `src/App.tsx` artifact side-panel export controls.
- Keep UI copy concise: `Markdown`, `JSON`, `Notion`.

### Prompt And Planning Contracts

- Update prompt/source-contract wording so user requests for Notion documents map to `file_draft` plus Notion export/publish targets.
- Do not add `notion_document` as a new artifact kind for v1.
- Optionally persist `export_targets: ["md", "json", "notion"]` in run workspace metadata for visibility.

## Parallel Execution Plan

Use parallel child agents when implementation begins:

- Agent A: CLI, model normalization, and smoke runner.
- Agent B: correlated fixture generator and prompt matrix.
- Agent C: Notion import/export and live publisher backend.
- Agent D: backend regression tests.
- Agent E: frontend unit and Playwright e2e tests.
- Main agent: integration, conflict resolution, final verification, and completion report.

Agents must work on disjoint file sets where possible and must not revert other agents' changes.

## Test Plan

Run deterministic local verification:

```bash
uv run pytest backend/tests
npm run test
npm run lint
npm run build
npm run test:e2e
```

Run OpenRouter smoke verification:

```bash
uv run filechat smoke models --data-dir .filechat-openrouter-smoke --export reports/openrouter-smoke.json
```

Run Notion live verification when `.env` contains `NOTION_API_KEY` and `NOTION_PARENT_PAGE_ID`:

```bash
uv run filechat notion publish --fixture correlated_business --title "FileChat Smoke Report"
```

Acceptance criteria:

- CLI commands work without manually starting the web server.
- Correlated fixtures upload and index successfully.
- All supported chart types generate successfully: bar, line, pie.
- All supported report/artifact paths generate successfully: table, file draft, summary panel, decision cards, Mermaid, timeline JSON-render.
- Notion import bundle exports successfully.
- Notion live publishing creates a page and datatable under the configured parent page.
- Smoke report clearly records successes, failures, model IDs, artifact kinds, chart types, citations, exports, and Notion IDs.
- Secrets are never printed.

## Assumptions

- `.env` contains the new `OPENROUTER_API_KEY`.
- `.env` will contain `NOTION_API_KEY` and `NOTION_PARENT_PAGE_ID` for live Notion publishing.
- No new dependency should be added unless unavoidable.
- Notion publishing is allowed to create test pages/databases as side effects under the configured parent page.
- Timeline/gantt outputs should remain JSON-render summary artifacts because native charts intentionally support only `bar`, `line`, and `pie`.

## Future Plan Preference

All future plans in this repo should be saved as Markdown files, preferably under `docs/plans/`, unless the user names a different path.
