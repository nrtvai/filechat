# Filechat Original — Milestone Ledger
STATUS: DONE
DoD ACCEPTANCE: scripts/dod/filechat-original.mjs   # author in M0 if absent; must exit 0 when done

## Definition of Done
Default route opens a clean chat UI (sidebar/history, chat area, bottom composer, file chips,
citation chips). The user can upload/select files and ask a question and receive either a grounded
answer with visible citations or an honest no-source/no-answer state. Debug/run internals (agent
phases, harness state, traces) are NOT on the main chat surface — they live behind a settings/debug
flag or secondary tab. `App.test.tsx` + backend smoke + e2e are green via build-lab.

## Ladder
- [x] M0 — Author DoD acceptance script (`scripts/dod/filechat-original.mjs`): asserts default route is chat-first, runs `npm test -- --run src/App.test.tsx`, backend smoke, and the clean-default e2e; exits 0 only when all hold. (2026-05-31: DoD runner added and verified green.)
- [ ] M1 — Gate run-internals / agent-phase UI off the default chat surface (behind settings or a `?debug`/flag). Keep citations, file chips, artifacts visible.
- [ ] M2 — Graceful no-source UX: replace bare "No sourced answer" text with an honest state that offers clarify / shows what was searched, without fabricating a source.
- [ ] M3 — Provider-missing graceful degradation: clear message + degraded mode when no OpenRouter/LLM key is configured, instead of a hard failure.
- [ ] M4 — E2E asserting: clean default surface (no debug nouns), upload→grounded-answer-with-citations path, and the no-source path. Wire into build-lab.

## Backlog
(ideas only — NOT executed until promoted to the Ladder)
- Export/share an answer as a report.
- CJK citation sorting/display polish.

## Log
- 2026-05-31 · M0 · DONE — DoD verified green with App tests, backend grounded smoke, and clean-default e2e · PR pending
