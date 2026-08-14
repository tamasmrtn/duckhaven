# DuckHaven — UI Design

> Companion to `ARCHITECTURE.md`. Defines the visual language, information
> architecture, and key screens for the DuckHaven control plane SPA
> (`duckhaven-api` is the contract; this document covers the React frontend
> described in §6 (the `web/` code map) and §8 of the architecture).
>
> **Status:** Implemented — describes the shipped DuckHaven SPA.
>
> **Audience:** the engineer(s) building the frontend.

---

## 1. Design DNA

Three forces shape every decision:

1. **DuckDB-first.** DuckHaven is for people who already love DuckDB. The
   visual identity borrows directly from `duckdb.org/design/manual/`: lemon
   yellow as the brand anchor, the "railroad diagram" motif (curved
   connectors, junction points, rounded rectangles, 2–3px strokes) as a
   structural cue, monospace numerals everywhere data appears.
2. **Open-source first.** The aesthetic target is a polished developer tool
   (think Linear, Supabase Studio, Sourcegraph), not an enterprise SaaS
   console. No marketing gradients, no decorative hero illustrations inside
   the app. Density and keyboard-first interaction over breathing room.
3. **Homelab-scale.** Two to ten users on Tailscale, not 10,000 on the open
   internet. The chrome is small. There is one primary surface — the
   **worksheet** — and everything else is one click away from it.

We're **not** trying to look like Databricks or Snowflake. Those products
serve very different audiences (enterprise admins, BI analysts) and lean on
heavy chrome — large left rails, multi-pane Genie/Copilot panels, dense
ribbon toolbars. We borrow their proven *patterns* (catalog tree on the
left, tabbed worksheets, role/warehouse context selector in the worksheet
header, results below the editor) but keep the surface area roughly half
the size.

### What we take from each reference

| From Databricks SQL Editor | From Snowflake Snowsight | From DuckDB itself |
|---|---|---|
| Tabbed worksheets, command palette (Cmd/Ctrl-K) | Per-worksheet context bar (warehouse + role → here: **agent + workspace**) | Lemon yellow brand, railroad/junction motif |
| Catalog browser permanently docked left | Worksheet explorer as a flat searchable list | Monospace identifiers, dark code blocks with syntax tokens |
| Multiple statement results stacked under the editor | Tab-state preservation across sessions | Sparse, technical, no-nonsense voice |
| Real-time progress feedback per statement | "Open in new tab" everywhere | Light mode primary; dark mode honored, not centered |

### What we explicitly reject from them

- **No notebook/cell model** (architecture D4). A worksheet is one buffer.
- **No AI assistant panel in MVP**. The right side of the worksheet is for
  results, not chat.
- **No marketplace, no governance dashboard**. Those are enterprise
  distractions. Lineage is the exception we did take: it earns its place as a
  tab on the table you are already looking at, not as a destination of its own.
- **No expandable mega-nav**. Five top-level destinations max.

---

## 2. Visual Language

### 2.1 Color

The palette is anchored to DuckDB's official brand colors and extended
with neutrals for UI chrome. Light mode is the default surface; dark mode
is a fully-supported peer (D4 of this doc, §9 of architecture's threat
model treats dark mode as a parity requirement, not a nice-to-have, because
data engineers run terminals all day).

#### Brand (from `duckdb.org/design/manual/`)

| Token | Light hex | Dark hex | Use |
|---|---|---|---|
| `--brand-yellow` | `#FFF100` | `#FFF100` | Logo, primary CTA accent, "run" state highlight |
| `--brand-orange` | `#FF6900` | `#FF8A33` | Warnings, in-flight query progress, agent attention |
| `--brand-slate-blue` | `#7D66FF` | `#9C8AFF` | Selection, focus rings, links |
| `--brand-maya-blue` | `#2EAFFF` | `#5BC0FF` | Informational badges, schema icons |

The yellow is *loud*. Use it on ≤2% of pixels at any time — the **Run**
button, the active worksheet tab indicator, the logo. Never as a
background field, never as body text.

