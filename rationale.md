# FileChat — Design Rationale

## Positioning
A local-first reading tool for thinking with documents. Not a chatbot, not a doc manager. The interface should feel like a quiet library workbench: paper, ink, a single ochre accent, and a mechanical typeface reserved for system truths (file sizes, token counts, processing stages).

## Layout logic
- **Left rail (collapsible):** Session list by default. A small toggle flips it to Files for this session. Rail can collapse to icons for focus mode.
- **Center:** The page. Empty state centers the composer like a title page; once a transcript exists, the composer docks to the bottom and the transcript scrolls above it.
- **Right rail (tabbed, persistent, collapsible to edge):** Files · Citations · Settings. Tabs rather than a drawer because the user is continuously referencing sources while reading answers — a drawer hides context.
- **Margin citations:** Citations appear both as inline superscripts in the answer AND as editorial margin cards in the right rail when the Citations tab is open. This mirrors how scholars actually read — body text and notes in peripheral vision simultaneously.

## Status language
Plain verbs — *Queued · Reading · Indexing · Ready · Failed* — paired with a thin 1px capillary progress line under each file chip. No spinners inside the transcript. No percentages unless you hover.

## Why three themes
Archive (warm) is default. Atelier (cool, graphite) is for people who find warm neutrals too soft. Reading Room (forest green accent) is for long sessions. All share the same type scale and spacing; only hues differ.

## What we resisted
- Purple gradients, "magic" language, glowing orbs.
- Icons for every verb (we let type do the work).
- Doc-manager first: the composer is always the center of gravity.
- Enterprise sprawl: no teams, no sharing, no workspaces.
