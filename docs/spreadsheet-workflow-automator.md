# Spreadsheet Workflow Automator

Spreadsheet Workflow Automator is a separate product surface in this repository. It lives at `/workflows`, uses the dedicated `/api/workflows/interview` and `/api/workflows/generate` endpoints, and produces downloadable local HTML workflow apps for recurring dependent-spreadsheet work.

The product is not the Filechat chat UI. It does not answer generic spreadsheet questions or fabricate steps from vague prompts. When the workflow lacks concrete source files, manual copy/paste/edit steps, matching keys, or deterministic output rules, it returns interview questions.

## Local Run

- API: `npm run dev:api`
- Workflow app: `npm run dev:spreadsheet-automator`
- Production build: `npm run build:spreadsheet-automator`

Open `http://127.0.0.1:5174/workflows`.

## Contract

- Vague request: call `/api/workflows/interview`; show required questions; do not create HTML.
- Specified request: call `/api/workflows/generate`; download `spreadsheet-workflow-automator.html`.
- Runtime output: generated HTML must run locally, require concrete spreadsheet inputs, reject ambiguous inputs, and download the final output without network dependencies.