#### Neutrals (Tailwind `slate` derived)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg-canvas` | `#FFFFFF` | `#0B0F19` | App background |
| `--bg-surface` | `#F8FAFC` (slate-50) | `#111827` (gray-900) | Cards, panels |
| `--bg-elevated` | `#FFFFFF` | `#1F2937` (gray-800) | Modals, dropdowns, command palette |
| `--bg-code` | `#0B0F19` | `#0B0F19` | SQL editor background (same in both modes — Monaco) |
| `--border-subtle` | `#E2E8F0` (slate-200) | `#1F2937` (gray-800) | Pane dividers |
| `--border-strong` | `#CBD5E1` (slate-300) | `#374151` (gray-700) | Input borders, hovered dividers |
| `--text-primary` | `#0F172A` (slate-900) | `#F1F5F9` (slate-100) | Body, headings |
| `--text-secondary` | `#475569` (slate-600) | `#94A3B8` (slate-400) | Metadata, captions |
| `--text-tertiary` | `#94A3B8` (slate-400) | `#64748B` (slate-500) | Placeholders, disabled |

#### Semantic

| Token | Light | Dark | Use |
|---|---|---|---|
| `--status-queued` | `#94A3B8` (slate-400) | `#64748B` | Query in queue |
| `--status-running` | `#FF6900` (orange) | `#FF8A33` | Query executing |
| `--status-success` | `#16A34A` (green-600) | `#22C55E` | Done, agent healthy |
| `--status-failed` | `#DC2626` (red-600) | `#EF4444` | Errors, agent down |
| `--status-cancelled` | `#475569` (slate-600) | `#94A3B8` | User-cancelled query |

All foreground/background pairs validated for WCAG AA (4.5:1 body, 3:1
large). Yellow text is **forbidden** on white; yellow only ever appears
on dark surfaces or as a fill behind dark text.

### 2.2 Typography

Three families, all open-source and self-hosted (no Google Fonts CDN
calls — this is a Tailscale-only app, no public CDN dependencies):

| Role | Family | Weights | Notes |
|---|---|---|---|
| UI / body | **Inter** | 400, 500, 600, 700 | All product chrome, navigation, table labels |
| Display / brand | **Inter** (tight tracking) | 700, 800 | Workspace names, page titles. We do **not** use a separate display face — Inter Tight at 700 carries it. |
| Code / data | **JetBrains Mono** | 400, 500, 700 | Monaco editor, table cell values, identifiers, query IDs, durations |

**Why not Space Mono everywhere** (the design-system tool suggested it):
Space Mono's brutalist personality fights with the railroad-diagram
softness DuckDB carries. JetBrains Mono has tabular numerals out of the
box, ligatures developers expect (`=>`, `!=`, `>=`), and Cyrillic/Greek
coverage if anyone ever uses non-ASCII column names.

#### Scale (8-point rhythm)

| Token | px | Use |
|---|---|---|
| `text-2xs` | 11 | Audit timestamps, kbd hints |
| `text-xs` | 12 | Table headers, status pills, breadcrumbs |
| `text-sm` | 13 | Default body, button text, menu items |
| `text-base` | 14 | Worksheet metadata, form labels |
| `text-md` | 16 | Section headers within panels |
| `text-lg` | 18 | Page titles |
| `text-xl` | 24 | Workspace switcher header, empty-state titles |
| `text-2xl` | 32 | Login screen, onboarding only |

Body line-height 1.5. Code line-height 1.6. Tabular figures
(`font-variant-numeric: tabular-nums`) on every numeric column — rows,
durations, byte counts.

### 2.3 Spacing & Sizing

4-pt grid. Density is closer to Linear than to Snowflake.

| Token | px |
|---|---|
| `space-0.5` | 2 |
| `space-1` | 4 |
| `space-2` | 8 |
| `space-3` | 12 |
| `space-4` | 16 |
| `space-6` | 24 |
| `space-8` | 32 |
| `space-12` | 48 |

Standard surface paddings: card body `space-4`, modal body `space-6`,
panel header `space-3 space-4`. Section gap inside a panel: `space-6`.

Component sizes (touch-target floor 32×32 on desktop — DuckHaven has no
mobile/tablet target):

| | Height | Padding-x |
|---|---|---|
| Button (default) | 32 | 12 |
| Button (compact, inline in toolbars) | 28 | 8 |
| Input | 32 | 10 |
| Select / agent picker | 32 | 10 |
| Tab | 32 | 12 |
| Top bar | 48 | 16 |
| Status bar (worksheet bottom) | 28 | 12 |

