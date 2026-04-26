# FileChat Question-Centric Agent Planning Harness

## Summary

- Add an in-app planning gate that asks the user whether they want a short interview for a better outcome or want FileChat to proceed automatically.
- Use an Ouroboros-style ambiguity loop inside FileChat runs, not in the Codex session: detect ambiguity, ask one useful question at a time, persist answers, revise the plan, then resume the pipeline.
- Redefine "no failure" as a product invariant: every run must end in a useful artifact/answer, a deterministic fallback, or a clear user-question state. Never end with vague schema/refusal text when a next action exists.

## Key Changes

- Extend run status/types with `awaiting_user_input`; keep existing `awaiting_approval` for cost/risk approval.
- Add persisted run questions:
  - `id`, `run_id`, `phase`, `question`, `kind`, `options_json`, `default_option`, `answer_json`, `status`, timestamps.
  - Question kinds: `interview_offer`, `clarification`, `choice`, `missing_context`, `approval`.
- Add run events and workspace:
  - Events record controller decisions, tool calls, question asks/answers, retries, fallbacks.
  - Workspace stores plan notes, source maps, table profiles, draft outlines, artifact candidates, validator reports.
- Replace the current linear `execute_agent_run` with a bounded controller loop:
  - `plan`: classify task, inspect source readiness, build ambiguity score, create todos.
  - If broad/ambiguous: ask "Do you want a short interview for a better result, or should I handle it automatically?"
  - If user chooses interview: ask up to 3 targeted questions, one at a time, then resume.
  - If user chooses automatic: commit FileChat's inferred default plan and continue.
  - Continue through search, analysis, writing, review, implement using persisted events and workspace state.
- Add deterministic defaults for vague create prompts:
  - `분석 자료 제작` plus survey/table source -> Korean analysis brief plus chart/table artifact.
  - Vague chart request plus table source -> choose the best categorical/numeric aggregate and cite it.
  - Vague draft request plus sources -> Markdown draft with source-backed sections.
- Add failure recovery contracts:
  - Model failure -> deterministic artifact/draft fallback when source data permits.
  - Schema failure -> repair once, then deterministic fallback.
  - Missing source readiness -> ask/wait/retry, not final refusal.
  - Ambiguous deliverable -> interview offer, then automatic default if the user chooses speed.

## API And UX

- Add endpoints:
  - `GET /api/sessions/{session_id}/runs/{run_id}/questions/current`
  - `POST /api/sessions/{session_id}/runs/{run_id}/questions/{question_id}/answer`
  - `GET /api/sessions/{session_id}/runs/{run_id}/events?after_seq=...`
  - `GET /api/sessions/{session_id}/runs/{run_id}/workspace`
- Frontend:
  - Show an inline "Planning needs a choice" card in the transcript and Runs panel.
  - First choice for broad tasks: "Interview me" vs "Handle automatically".
  - For interview mode, show one compact question at a time with suggested options.
  - For automatic mode, show the inferred plan before continuing: output type, source files, tools, fallback path.
  - Replace scary technical artifact failure text with user-safe status plus expandable diagnostics.

## Test Plan

- Backend:
  - Broad Korean prompt creates an `interview_offer` question before analysis.
  - Choosing automatic resumes and produces analysis brief plus chart/table from survey CSV.
  - Choosing interview asks targeted follow-up questions and stores answers in run state.
  - Missing/invalid model output triggers repair or deterministic fallback, not final schema failure.
  - Run events and workspace persist across polling and retry.
- Frontend:
  - Question card renders with "Interview me" and "Handle automatically".
  - Answering a question resumes the run and updates timeline/events.
  - Automatic path shows inferred plan and completes without extra friction.
  - Interview path shows one question at a time.
- E2E:
  - Upload Korean survey CSV.
  - Ask `분석 자료 제작`.
  - Choose automatic, verify draft/chart/table appear.
  - Retry with interview choice, answer one question, verify improved tailored output.
- Verification:
  - `uv run pytest`
  - `npm run test`
  - `npm run build`
  - `npm run test:e2e`

## Assumptions

- No new dependency is added; DeepAgents/Ouroboros are architectural references.
- User questions happen inside the FileChat run planning phase.
- FileChat defaults to offering the interview for broad tasks, while preserving a fast automatic path.
- "Guarantee no failure" means no dead-end UX: the app must produce a valid result, fallback, or actionable question state.
