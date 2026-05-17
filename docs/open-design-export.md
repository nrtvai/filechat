# Open Design-compatible exports

FileChat can export eligible `file_draft` artifacts as lightweight Open Design-compatible ZIP bundles for design documents and source-grounded materials.

The first slice is export-only and intentionally does **not** embed the Open Design runtime, run a daemon, or render arbitrary HTML previews. FileChat continues to render drafts safely as Markdown/text in the app.

A ZIP produced with `format=od` contains:

- `SKILL.md` — minimal skill metadata with Open Design-compatible frontmatter.
- `DESIGN.md` — a neutral nine-section design system fallback.
- `content.md` — the grounded FileChat draft content.
- `metadata.json` — normalized material metadata and FileChat source artifact/chunk references.

Open Design ZIP is shown only for `file_draft` artifacts whose spec includes normalized `open_design` metadata. Normal drafts remain exportable as Markdown, JSON, Notion JSON, and PDF.