### 2.4 Radius, Borders, Elevation

| Token | Value | Use |
|---|---|---|
| `radius-sm` | 4 px | Inputs, buttons, badges |
| `radius-md` | 6 px | Cards, panels, dropdowns |
| `radius-lg` | 10 px | Modals, command palette |
| `radius-full` | 9999 px | Avatars, status dots, pills |

Borders are 1px (`--border-subtle`) by default. Elevation in light mode
uses shadow + border together — drop shadows alone don't read well at low
contrast.

| Elevation | Shadow (light) | Shadow (dark) |
|---|---|---|
| `e0` panel | none | none |
| `e1` card | `0 1px 2px rgb(15 23 42 / 0.04)` | `0 1px 0 rgb(0 0 0 / 0.4)` |
| `e2` dropdown | `0 4px 12px rgb(15 23 42 / 0.08)` | `0 4px 12px rgb(0 0 0 / 0.6)` |
| `e3` modal | `0 12px 32px rgb(15 23 42 / 0.12)` | `0 16px 40px rgb(0 0 0 / 0.7)` |

Dark mode prefers **borders over shadows** for hierarchy (shadows mostly
disappear on `#0B0F19`).

### 2.5 Iconography

- **Lucide** as the single icon set (matches the rounded-rectangle, 2 px
  stroke aesthetic DuckDB uses).
- 16 px in toolbars and tree rows; 20 px in primary nav; 14 px inline with
  body text.
- **Never emoji**, anywhere — including empty states and toasts.
- Two custom SVGs we maintain in-repo:
   - **DuckHaven mark** — a duck silhouette nested inside a rounded
     junction-point shape (a nod to DuckDB's railroad-diagram motif and to
     DuckHaven being a "haven" — a port of call for a duck).
   - **Storage backend glyphs** — object storage (database), S3 (object cube),
     ADLS (cloud), each 20 px, single-color, currentColor.

### 2.6 Motion

Conservative. We're a developer tool, not a marketing page.

| Pattern | Duration | Easing |
|---|---|---|
| Dropdown / popover open | 120 ms | `ease-out` |
| Modal open / overlay fade | 180 ms | `ease-out` |
| Tab content swap | 100 ms | `ease-out` |
| Query status pill color change | 160 ms | `ease-in-out` |
| Skeleton shimmer | 1400 ms loop | `linear` |
| Worksheet split-pane drag | 0 ms (direct) | — |

`prefers-reduced-motion` zeros out everything except status-pill color
transitions (those convey meaning, not decoration).

The only "delight" animation is the **Run button's brand-yellow pulse**
when a query has just been dispatched — one 240 ms easing pulse, then the
button settles into its disabled-while-running state. No looping.

---

## 3. Information Architecture

Five top-level destinations, surfaced as a thin **left rail** (icons +
labels on hover/expand). The rail is collapsed to icons by default;
expanded state is remembered per user.

```
┌──┬──────────────────────────────────────────────────────────────────┐
│🦆│  TopBar:  workspace switcher │ ⌘K search │ user menu             │
├──┴──────────────────────────────────────────────────────────────────┤
│ 📝 │                                                                 │
│ 📚 │                       MAIN CONTENT AREA                          │
│ 💾 │                       (route-specific)                          │
│ 🛰 │                                                                 │
│ 📊 │                                                                 │
│ ⚙  │                                                                 │
└────┴─────────────────────────────────────────────────────────────────┘
```

| Rail icon | Label | Destination |
|---|---|---|
| 📝 (file-text) | Worksheets | Tabbed SQL editor — **default landing** |
| 📚 (book-open) | Catalog | Workspace → schema → table browser, read-only details view |
| 💾 (database) | Saved queries | Library of named queries, owned + shared |
| 📊 (clock-history) | History | Workspace query log; admins get an All-workspaces audit toggle |
| ⚙ (cog) | Admin | Storage backends, agents, users, maintenance (visible only to admins) |

User menu (top-right): name, "Theme: light/dark/system", "Sign out".

