# FileChat

FileChat is a local-first document workbench for grounded reading, analysis, and artifact creation. It combines a React/Vite frontend with a FastAPI backend, keeps session data on your machine, and uses OpenRouter only for model calls such as planning, embeddings, OCR-assisted extraction, and grounded writing.

The current product direction is simple: make runs honest. If FileChat can answer, it should answer with citations. If it can create something useful from attached files, it should produce a real artifact. If it cannot finish safely, it should pause with a clear question or setup instruction instead of pretending success.

FileChat is also split at the product boundary: Community edition defaults to a single local owner for the open-source app, while Enterprise edition enables role-gated management surfaces for paid org administration.

## What It Does

- Upload and index PDFs, Office files, spreadsheets, CSV/TSV, Markdown/text, and common image formats.
- Ask grounded questions over attached files with cited source chunks.
- Run model-led but tool-backed workflows for planning, search, analysis, writing, review, and implement.
- Build safe artifacts such as charts, tables, summary panels, Mermaid diagrams, and exportable file drafts.
- Profile survey-style CSVs and generate deterministic chart/table outputs before relying on fragile model formatting.
- Persist runs, review results, prompt context snapshots, repair attempts, and artifact history for debugging.
- Degrade gracefully when model/provider steps fail by using local structured analysis where possible.

## Why This Repo Exists

Most file chat products fail in one of two ways:

- they hallucinate a polished answer with weak grounding
- they enforce a schema so rigidly that users get technical failure text instead of useful work

FileChat is trying to sit in the harder middle: the model can plan and synthesize, but deterministic code owns parsing, validation, citations, artifact safety, and completion semantics.

## Product Shape

### Runtime model

FileChat stores each run as a persisted workflow with:

- a planner contract: what the model believes the user wants
- an executable contract: what the system can actually promise with the current files, tools, and provider state
- explicit run states such as `awaiting_user_input`, `needs_setup`, `needs_revision`, `completed`, and `completed_with_warning`

### Artifact policy

- Primary artifacts render inline in chat.
- Supporting artifacts stay available through inspect panels and the runs/artifacts sidebars.
- Survey/material requests default to a practical bundle: draft + chart first, extras only when they add value.

### Context model

Prompt construction is layered and persisted per run:

- product policy
- prompt pack version notes
- user display preferences
- session brief
- file intelligence
- task contract
- evidence packet

That makes behavior easier to inspect and safer to evolve across prompt upgrades.

## Stack

- Frontend: React 19, TypeScript, Vite, Vitest, Playwright
- Backend: FastAPI, SQLite, `uv`, pytest
- Model gateway: OpenRouter through the backend provider registry
- Rendering: custom safe artifact renderer plus constrained JSON render support for legacy layouts

## Local Development

### Requirements

- Node.js 20+
- Python 3.10+
- `uv`
- OpenRouter API key for real model-backed runs

### Setup

```bash
npm install
uv sync --extra dev
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`, or add it later from the in-app Settings panel. Environment variables take precedence over locally stored keys and cannot be cleared from the app; admins can only clear keys saved by FileChat.

### Run the app

```bash
npm run dev:api
npm run dev
```

Or start both together:

```bash
npm run dev:all
```

Frontend: [http://127.0.0.1:5173](http://127.0.0.1:5173)
API: [http://127.0.0.1:8000](http://127.0.0.1:8000)

Local data is stored in `.filechat/` by default. Override with `FILECHAT_DATA_DIR`.

## Environment

```bash
OPENROUTER_API_KEY=
FILECHAT_CHAT_MODEL=openai/gpt-4o-mini
FILECHAT_EMBEDDING_MODEL=openai/text-embedding-3-small
FILECHAT_OCR_MODEL=openai/gpt-4o-mini
FILECHAT_DATA_DIR=.filechat
FILECHAT_ALLOW_FAKE_OPENROUTER=false
FILECHAT_EDITION=community
FILECHAT_AUTH_TEST_MODE=false
FILECHAT_TRUSTED_AUTH_HEADERS=false
FILECHAT_META_ISSUES_GITHUB_ENABLED=false
FILECHAT_META_ISSUES_GITHUB_REPO=
FILECHAT_META_ISSUES_GITHUB_TOKEN=
FILECHAT_SLACK_SIGNING_SECRET=
FILECHAT_TELEGRAM_WEBHOOK_SECRET=
```

`FILECHAT_ALLOW_FAKE_OPENROUTER=true` is intended only for tests and local smoke checks that should not call the network.

Use `FILECHAT_EDITION=enterprise` with `FILECHAT_AUTH_TEST_MODE=true` to switch between owner, admin, and member roles in the UI without creating real accounts. Production enterprise deployments should keep test mode off and only set `FILECHAT_TRUSTED_AUTH_HEADERS=true` behind a trusted authentication proxy or adapter that strips untrusted inbound role headers.

Set `FILECHAT_EDITION=enterprise` to enable enterprise boundaries. In enterprise mode, members can use sessions and files, admins can manage provider/model settings, and only owners can export audit logs. Set `FILECHAT_AUTH_TEST_MODE=true` in local development to impersonate owner, admin, and member roles without creating real accounts. Audit metadata is append-only and redacted before storage; file security metadata is limited to non-content identifiers such as IDs, hashes, type, size, and status.

Runtime complaints and internal failures can be captured as sanitized meta issues with `POST /api/meta-issues`; admins can review and triage them under `/api/admin/meta-issues`. GitHub issue creation is off by default and only runs when the `FILECHAT_META_ISSUES_GITHUB_*` settings are present.

Org/user LLM wiki groundwork is exposed as API-only graph storage under `/api/wiki/nodes` and `/api/wiki/edges`. Nodes and edges are scoped to the current organization, user-scoped nodes are visible only to their owner, and properties/source references are sanitized before storage.

Slack and Telegram webhook scaffolding is available at `/api/integrations/slack/events` and `/api/integrations/telegram/webhook`. Slack requests must pass signing-secret verification, Telegram requests must include `X-Telegram-Bot-Api-Secret-Token`, and inline file payloads are queued through the same ingestion lifecycle as UI uploads.

## Verification

```bash
uv run pytest
npm run test
npm run lint
npm run build
npm run test:e2e
```

## Repository Safety

This repository is intended to protect `main` with pull-request review rather than direct, silent merges. The recommended baseline is:

- require pull requests before merging to `main`
- require one approving review
- require code owner review
- use `CODEOWNERS` so review requests go to `@nrtvai`
- keep an owner/admin bypass available to avoid locking the repo if the only maintainer authored the PR

## Project Layout

```text
backend/app/      FastAPI app, runtime, retrieval, orchestration, artifact validation
backend/tests/    Backend regression and API coverage
src/              React app, artifact rendering, runs UI, settings UI
tests/e2e/        Playwright end-to-end coverage
docs/             Research notes and planning docs
```

## Status

This is still an actively evolving local product. The control layer, prompt context system, survey path, and repository safety defaults are all being tightened so the app behaves more like a reliable tool and less like a phase-shaped demo.
