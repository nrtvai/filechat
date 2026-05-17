---
version: alpha
name: Evidence Workbench
description: Quiet, high-trust chat interfaces for file and search workflows; optimized for grounded answers, citations, and reviewable agent runs.
colors:
  primary: "#111827"
  secondary: "#4B5563"
  tertiary: "#2563EB"
  neutral: "#F8FAFC"
  surface: "#FFFFFF"
  success: "#047857"
  warning: "#B45309"
  danger: "#B91C1C"
typography:
  h1:
    fontFamily: Inter
    fontSize: 2.25rem
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.03em"
  h2:
    fontFamily: Inter
    fontSize: 1.25rem
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  body-md:
    fontFamily: Inter
    fontSize: 1rem
    fontWeight: 400
    lineHeight: 1.55
  mono-sm:
    fontFamily: JetBrains Mono
    fontSize: 0.8125rem
    fontWeight: 500
    lineHeight: 1.45
rounded:
  sm: 6px
  md: 12px
  lg: 20px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
components:
  button-primary:
    backgroundColor: "{colors.tertiary}"
    textColor: "#FFFFFF"
    rounded: "{rounded.md}"
    padding: 12px
  button-primary-hover:
    backgroundColor: "#1D4ED8"
    textColor: "#FFFFFF"
    rounded: "{rounded.md}"
    padding: 12px
  composer:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.lg}"
    padding: 16px
  citation-chip:
    backgroundColor: "#EFF6FF"
    textColor: "#1E3A8A"
    rounded: "{rounded.sm}"
    padding: 8px
  warning-banner:
    backgroundColor: "#FEF3C7"
    textColor: "{colors.warning}"
    rounded: "{rounded.md}"
    padding: 12px
  sidebar-surface:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.secondary}"
    rounded: "{rounded.md}"
    padding: 16px
  evidence-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.md}"
    padding: 16px
  success-badge:
    backgroundColor: "#ECFDF5"
    textColor: "{colors.success}"
    rounded: "{rounded.sm}"
    padding: 8px
  danger-banner:
    backgroundColor: "#FEE2E2"
    textColor: "{colors.danger}"
    rounded: "{rounded.md}"
    padding: 12px
---

## Overview

Evidence Workbench is the shared design language for Filechat and Searchchat. The interface should feel like a serious chat product, not an ops dashboard: one primary conversation, a calm sidebar, and progressive disclosure for sources, traces, settings, and enterprise controls.

The visual tone is quiet, legible, and auditable. Sophisticated work should happen behind the scenes; the user-facing surface should make it obvious where to type, what evidence was used, what failed, and what can be done next.

## Colors

- **Primary (#111827):** near-black text for long-form reading and professional trust.
- **Secondary (#4B5563):** subdued metadata, source descriptions, and low-priority controls.
- **Tertiary (#2563EB):** the only high-emphasis action color: send, run, open citation, confirm.
- **Neutral (#F8FAFC) and Surface (#FFFFFF):** high-contrast calm backgrounds for chat and reading.
- **Success / Warning / Danger:** reserved for factual runtime states; do not use status colors decoratively.

## Typography

Use Inter for UI and answer prose. Use JetBrains Mono only for IDs, citations, traces, hashes, JSON, and CLI parity surfaces. Favor readable body text over dense dashboard labels.

## Layout

Default web layout: left sidebar, main chat thread, bottom composer. Hide settings, trace, connector setup, provider config, and admin/security controls behind gear/details panels unless the user asks for them.

CLI layout should mirror the web information hierarchy: answer first, claims/citations second, receipts/trace third, raw JSON only behind `--json`.

## Elevation & Depth

Use thin borders and subtle shadow only to separate the composer, selected run, and citation cards. Avoid heavy glassmorphism or decorative gradients that reduce evidence readability.

## Shapes

Use rounded chat and composer surfaces, but keep evidence/citation chips compact. Enterprise/admin panels may use sharper table-like containment to signal operational controls.

## Components

- `composer` is the primary action zone and must stay visible on empty/default states.
- `citation-chip` links every substantive claim to inspectable evidence.
- `warning-banner` is for honest uncertainty, missing provider setup, degraded connectors, or unsafe output refusal.
- Primary buttons should be rare; most secondary actions should be plain text buttons or icon buttons.

## Do's and Don'ts

Do:
- Lead with the best grounded answer, then show citations and receipts.
- Keep source/proof details one click away, not hidden in logs.
- Make CLI and web outputs test the same runtime contract.
- Use design tokens so Filechat and Searchchat stay visually related.

Don't:
- Default to dashboards, traces, or provider setup screens.
- Show fake citations, fake progress, or synthetic source confidence.
- Put enterprise controls in the open-source default path.
- Copy code or visual assets from reference projects without an explicit compatible license review.