The yellow duck mark in the top-left **does not** navigate — clicking it
opens the workspace switcher (Snowflake-style), the most common
destination-change action.

---

## 4. Key Screens

The five screens that have to be right at M1. Each is shown as an ASCII
wireframe with the layout intent, then a short notes block. Margins,
exact widths, and component states are settled at implementation time
against the shadcn/ui primitives.

### 4.1 Worksheet (the entire product, basically)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🦆 acme-analytics ▾ │ ⌘K Search…             │ ◐ light  Marton ▾           │
├──┬──────────────────────────────────────────────────────────────────────────┤
│📝│ Tabs:  ▸ events.sql  ●  │ ▸ funnel-draft  │ ▸ +                    [⋮]   │
│📚│ ┌─ Catalog ────────────┐ ┌─ SQL ──────────────────────────── ⚙ ─┐         │
│💾│ │ 🔍 search tables…     │ │ Agent: ▾ agent-a (DuckDB 1.5.2 • 6GB) │         │
│🛰│ │ ▾ acme-analytics      │ │ Memory: 6 GB ▾   Timeout: 10 min ▾    │         │
│📊│ │   ▾ raw               │ │                                       │         │
│⚙ │ │     ▸ events       42M│ │ 1  SELECT                              │         │
│  │ │     ▸ users        1.1M│ │ 2    date_trunc('day', event_time) d, │         │
│  │ │   ▸ analytics         │ │ 3    count(*) n                        │         │
│  │ │ ▸ acme-shared (ro)    │ │ 4  FROM raw.events                     │         │
│  │ │                       │ │ 5  WHERE event_time >= '2026-05-01'    │         │
│  │ │                       │ │ 6  GROUP BY 1 ORDER BY 1;              │         │
│  │ │                       │ │                                        │         │
│  │ │                       │ │  [▶ Run  ⌘↵]   [Cancel]   [Save…]      │         │
│  │ │                       │ └────────────────────────────────────────┘         │
│  │ │                       │ ┌─ Results ─────────────────── ● done 1.4s ────┐  │
│  │ │                       │ │ Statement 1 · 30 rows · 6.2 KB                │  │
│  │ │                       │ │ ┌──────────┬─────┐                             │  │
│  │ │                       │ │ │ d        │ n   │                             │  │
│  │ │                       │ │ │ 2026-05-01│ 9123│                            │  │
│  │ │                       │ │ │ 2026-05-02│10488│                            │  │
│  │ │                       │ │ │ …                                            │  │
│  │ │                       │ │ └──────────┴─────┘   [Copy] [Download CSV]    │  │
│  │ └───────────────────────┘ └──────────────────────────────────────────────┘  │
│  │ Status bar:  ● agent-a healthy  │  workspace: acme-analytics (S3)  │ 1.4s  │
└──┴──────────────────────────────────────────────────────────────────────────┘
```

Notes:

- **Three vertical panes**, draggable dividers: catalog (240–360 px),
  editor + results (the rest, with horizontal divider between them).
- **Tab strip** holds open worksheets. Dot = unsaved. ⋮ menu per tab:
  Rename, Duplicate, Close, Close others.
- **Agent selector** lives in the editor's settings strip (gear icon
  opens a popover for memory/timeout overrides). The selector itself
  shows agent name + DuckDB version + per-query memory ceiling. Health
  dot to the left. If the agent is missing the extension required for
  this workspace's backend (D17), the option is dimmed with a "missing
  `azure` extension" tooltip — the user *can* still pick it but Run will
  fail-fast with the same message inline.
- **Run button**: the only place yellow appears in this screen. ⌘↵
  shortcut shown inside the button at 11 px.
- **Results** are tabs *under* the editor when a statement produces
  multiple result sets (rare in DuckDB but possible with multi-statement
  scripts). One result tab per statement. Pagination is cursor-based and
  fetched on-demand (D5).
- **Status bar** is the persistent reminder of *which compute against
  which storage* the user is using. This is the answer to "wait, where am
  I running this?" — a question Databricks/Snowflake users ask multiple
  times a day.

#### Engine selector (close-up)

```
┌─ Engine ─────────────────────────────────┐
│ Select an agent to run on                │
│                                          │
│ ● agent-a   DuckDB 1.5.2  6 GB           │
│   home-server · S3 ✓ · ADLS ✓ · local ✓  │
│ ● agent-b   DuckDB 1.5.2  12 GB          │
│   beefy-vm   · S3 ✓ · ADLS ✗ · local ✓   │
│ ○ agent-c   DuckDB 1.4.3  6 GB           │
│   legacy    · S3 ✓ · ADLS ✓ · local ✓    │
│                                          │
│ ⓘ This workspace is on ADLS — agent-b    │
│   cannot execute writes here.            │
└──────────────────────────────────────────┘
```

The capability advertisement (D17) is rendered as compact backend tags
under each agent — `✓` available, `✗` missing extension. The
incompatible row is selectable (for SELECT-against-other-backend cases
the architecture allows) but a warning banner appears in the editor if
the SQL writes.

#### Query lifecycle pill

A single pill in the result header walks through `● queued` (slate) →
`● running 0:04` (orange, counter ticking) → `● done 1.4s` (green) /
`● failed` (red) / `● cancelled` (slate). One affordance, four colors,
matches the `--status-*` tokens above.

### 4.2 Catalog browser (full-screen detail)

The left-pane tree is a quick navigator; the full Catalog destination is
the detail view a user opens when they want to see a table's schema,
sample rows, or metadata.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Catalog  /  acme-analytics  /  raw  /  events                       [≡] [✎] │
├─────────────────────────────────────────────────────────────────────────────┤
│ Backend: S3 (s3://acme-data/duckhaven/acme-analytics/)                       │
│ Format:  Iceberg · Catalog Commits ON · 42.1M rows · 312 MB · 14 commits       │
│ Owner:   Marton (you) · Last write: 2026-05-15 14:03 by jess (agent-b)       │
│                                                                              │
│ ┌─ Schema ────────────────────────────┐ ┌─ Sample (LIMIT 50) ────────────┐  │
│ │ # │ Column        │ Type      │NULL? │ │ event_id │ user_id │ event… │  │
│ │ 1 │ event_id      │ UUID      │  N    │ │ 9c2…     │ 4f1…    │ click   │  │
│ │ 2 │ user_id       │ UUID      │  Y    │ │ 9c3…     │ —       │ view    │  │
│ │ 3 │ event_type    │ VARCHAR   │  N    │ │ …                                │
│ │ 4 │ event_time    │ TIMESTAMP │  N    │ │                                  │
│ │ 5 │ properties    │ JSON      │  Y    │ │                                  │
│ └──────────────────────────────────────┘ └──────────────────────────────────┘
│                                                                              │
│ [Query this table]   [Show recent queries]   [Show table history]            │
└─────────────────────────────────────────────────────────────────────────────┘
```

