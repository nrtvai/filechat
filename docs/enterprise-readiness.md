# Enterprise Readiness Surfaces

FileChat enterprise readiness is split across small API surfaces so security and integration behavior can be tested without adding broad UI.

## Security And Settings

- `DELETE /api/admin/settings/openrouter-key` clears only keys saved by FileChat. `OPENROUTER_API_KEY` remains deployment-owned and cannot be cleared in-app.
- `GET /api/admin/audit-events` remains owner-only in enterprise mode.
- Audit metadata, meta issue metadata, wiki properties, and bot rejection metadata are sanitized before storage or export.
- Sensitive file metadata policy allows IDs, hashes, types, sizes, and statuses; filenames, paths, excerpts, content, tokens, and API keys are redacted in security surfaces.

## Provider Boundary

- Runtime model work goes through `backend.app.providers.provider_registry()`.
- OpenRouter is the only live provider.
- Future providers should be added as registry adapters rather than called directly from API, ingestion, or retrieval modules.

## Meta Issues

- `POST /api/meta-issues` records sanitized runtime errors or complaints for the current org.
- `GET /api/admin/meta-issues` and `PATCH /api/admin/meta-issues/{issue_id}` let admins triage local issues.
- GitHub mirroring is disabled unless `FILECHAT_META_ISSUES_GITHUB_ENABLED`, `FILECHAT_META_ISSUES_GITHUB_REPO`, and `FILECHAT_META_ISSUES_GITHUB_TOKEN` are set. Local capture remains the source of record.

## Wiki Graph

- `POST /api/wiki/nodes` and `POST /api/wiki/edges` provide typed graph groundwork.
- Organization nodes are visible inside the org. User nodes are visible only to their owner.
- This layer does not auto-populate from sessions, messages, or files.

## Bot Webhooks

- `POST /api/integrations/slack/events` requires Slack signing-secret verification.
- `POST /api/integrations/telegram/webhook` requires `X-Telegram-Bot-Api-Secret-Token`.
- Inline webhook attachments are normalized into the same queued file ingestion lifecycle as UI uploads.
- External provider file download is intentionally deferred until bot token configuration is introduced.

## Final Smoke Checklist

- Enterprise member cannot manage provider keys or read admin meta issue lists.
- Enterprise admin can clear a local provider key but cannot clear an env key.
- Owner can export audit events and see redacted metadata only.
- Meta issue creation redacts secrets and can optionally receive a GitHub URL.
- Wiki nodes and edges are isolated by organization and user scope.
- Slack/Telegram reject invalid auth and log sanitized bot meta issues.
- Slack/Telegram accept verified inline attachments and queue FileChat ingestion.
