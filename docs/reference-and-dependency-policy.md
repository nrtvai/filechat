# Reference and Dependency Policy

This project may study open-source tools and products for product patterns, UX expectations, architecture trade-offs, and benchmark ideas, but must not copy source code, proprietary assets, or distinctive UI implementations from reference projects.

## Safe reference use

Allowed:
- Reading docs to understand workflows, APIs, and UX conventions.
- Re-implementing generic ideas in original code.
- Citing reference projects in design notes or issue discussions.
- Adding third-party tools only when their license, maintenance posture, and security posture are acceptable.

Not allowed without explicit review:
- Copy-pasting implementation code.
- Copying visual assets, icons, screenshots, themes, prompts, or dataset content.
- Adding dependencies with unknown, restrictive, viral, deprecated, or abandoned licenses.
- Adding packages that execute remote code, collect telemetry by default, or require broad credentials without sandboxing.

## Preferred dependency license posture

Prefer permissive, well-known licenses: MIT, Apache-2.0, BSD-2/3-Clause, ISC, CC0 for data where appropriate. GPL/AGPL/LGPL, custom commercial licenses, source-available licenses, model/data licenses, and copyleft assets require a specific product decision before incorporation.

## Security posture

Before incorporating a tool:
- Verify the package source, maintainer, release cadence, and license.
- Prefer small, widely used dependencies over opaque frameworks.
- Avoid packages that need secrets in the browser.
- Keep network/search connectors isolated behind explicit adapter contracts and timeouts.
- For enterprise versions, require audit logs, access-control checks, redaction boundaries, and sandboxed execution for untrusted files or tools.

## Product boundary

Open-source defaults should remain simple and locally understandable. Enterprise-only hardening can live behind edition flags, separate modules, or documented extension points so community users are not forced through admin/security setup just to try the product.