Notes:

- **Breadcrumb is clickable** all the way up. ⌘-click to open in a new tab.
- **No edit-schema UI in MVP** — DuckDB's UC extension can't ALTER (D8).
  The pencil icon top-right opens "Rename / Drop", that's it.
- **Sample rows** uses the worksheet's last-selected agent; if none, the
  first healthy agent compatible with this workspace's backend.

### 4.3 Workspace switcher (modal, ⌘K from anywhere)

Opens on yellow-duck click or `⌘.` / `Ctrl+.`. Same surface as the global
command palette but pre-filtered to workspaces. Linear-style:

```
┌─ Switch workspace ───────────────────────────────┐
│ 🔍 Type to filter…                                │
│                                                  │
│ ▸ acme-analytics    S3       owner       ↵       │
│ ▸ acme-research     ADLS     writer              │
│ ▸ public            object   reader              │
│ ▸ home-lab          object   owner               │
│ ─────────────────                                │
│ + Create workspace…                              │
└──────────────────────────────────────────────────┘
```

The role chip uses neutral colors only (no yellow/orange/green) — it's
metadata, not a status. The backend kind is the second column because
"where am I writing?" is a more common question than "what's my role?".

### 4.4 Admin — Agents

The agent registry is the screen operators will spend the most time on.
It's a list with one drawer-style detail panel.

