---
name: Maverick Agent Pipeline
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#434655'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#737686'
  outline-variant: '#c3c6d7'
  surface-tint: '#0053db'
  primary: '#004ac6'
  on-primary: '#ffffff'
  primary-container: '#2563eb'
  on-primary-container: '#eeefff'
  inverse-primary: '#b4c5ff'
  secondary: '#712ae2'
  on-secondary: '#ffffff'
  secondary-container: '#8a4cfc'
  on-secondary-container: '#fffbff'
  tertiary: '#005a89'
  on-tertiary: '#ffffff'
  tertiary-container: '#0073ae'
  on-tertiary-container: '#e7f2ff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dbe1ff'
  primary-fixed-dim: '#b4c5ff'
  on-primary-fixed: '#00174b'
  on-primary-fixed-variant: '#003ea8'
  secondary-fixed: '#eaddff'
  secondary-fixed-dim: '#d2bbff'
  on-secondary-fixed: '#25005a'
  on-secondary-fixed-variant: '#5a00c6'
  tertiary-fixed: '#cce5ff'
  tertiary-fixed-dim: '#93ccff'
  on-tertiary-fixed: '#001d31'
  on-tertiary-fixed-variant: '#004b73'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: -0.005em
  body-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  body-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: 0em
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
    letterSpacing: 0em
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.02em
  mono-md:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: 0em
  mono-sm:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 16px
    letterSpacing: -0.01em
  mono-xs:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  space-2xs: 2px
  space-xs: 4px
  space-sm: 8px
  space-md: 12px
  space-lg: 16px
  space-xl: 20px
  space-2xl: 24px
  space-3xl: 32px
  gutter: 12px
  sidebar-collapsed: 48px
  sidebar-expanded: 240px
  inspector-width: 380px
---

## Brand & Style

The design system embodies precision engineering, high observability, and calm computational authority. Tailored for staff engineers, infrastructure architects, and technical engineering leaders orchestrating autonomous AI agent pipelines, the visual language avoids frivolous ornament and decorative excess. It prioritizes low visual latency, extreme information clarity, and tactile predictability.

The aesthetic fuses modern developer tooling (such as Linear and GitHub Enterprise) with the robust operational rigour of mission-critical telemetry consoles (like Datadog and Argo Workflows). Structural borders, purposeful micro-interactions, explicit status mapping, and high-density layouts build confidence during autonomous execution, manual pipeline intervention, and code validation.

## Colors

The system employs a crisp, slate-dominated light theme that maximizes legible contrast in data-heavy environments. Working surfaces and modal canvases sit on pure white (`#FFFFFF`), separated from the system backdrop (`#F8FAFC`) and auxiliary structural panels (`#F1F5F9`) via subtle neutral borders (`#E2E8F0` and `#CBD5E1`).

### Primary & Specialty Accents
- **Primary Cobalt (`#2563EB` / Active: `#1D4ED8`):** Anchors primary actions, commit transitions, and active navigation nodes.
- **AI Agentic Violet (`#7C3AED` / Light: `#EDE9FE`):** Explicitly reserved for AI planning, autonomous agent deliberations, prompt generation, and LLM inference states.
- **In-Flight Sky (`#0284C7` / Light: `#E0F2FE`):** Signifies live execution, spinning sub-tasks, and asynchronous pipeline streaming.

### Semantic Status Colors
- **Success (`#059669` / Surface: `#ECFDF5` / Border: `#A7F3D0`):** Completed steps, healthy builds, passing automated tests.
- **Warning / Hold (`#D97706` / Surface: `#FFFBEB` / Border: `#FDE68A`):** Human-in-the-loop review needed, budget soft-limits, paused runs.
- **Critical Failure (`#DC2626` / Surface: `#FEF2F2` / Border: `#FECACA`):** Pipeline halts, syntax/runtime crashes, policy violations.

### Neutral Scale
- Headings & Primary Text: `#0F172A`
- Body Text & Structural UI: `#334155`
- Secondary Meta & Subtle Labels: `#64748B`
- Strong Outlines & Delimiters: `#CBD5E1`
- Subtle Hairline Dividers: `#E2E8F0`
- Inactive Canvas & Rail Backgrounds: `#F1F5F9`
- Global Application Canvas: `#F8FAFC`
- Card, Table & Work Surface: `#FFFFFF`

## Typography

The typography strategy pairs a dense, highly legible neo-grotesque (`Inter`) for natural text and user interaction controls with a monospaced workhorse (`JetBrains Mono`) for programmatic artifacts. 

- Use `Inter` for layout hierarchy, navigation labels, contextual dialogs, and table cell headers.
- Reserve `JetBrains Mono` exclusively for commit SHAs, git branch names, pipeline run IDs, LLM token usages, API latency metrics, and terminal stdout/stderr logs.
- Utilize tabular numbers (`tnum`) across all tabular rows, real-time counters, and latency figures to avoid horizontal jitter during live telemetry updates.

## Layout & Spacing

The layout is built on a high-density, multi-pane fluid workspace optimized for high-resolution displays (1440px and wider).

### Grid & Panes
- **Global App Shell:** Composed of a fixed-width left rail (collapsible between 48px and 240px), a flexible primary canvas for node graphs and log streams, and an optional collapsible contextual inspector (380px) on the right.
- **Content Panes:** Standard nested padding within cards and panels is fixed at 12px or 16px to prevent wasted screen real estate.
- **Rhythm:** An ultra-disciplined 4px baseline rhythm governs all components. Compact table rows maintain a strict height of 32px or 36px, maximizing visible entries without requiring scrolling.

