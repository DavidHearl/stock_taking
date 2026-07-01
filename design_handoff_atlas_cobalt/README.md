# Handoff: Atlas — "Ink" Cobalt Branding & UI

## Overview
Atlas is Sliderobes' internal operations platform (ordering, production, accounting, management — not customer-facing). This package documents the **approved "Ink" cobalt direction**: logo usage, the full colour system, typography, and a component library, applied to real screens (dashboard, sales list, sidebar nav).

## About the Design Files
The files in this bundle are **design references authored in HTML** (Design Components) — prototypes that show the intended look and behaviour. They are **not production code to copy directly**. The task is to **recreate these designs in the target codebase using its established patterns and libraries** (React, Vue, etc.). If no front-end environment exists yet, choose the most appropriate framework and implement there. All values below are exact — build to them.

> The `.dc.html` files are a lightweight component runtime for previewing only. Open `Atlas Cobalt Reference.dc.html` in a browser to see every screen locked to the cobalt tokens. `Atlas UI Kit.dc.html` is the interactive component gallery (dropdowns, tabs, chart switcher) and is the best single reference for component styling.

## Fidelity
**High-fidelity.** Final colours, typography, spacing, radii and interactions are specified. Recreate the UI pixel-accurately using the codebase's component library.

## Screens / Views

### 1. Sidebar Navigation (`NavMenu.dc.html`)
- **Purpose:** Primary app navigation.
- **Layout:** Fixed vertical rail, **width 256px**, white background (`#FFFFFF`), `border-right: 1px solid #EEEFF2`, padding `18px 14px 26px`. Flex column.
- **Brand header:** Atlas mark (26px) + "ATLAS" wordmark (Chakra Petch 600, 17px, letter-spacing .15em, `#141821`) with "BY SLIDEROBES" beneath (Chakra Petch 500, 7.5px, letter-spacing .3em, accent colour). Bottom border `1px solid #F1F2F5`, padding-bottom 20px.
- **Sections:** Uppercase labels — General, Projects, Products & Stock, Purchase, Accounting. Label style: 10.5px / 700 / letter-spacing .13em / `#9AA2B0`, padding `0 12px 8px`. Each section separated by `border-top: 1px solid #F3F4F6`, padding-top 14px.
- **Item (default):** flex row, gap 13px, padding `9px 12px`, border-radius 9px. Icon 21px `#6B7280`; label Manrope 14.5px / 500 / `#3D4451`.
- **Item (active — e.g. "Sales"):** background `rgba(47,107,246,0.10)`, `box-shadow: inset 2.5px 0 0 #2F6BF6` (left rail), icon + label in accent `#2F6BF6`, label weight 700.
- **Icons:** Material Symbols Outlined (opsz 24, wght 400, FILL 0). Map: Dashboard=`speed`, Calendar=`calendar_month`, Reports=`bar_chart`, Active=`space_dashboard`, Sales=`sell`, Customers=`group`, Map=`view_column`, Gantt=`view_timeline`, Products=`inventory_2`, Stock Take=`fact_check`, Shortages=`warning`, Validation=`schedule`, Purchase Orders=`shopping_cart`, Suppliers=`local_shipping`, Invoices=`description`, Payments=`credit_card`, Accounts Payable=`account_balance_wallet`, Xero=`link`, Timesheets=`schedule`.

### 2. Sales List (`SalesTable.dc.html`)
- **Purpose:** Browse/act on open sales.
- **Toolbar:** logo lockup (left) | segmented tabs Open/Remedial/Complete | search field (flex-grow) | action cluster (bell w/ badge, history icon, sync pill, location pill, kebab).
  - **Active tab:** accent background `#2F6BF6`, white text, `box-shadow 0 4px 12px rgba(47,107,246,.35)`, radius 999px. Inactive: `#5A616E` on transparent, inside a `#E3E5E9` pill track.
  - **Search:** 44px tall, white, `border 1px solid #ECEEF1`, radius 999px, magnifier icon `#9AA2B0`, placeholder `#9AA2B0` 14px.
  - **Sync pill:** accent fill, white, "⟳ 19". **Location pill:** `rgba(accent,.1)` bg, accent text, pin glyph, "Belfast". **Bell badge:** `#F5A524`.