```
┌─ Admin / Agents ────────────────────────────────────── [Generate bootstrap] ┐
│                                                                              │
│ Status  Name         DuckDB  Host           Extensions        Mem  Last ping │
│ ●green  agent-a      1.5.2   homeserver-01  iceberg,httpfs,az 6G  2s ago    │
│ ●green  agent-b      1.5.2   beefy-vm       iceberg,httpfs    12G 1s ago    │
│ ●red    agent-c      1.4.3   —              iceberg,httpfs,az 6G  4m ago    │
│ ●amber  agent-d      1.5.2   sandbox        iceberg                6G 12s ago   │
│                                                                              │
│ Drawer (agent-b selected) ────────────────────────────────────────────────── │
│ Capabilities · 12 GB cap · 4 cores · Tailscale 100.74.x.x                    │
│ Workspaces served (last 24h): acme-analytics (124 q), public (8 q)           │
│ Recent errors: 0                                                             │
│ [Revoke credential]   [View audit for this agent]                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

Notes:

- **Bootstrap token modal** shows the one-time token with a copy-to-
  clipboard button and a clear "this is the only time we'll show it"
  warning. The token field is `JetBrains Mono`, 14 px, no obscuring — at
  ≤10 users on Tailscale the threat model (architecture §9) doesn't
  warrant hiding it.
- **Amber status** = connected but missing an extension that some
  workspace it could serve needs. Hovering the dot lists which.

### 4.5 Admin — Storage backends

A flat table; backends are the second-most-edited admin object.

```
┌─ Admin / Storage backends ──────────────────────────── [+ Register backend] ┐
│ Kind     Name           Root URI                            UC cred   In use│
│ S3       acme-prod      s3://acme-data/duckhaven/           ✓ valid   3 ws  │
│ ADLS     research       abfss://research@acme/duckhaven/    ✓ valid   1 ws  │
│ Object   home-lab       home-lab/                           —         1 ws  │
│ Object   box                                                —         2 ws  │
└──────────────────────────────────────────────────────────────────────────────┘
```

Notes:

- "In use" is non-zero → delete is disabled with a tooltip listing the
  workspaces.
- "Register backend" is a 3-step wizard: kind → URI → UC credential
  binding + health check. The health check is loud — it returns the
  short-lived credential vending result and one test `LIST` against the
  root, with the agent that ran the check named in the result.

---

## 5. Component Inventory

The components below have first-class shadcn/ui implementations and small
DuckHaven-specific wrappers. Built on Radix primitives.

| Component | shadcn primitive | DuckHaven wrapper notes |
|---|---|---|
| Button | `button` | Adds `intent="run"` (yellow) + loading-spinner-while-async |
| Tabs (worksheet) | `tabs` | Closable, dirty-dot, drag-to-reorder |
| Tree (catalog) | custom (Radix `collapsible` + virtualized) | Lazy-load children per click; row count badges |
| Command palette | `command` | Static groups: Workspaces, Saved queries, Tables, Actions |
| Combobox (agent picker) | `command` + `popover` | Per-row capability tags |
| Data table (results) | TanStack Table + custom virtualization | Tabular nums; cell click = "copy"; right-click → "Filter by", "Group by" inserts SQL |
| Toast | `sonner` | Stack max 3, 4 s default, errors require dismiss |
| Modal | `dialog` | `size="md|lg"`; default focus on first input |
| Drawer (admin detail) | `sheet` | Right-side, 480 px, persistent until dismissed |
| Status pill | custom | Four states, see §4.1 |
| Skeleton | `skeleton` | One shimmer animation, gated by `prefers-reduced-motion` |
| Avatar | `avatar` | Initials only (no Gravatar — Tailscale-only, no public CDN) |

### Monaco configuration

- Theme: a custom **DuckHaven Dark** (used regardless of UI theme — code
  is always dark, mirroring DuckDB's terminal aesthetic).
- Token colors map to DuckDB's syntax-highlight conventions: keywords =
  `--brand-yellow`, strings = `--brand-orange`, functions = `#7DD3FC`,
  identifiers = slate-200, comments = slate-500.
- Auto-format on save uses `sql-formatter` with `language: "duckdb"`.
- A 12 px gutter shows query line/col positions from agent errors (S1
  pass criterion bullet 3 of the architecture).

---

## 6. Interaction States (the "boring but critical" section)