### Responsive Behavior
- **Desktop (1280px+):** Full three-pane view simultaneously active (Navigation, Main Pipeline/DAG/Data Grid, Contextual Node Inspector).
- **Tablet / Narrow Desktop (1024px - 1279px):** Inspector defaults to an overlaid slide-out drawer; main table horizontally scrolls with sticky key identifier columns.
- **Mobile (<1024px):** Primary focus shifts to sequential list views, task run alerts, and status badges; DAG nodes collapse to vertical step progress cards.

## Elevation & Depth

Visual hierarchy is maintained through crisp structural borders, subtle surface tone shifting, and low-amplitude ambient shadows. The system strictly avoids floating, heavily blurred drop shadows.

- **Level 0 (App Canvas):** `#F8FAFC` base tone. No shadow, flush surface.
- **Level 1 (Panels & Cards):** `#FFFFFF` surface with a 1px continuous border of `#E2E8F0`. Rest state shadow: `0 1px 2px 0 rgba(15, 23, 42, 0.04)`.
- **Level 2 (Hovered Rows & Floating Toolbars):** `#FFFFFF` surface with a 1px border of `#CBD5E1`. Shadow: `0 2px 4px -1px rgba(15, 23, 42, 0.06), 0 1px 2px -1px rgba(15, 23, 42, 0.04)`.
- **Level 3 (Dropdowns, Command Palette, Modals):** `#FFFFFF` surface with a 1px border of `#CBD5E1`. Shadow: `0 8px 16px -4px rgba(15, 23, 42, 0.08), 0 2px 4px -2px rgba(15, 23, 42, 0.04)`.
- **Level 4 (DAG Graph Drag Nodes & Critical Overlays):** `#FFFFFF` surface with a 1px border of `#94A3B8`. Shadow: `0 12px 24px -6px rgba(15, 23, 42, 0.12)`.

## Shapes

The design system uses a tight, structured geometric profile. Elements use 6px standard corner radii for inputs, buttons, and badges, and 8px for containers, panels, and modals. 

- Controls (Inputs, Buttons, Segmented Selectors, Chips): `rounded-md` (6px).
- Containers (Cards, Table wrappers, Modals, Flyout Panels): `rounded-lg` (8px).
- Internal Badges & Micro-tags: `rounded` (4px).
- Status Indicator Dots & Avatars: Fully circular (50% / `rounded-full`).

## Components

### Buttons
- **Primary:** `#2563EB` solid background, `#FFFFFF` text, 1px border of `#1D4ED8`. Hover: `#1D4ED8`. Active: `#1E40AF`. Height: 32px (compact), 28px (dense).
- **Secondary / Outline:** `#FFFFFF` background, `#334155` text, 1px border of `#CBD5E1`. Hover: `#F8FAFC` background with border `#94A3B8`.
- **Ghost / Subtle:** Transparent background, `#64748B` text. Hover: `#F1F5F9` background, `#0F172A` text.
- **Specialized AI Action Button:** `#7C3AED` solid background, `#FFFFFF` text, with micro sparkle icon prefix. Used only for autonomous synthesis and agent prompt execution.

### Status Badges & Chips
- Formed with a 1px border, 4px corner radius, and font size `11px` JetBrains Mono or Inter medium.
- **Success Badge:** Background `#ECFDF5`, text `#065F46`, border `#A7F3D0`.
- **Running / Queued:** Background `#E0F2FE`, text `#0369A1`, border `#BAE6FD`. Contains animated 6px pulse dot.
- **Review / Paused:** Background `#FFFBEB`, text `#92400E`, border `#FDE68A`.
- **Failed / Error:** Background `#FEF2F2`, text `#991B1B`, border `#FECACA`.
- **AI Agent Node:** Background `#F5F3FF`, text `#5B21B6`, border `#DDD6FE`.

### Data Tables & Compact Rows
- Alternating or flush `#FFFFFF` rows with a bottom hairline border of `#E2E8F0`.
- Row height: 32px for high-density log/run tables, 40px for higher-level workflow overview tables.
- Row hover: `#F8FAFC` background transition with keyboard navigation highlight (`#2563EB` 2px left-edge border indicator).
- Column headers: `#F8FAFC` background, 11px uppercase Inter, medium weight, `#64748B` color.

### Form Inputs & Filters
- **Text & Monospaced Input:** 32px height, 6px radius, `#FFFFFF` background, 1px `#CBD5E1` border, `#0F172A` text. Placeholder `#94A3B8`. Focus: 1px `#2563EB` with `0 0 0 2px rgba(37, 99, 235, 0.15)`.
- **Inline Filter Dropdowns:** Flat background `#F1F5F9`, borderless until hovered, presenting key-value syntax (`status:failed`, `agent:codegen-4`).

### Pipeline DAG Node Cards
- Dedicated workflow canvas nodes: Minimum width 220px, `#FFFFFF` background, 8px radius, 1px border `#E2E8F0`.
- Header strip indicates runtime status color bar (3px top accent or status icon).
- Body features node name (12px Inter semi-bold), task duration, and token cost metric in 11px `JetBrains Mono`.
- Connecting ports: 8px circular nodes embedded into border midpoints.