- **Table:** white card, radius 14px, `border 1px solid #ECEEF1`.
  - **Header row:** background `#E9EBEE`, 11.5px / 700 / letter-spacing .06em / `#4A515E`, padding `15px 22px`. Columns: Sale Number | Customer | Assigned To | Value | Fit Date | Location | Status | Paid | (kebab). Grid: `156px 1.25fr 1.25fr 0.8fr 1fr 0.95fr 1fr 62px 28px`, gap 14px.
  - **Group row (PFP):** `#F7F8FA`, chevron `#9AA2B0`, "PFP" chip (mono 11px / 700, `#B07D0A` on `#FBF0CE`, radius 6px), bold text, italic hint `#9AA2B0`.
  - **Data row:** padding `16px 22px`, `border-bottom 1px solid #ECEEF1`, 14px. Sale Number + Customer are **links in accent `#2F6BF6`, weight 600**. Assigned/Fit/Location `#5A616E`. Value: `–` → `#B7BDC7`; amount → `#1F2430` weight 600, right-aligned.
  - **Paid:** 22px red circle `#E5484D` with white ✕.
- **Status pills** (radius 999px, 12px / 700, padding `5px 15px`):
  - SHORT → text `#E5484D` on `#FDEAEA`
  - REQUIRED → text `#B07D0A` on `#FBF0CE`
  - ORDERED → text `#2F6BF6` on `rgba(47,107,246,.12)` (brand/info)
  - (Kit adds DELIVERED → `#12924A` on `rgba(34,197,94,.14)`)

### 3. Operations Dashboard (`AtlasDash.dc.html`) — light + dark
- **Layout:** sidebar (228px) + main (topbar 58px + content). Content padding 22px, gap 18px.
- **Sidebar:** logo lockup, "OPERATIONS" group label, nav items (active = `rgba(accent,.1)` + `inset 2px 0 0 accent`), user footer (avatar w/ accent gradient + name/role).
- **Topbar:** breadcrumb ("Operations / Overview"), search (260px), 2 icon buttons, avatar.
- **Page header:** H1 "Operations Overview" (Chakra Petch 22px / 600), sub `#dim`; right: "Last 30 days" pill + primary button "＋ New Order" (accent gradient, white, `0 6px 16px rgba(accent,.35)`).
- **KPI row:** 4 cards (`repeat(4,1fr)`, gap 14). Card: panel bg, `1px solid line`, radius 14px. Label 12px/600 dim; value Chakra Petch 26px/600; delta chip (green `#22C55E` up / red `#F04452` down, soft bg); foot 11.5px faint.
- **Mid row:** `1.85fr 1fr`. Left = bar chart ("Order intake", 12 CSS bars, last bar accent gradient + glow, others `rgba(accent,.4)`). Right = donut (`conic-gradient(accent 0 78%, #38BDF8 78% 91%, #F5A524 91% 97%, line 97% 100%)`) + legend.
- **Table:** "Recent orders" with same status pills as Sales List.

### 4. Component Library (`Atlas UI Kit.dc.html`)
Interactive gallery — the definitive component spec. **Tweakable props:** `accent` (color), `mode` (light/dark), `radius` (2–16px, default 9), `density` (comfortable/compact). Contains:
- **Buttons:** Primary (accent, white, `0 4px 12px rgba(accent,.3)`), Secondary (white + `1px solid line`), Ghost (accent text), Danger (`rgba(#F04452,.12)` bg, `#F04452` text), sizes sm/md/lg, gradient icon button, disabled (`line` bg / `faint` text). Radius = `radius` token; padding `10px 16px` (comfortable) / `8px 13px` (compact).
- **Text fields:** label 12.5px/700 dim; input padding `11px 15px`, `1px solid line`, radius = token; error state = `1.5px solid #F04452` + `0 0 0 3px rgba(#F04452,.12)` + red label + helper text.
- **Dropdown/Select:** trigger = input style w/ caret (▾/▴); menu = panel, `1px solid line`, radius token, `0 16px 40px rgba(0,0,0,.18)`, 4px padding; selected row tinted `rgba(accent,.09)` + accent ✓.
- **Selection:** checkbox (20px, radius min(token,7), accent fill + white ✓ when on), radio (20px circle, 2px border → accent + 10px accent dot), switch (42×24 track, accent when on, 18px white knob).
- **Tabs/segments:** pill track `panel2`, active = accent fill + white + shadow.
- **Status & badges:** pills (above), count badge (accent fill, white, 800), soft badge (`rgba(accent,.12)` + accent), live dot (`#22C55E` + halo), avatar (accent gradient).
- **Charts:** switchable Bar / Line / Area / Donut. Line = `polyline` stroke accent 2.5px; Area = same + fill `rgba(accent,.16)`; Donut = conic-gradient (accent / `#38BDF8` / `#F5A524` / line).
- **Stat card:** as dashboard KPI.

