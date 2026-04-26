# Artifact Rendering Research

FileChat should support model-created charts, illustrations, reports, and slide decks through validated structured artifacts, not arbitrary model-generated HTML. The model should produce strict JSON specs, the backend should validate and store those specs with source chunk provenance, and the frontend should render them through a small allowlisted component set.

## Recommended Shape

- Add persisted artifact records for `chart`, `illustration`, `report`, and `slide_deck`, each linked to the session, source chunks, and the message that requested it.
- Validate generated specs on the backend with Pydantic and mirror critical schemas in TypeScript for the renderer.
- Render previews in the transcript and right panel with explicit export controls.
- Keep export deterministic and server-side where possible so exported reports and decks match stored artifact specs.

## Tooling Options

- [json-render](https://github.com/vercel-labs/json-render): broad guardrailed generated-UI framework. It is a strong fit if FileChat needs general model-composed dashboards or artifact panels, but it is likely too broad for a first charts/reports pass.
- [DESIGN.md](https://github.com/google-labs-code/design.md): alpha design-token contract with lint, diff, and export commands. Use it as an optional design consistency input, not as a hard dependency in persisted artifact data yet.
- [Vega-Lite](https://vega.github.io/vega-lite/) plus [Vega-Embed](https://vega.github.io/vega-embed/): best first target for charts because the chart is a declarative JSON grammar and can be exported as SVG/PNG.
- [PptxGenJS](https://gitbrent.github.io/PptxGenJS/docs/introduction/): practical slide export path for `.pptx`, including text, images, tables, and native charts.
- [WeasyPrint](https://weasyprint.org/index.html): natural FastAPI-side HTML/CSS to PDF option for reports.
- [React-PDF](https://react-pdf.org/): useful if report layouts should share a React-style component model and Node-side PDF rendering.

## First Implementation Slice

Start with a `chart` artifact using a constrained Vega-Lite schema and inline data derived from retrieved file chunks. Store the Vega-Lite spec, cited chunk IDs, and a short natural-language caption. Render it in React with Vega-Embed and export SVG/PNG using Vega's export path. Once charts are reliable, add `report` artifacts that can embed chart SVGs and export to PDF with WeasyPrint.

## Risks

- Generated chart data must be traceable to source chunks; strict grounding should reject unsupported numbers instead of drawing them.
- `DESIGN.md` is promising but alpha, so it should shape prompts/styles without becoming a migration-sensitive storage format.
- `json-render` is powerful but young and broad; wrap it behind an adapter if adopted.
- Export fidelity differs across SVG, PNG, PDF, and PPTX. Prefer SVG-first charts and embed those SVGs into reports and decks.