These are the states the eight prior sections take for granted. Listing
them explicitly so the implementer doesn't have to guess.

### 6.1 Loading

- **Initial app load**: full-screen yellow duck mark, 1.2× scaled, with
  a 2 px ring that fills in 600 ms. Replaces nothing — appears before
  React hydration. Reduced-motion → static logo + "Loading…" label.
- **Worksheet tab opening**: skeleton-shimmer the editor pane only;
  catalog stays interactive.
- **Query running**: editor freezes inputs that mutate SQL (typing is
  still allowed, but Run is disabled and Cancel takes its place). Result
  pane shows skeleton table rows.
- **Result pagination**: a thin yellow progress bar at the top of the
  result table, no full skeleton.

### 6.2 Empty

Every list and grid has a real empty state. Never a blank pane.

| Surface | Empty copy | Action |
|---|---|---|
| No worksheets yet | "Open a worksheet to start querying." | `[New worksheet]` |
| No tables in schema | "No tables in `raw` yet." | `[Create table…]` if writer+ |
| No agents connected | "No agents are connected. Generate a bootstrap token to register one." | `[Generate bootstrap]` |
| No audit rows | "No queries yet." | — (read-only) |
| No saved queries | "Save a worksheet to keep it here." | `[Save current worksheet]` |
| No workspaces | "You're not a member of any workspace yet. Ask an admin to add you." | — |

Empty-state typography: `text-md` slate-600 title + `text-sm`
slate-500 secondary line. No illustrations. The action button is the
only color. Spacing: empty state vertically centered in pane, 320 px max
content width.

### 6.3 Error

Errors are shown where they happen, never as global toasts unless the
user couldn't have caused them (network/loss-of-connection).

- **SQL error**: inline below the editor, with the agent's reported line
  and column highlighting in the gutter. Copyable. "Open in worksheet"
  if surfaced from a saved query.
- **Agent disconnected mid-query**: result pane shows "Agent agent-b
  disconnected" + suggested action ("Run on a different agent" with a
  pre-filled engine picker).
- **Permission denied**: modal with the exact workspace + role mismatch
  ("You're a reader on acme-research; this action needs writer.").
- **Validation (form)**: inline below the field, red text, 12 px,
  `aria-live=polite`, focus moves to the first invalid field on submit.

Never use red for non-error semantic content (e.g. revoke buttons are
neutral with a confirmation dialog — they're not errors, they're
destructive actions).

### 6.4 Focus and keyboard

- Every interactive element has a 2 px `--brand-slate-blue` focus ring
  with a 2 px offset. The ring never collides with adjacent elements.