## Interactions & Behavior
- **Dropdown:** click trigger toggles menu; click option sets value + closes. (Add outside-click-to-close in production.)
- **Segmented tabs / chart switcher:** click selects; active restyles immediately.
- **Checkbox/radio/switch:** click toggles state.
- **Transitions:** switch knob `all .15s`. Buttons/links: add subtle hover (e.g. −6% lightness or accent→darker). Nav item hover: `rgba(accent,.06)`.
- **Row hover (tables):** tint row `panel2` / `rgba(accent,.04)`.

## State Management
- Nav: `activeRoute`.
- Sales list: `activeTab` (Open/Remedial/Complete), `query`, `location`, sort/expand for PFP group.
- Dropdowns: `open`, `selected`.
- Toggles/radios/checks: booleans / enum.
- Charts: `chartType`.
- Theme: `mode` (light/dark), `accent`, `radius`, `density` — implement as CSS variables / theme context so the whole app re-themes (mirrors the kit's tweak behaviour).

## Data legibility (priority — apply to every tabular/data screen)
The single most important goal is **reading data fast**. The earlier cobalt applied to the live UI came out washed-out; these rules fix it (see `Atlas Data Views.dc.html`):
- **Text contrast (light):** primary `#0F172A`, secondary `#334155`, muted `#5B6472`, faint `#8A93A3`. Never put labels lighter than `#5B6472` on white. **Dark:** primary `#F4F7FC`, secondary `#C4CDDC`, muted `#93A0B5`.
- **Cards must separate from the canvas:** panel `#FFFFFF` on canvas `#EEF1F5`, `1px solid #DCE1E9` border **plus** shadow `0 1px 2px rgba(15,23,42,.06), 0 6px 20px rgba(15,23,42,.07)`. Don't rely on colour alone.
- **Links / report actions:** use the darker cobalt `#1D4ED8` on light (better contrast than `#2F6BF6`), `#77A5FF` on dark.
- **Tables:** header band `#EEF2F7` with `700/uppercase #5B6472` labels and a hard bottom rule `#C3CAD6`; **zebra rows** (`#F7F9FB`) for scanning; row separators `#DCE1E9`; row padding ~13px.
- **Numbers:** `font-variant-numeric: tabular-nums`, right-align currency, centre counts; monospace-ish for barcodes/material codes; bold totals.
- **Controls in cells:** qty steppers bordered (`#C3CAD6`); edge checkboxes = accent fill + white ✓ when on, `1.5px #C3CAD6` outline when off (20px, radius 6).
- **Wide tables scroll horizontally** inside the card (min-width ~1240px) rather than collapsing columns.
- **Charts:** show y-axis tick labels + light gridlines (`#DCE1E9`) + a solid baseline (`#C3CAD6`); keep per-metric series colours (cyan/green/purple/amber) — they aid recognition and are fine for legibility.

## Design Tokens

**Brand / accent (Ink cobalt)**
- Accent (interactive, links, active): `#2F6BF6`
- Gradient (logo, buttons, avatars, badges): `#2563EB → #38BDF8` (135°)
- Accent tint (active bg / soft): `rgba(47,107,246,0.10)`; pill/info: `rgba(47,107,246,0.12–0.13)`

**Neutrals — Light**
- Canvas `#EEF0F3` (app) / `#F5F6F9` (dash) · Panel `#FFFFFF` · Sunken `#F2F4F7` · Line `#E5E8EE`/`#ECEEF1` · Text `#141821`/`#1F2430` · Dim `#5A616E` · Faint `#9AA2B0`

**Neutrals — Dark** — neutral greys with NO blue cast; surfaces elevated above the canvas with a visible border; soft off-white text (never pure `#FFF`). Keep colour LOW in dark: one accent, muted status/semantic colours, no large saturated fills.
- Canvas `#141518` · Card/Panel `#1F2024` · Sunken/Header `#2A2B30` · Border `#34363B` · Text `#DEE1E5` (soft) · Secondary `#B4B8BF` · Dim `#9DA1A9` · Faint `#74787F`
- Data Views / Order Detail dark: Canvas `#121316` · Panel `#1D1E22` · Header `#26272C` · Zebra `#212226` · Border `#313236` / hard `#42444A` · Text `#DEE1E5` · Secondary `#B4B8BF` · Dim `#8F949C` · Faint `#6C7079`
- Every card: `box-shadow: 0 0 0 1px rgba(255,255,255,.03), 0 16px 40px rgba(0,0,0,.5)` (the 1px ring gives edge definition on dark).

**Semantic**
- Success `#22C55E` (light text `#12924A`) · Warning `#F5A524` (light text `#B07D0A`) · Danger `#F04452`/`#E5484D`
- Status: SHORT=danger, REQUIRED=warning, ORDERED=accent, DELIVERED=success (all on ~12–16% tinted bg)

**Radius:** default 9px (buttons/inputs/tabs); cards +5 (14px); pills 999px; kit exposes 2–16px.
**Shadows:** card `0 1px 2px rgba(16,20,30,.04), 0 10px 30px rgba(16,20,30,.05)` (light); menu `0 16px 40px rgba(0,0,0,.18)`; accent button `0 4–6px 12–16px rgba(47,107,246,.3–.35)`.
**Spacing:** 4 · 8 · 12 · 14 · 18 · 22px rhythm.

**Typography**
- Display / wordmark / headings & numerics: **Chakra Petch** (500/600/700) — geometric, wide, letterspaced for the wordmark (.15–.17em).
- UI / body: **Manrope** (400–800).
- Mono (IDs, hex): `ui-monospace, Menlo, monospace`.
- Type scale (px): H1 22 (Chakra 600) · section label 11–13 / 700 uppercase · body 14–14.5 · small 12–12.5 · micro 10.5–11. Stat value 26–30 (Chakra 600).

## Assets
- **Logo mark:** geometric "A" (triangle) with a hidden "L". SVG polygons on a 0 0 100 100 viewBox:
  - Accent A-mass (gradient fill): `49,4 60,24 35,63 58,63 66,76 10,76`
  - Right leg (solid — text colour): `52,30 60,30 92,92 84,92`
  - Detached foot (solid): `4,92 22,92 31,80 13,80`
  - Gradient runs bottom-left→top-right (`#2563EB → #38BDF8`). "Solid" = white on dark, `#141821` on light. See `AtlasMark.dc.html`.
- **Fonts:** Chakra Petch, Manrope, Material Symbols Outlined — all Google Fonts (self-host in production).
- **Icons:** Material Symbols Outlined (or swap to the codebase's icon set using the name map above).
- No raster image assets; provide real product imagery where placeholders appear.

## Files
- `Atlas Cobalt Reference.dc.html` — all approved screens locked to cobalt (open this first).
- `Atlas Data Views.dc.html` — **data-first legibility pass** (high-contrast KPI cards, dense Board Details table, legible chart). Use these contrast rules for ALL tabular/data screens — see "Data legibility" below.
- `Atlas Order Detail.dc.html` — the sale/order detail screen (customer header + status/value/next-step, tabs, Sale Details, Documents + job-photo dropzone, dense Board Details table, right rail: stage, Financial Summary with cost-unit grid, Quick Actions + job status). Themed light/dark.
- `Atlas Workflow.dc.html` — the Workflow Management modal (scrollable stage list with team pills, durations, checklist/people counts, the "Current" stage highlighted + expandable to tasks). Themed light/dark.
- `Atlas UI Kit.dc.html` — interactive, tweakable component library (definitive component spec).
- `AtlasDash.dc.html` — dashboard (props: `mode`, `accent`, `gradFrom`, `gradTo`).
- `SalesTable.dc.html` — sales list (props: `accent`, `gradFrom`, `gradTo`).
- `NavMenu.dc.html` — sidebar nav (props: `accent`, `gradFrom`, `gradTo`).
- `AtlasMark.dc.html` — logo mark (props: `gradFrom`, `gradTo`, `solid`, `size`).
- `support.js` — preview runtime only (do not port).

For the cobalt direction pass every component: `accent="#2F6BF6"`, `gradFrom="#2563EB"`, `gradTo="#38BDF8"`.
