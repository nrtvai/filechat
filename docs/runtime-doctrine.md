# Filechat + Searchchat Runtime Doctrine

Goal: make Filechat and Searchchat specialized agentic runtimes that feel as easy to use as Hermes, but outperform generic SOTA chat tools in their focused domains.

## Product roles

- **Filechat:** go-to wrapper for file workflows: documents, reports, spreadsheets, extraction, comparison, local/project data analysis, and grounded document writing.
- **Searchchat:** go-to wrapper for search workflows: research, proof, evidence, references, recency, source triangulation, and fact-checking.

## Runtime contract

Every run should produce a reviewable packet:
1. user request
2. plan / route
3. tool receipts
4. evidence packet
5. answer / artifact
6. citations or explicit no-evidence explanation
7. replayable trace
8. exportable result

The same contract should power CLI, web, tests, and build-lab reports.

## CLI + Web parity

CLI is for fast testing, automation, and reproducible proof. Web is for broad appeal and everyday use. Both must exercise the same runtime path instead of drifting into separate demos.

CLI output order:
1. direct answer or artifact summary
2. claims / findings
3. citations or file references
4. receipts / degraded sources
5. next action

Web output order:
1. chat transcript
2. visible composer
3. compact file/source chips
4. expandable citations/proof
5. advanced trace/settings/admin behind disclosure

## Build-lab discipline

Build-lab should constantly run representative workflows, not only unit tests:
- Filechat: upload/read/analyze files and prove cited outputs.
- Searchchat: ask research/fact-check queries and prove cited source receipts.
- CLI smoke: commands return useful human text and machine-readable JSON.
- Web smoke: production build succeeds and core chat UI is testable.
- Regression: never accept fake citations, hidden provider failures, or unreviewable answers.

## Edition boundary

Open-source community edition:
- simple local-first setup
- no enterprise admin burden on first run
- clear extension points
- safe default data directory
- honest degraded/offline behavior

Enterprise edition:
- sandboxed file/tool execution
- admin/provider controls
- access control
- audit logs
- redaction and data-retention policies
- connector credential boundaries
- deployment hardening

Do not optimize sales/growth until these runtime contracts are working and demonstrably useful.