- **Global shortcuts**:
   - `⌘K` / `Ctrl+K`: command palette
   - `⌘.` / `Ctrl+.`: workspace switcher
   - `⌘↵` / `Ctrl+↵`: Run query
   - `⌘S` / `Ctrl+S`: Save worksheet (no autosave — the editor isn't a
     notebook; we don't want surprise persistence of half-typed SQL)
   - `⌘W` / `Ctrl+W`: Close current tab (with unsaved-changes guard)
   - `Esc`: dismiss modal / cancel running query (with confirm if >1 s)
- Tab order matches visual order. Tree rows use ↑↓ for navigation, → / ←
  for expand/collapse, `Enter` for "open in active worksheet", `⌘Enter`
  for "open in new tab".

---

## 7. Light Mode, Dark Mode

Both modes are first-class. Theme switching is per-user, persisted
server-side (so it follows the user across devices on the same
Tailnet), with a `system` option that follows the OS.

Rules of thumb when implementing both:

- **Test in dark first** when implementing data-dense panels. Light is
  the easier mode visually; dark is where contrast bugs hide.
- **Borders, not shadows**, for hierarchy in dark mode.
- **Yellow stays #FFF100** in both modes — its luminance puts it within
  AA contrast on both `#0B0F19` and `#FFFFFF`, but only for non-text use.
  Yellow is never body text.
- **Code blocks (Monaco) use the same dark theme** in both modes. This
  is deliberate — it tracks DuckDB's own docs convention and reduces
  retina flash for users running dark terminals next to a light-themed
  app.

### Dark-mode swatch (the one swap that's easy to get wrong)

In light mode, `text-secondary` is slate-600 on slate-50 (legible
metadata). In dark mode, the equivalent is **slate-400 on gray-900**,
**not** slate-600 on gray-900 (which fails AA at 13 px). Codify this in
the theme tokens — never inline color literals.

---

## 8. Accessibility Contract

- WCAG 2.1 **AA** is the floor. AAA where it costs us nothing.
- All interactive elements: keyboard reachable, `aria-label` if icon-
  only, `aria-pressed` / `aria-expanded` / `aria-selected` for stateful
  ones, `aria-busy` while loading.
- Status pills convey status by **color and label** (never color alone).
  "● running 0:04" not just "●".
- Toasts use `aria-live="polite"` for info/success, `role="alert"` for
  errors.
- `prefers-reduced-motion` honored everywhere except the four-state
  status pill (motion conveys meaning, no good alternative).
- Monaco's built-in screen-reader mode is exposed via the editor's
  context menu and `Alt+F1`.
- High-contrast mode (Windows) inherits from CSS system colors — we
  don't override `forced-colors: active`.

A standing rule: **a feature is not done until both keyboard-only and
screen-reader walk-throughs of the primary path pass**. M2 exit
criterion adds this.

---

## 9. Frontend Tech Mapping

To stay aligned with `ARCHITECTURE.md` §8:

| Concern | Choice |
|---|---|
| Framework | React 19 + TypeScript + Vite |
| Routing | TanStack Router (typed routes, search-param state) |
| Data | TanStack Query — query + mutation cache; no Redux |
| UI primitives | shadcn/ui (Radix under the hood) + Tailwind v4 |
| Editor | Monaco, lazy-loaded on first worksheet open |
| Icons | Lucide-react |
| Forms | React Hook Form + Zod (admin wizards) |
| Toasts | Sonner |
| Tables | TanStack Table + custom virtual scroller |
| Theme tokens | CSS custom properties + a Tailwind preset that consumes them |
| Fonts | Self-hosted Inter + JetBrains Mono via `@fontsource` |
| Tests | Vitest + Testing Library + Playwright (smoke + a11y) |

The design tokens live in `src/styles/tokens.css` as CSS variables; the
Tailwind preset only references those variables. This is what keeps the
DuckDB brand colors in one editable place when the upstream brand
evolves.

---

## 10. Open UI Questions (resolve during M1)

1. **Q-UI-1.** Workspace switcher: surface storage backend kind as an
   icon (current sketch) or as a colored capsule? **Default: icon.**
2. **Q-UI-2.** Show per-statement results stacked (Databricks-style) or
   tabbed (Snowflake-style) when a script has multiple statements?
   **Default: tabbed.** Stacking gets noisy past two statements.
3. **Q-UI-3.** Should the agent picker remember per-worksheet *or* per-
   workspace? **Default: per-worksheet** (matches "user picks engine
   per query" — D15 of architecture).
4. **Q-UI-4.** Auto-suggest from catalog inside Monaco — pull schemas
   eagerly on workspace open or lazily on `.` keystroke? **Default:
   lazy**, with a 250 ms pre-fetch on schema-tree hover.
5. **Q-UI-5.** Result-grid cell click — copy-to-clipboard (current) or
   open a detail popover (better for long JSON cells)? **Default: copy
   for ≤120 chars, popover otherwise.**
6. **Q-UI-6.** Theme default for a brand-new install — light, dark, or
   system? **Default: system**, with the first-run modal offering an
   explicit choice.

---

## 11. Out of Scope for the Design RFC

The same way `ARCHITECTURE.md §2` declares non-goals, this doc declares
**design** non-goals so they don't sneak in during M1:

- No AI/Copilot/Assistant panel.
- No data-quality dashboards. (Lineage shipped after M1, as a table-detail tab —
  see [Lineage](../concepts/lineage.md).)
- No marketing pages — `/` redirects to `/worksheets` after login.
- No notification center. Toasts only.
- No customizable themes beyond light/dark. Brand stays brand.
- No public-internet-facing pages (no signup, no marketing footer).
- No mobile breakpoint. Floor is 1280 × 800. A mobile UI is a v2
  decision driven by the on-call use case, not a launch requirement.

---

*End of design document.*
