# CSS Design System — Atlas Cobalt

The standing design rules for Sliderobes Atlas, based on the "Atlas Cobalt" brand direction (see `design_handoff_atlas_cobalt/README.md` for the original handoff this was adapted from). **The single source of truth is the token block in [`static/css/styles.css`](../static/css/styles.css) (`:root`), overridden for light mode in [`static/css/light-mode.css`](../static/css/light-mode.css).** There is also a live, rendered reference page in the app at **Admin → Design Rules** ([`admin_design_rules.html`](../stock_take/templates/stock_take/admin_design_rules.html)) — use it to eyeball swatches/spacing.

The one rule that matters most: **never hardcode a colour, radius, shadow, or spacing value in a feature CSS file. Always reference a token below.** That is what makes dark/light mode work — light mode only overrides the variables; every component inherits automatically.

---

## How theming works

- **Dark is the default** (defined in `:root` in `styles.css`).
- **Light mode** is opt-in via `body.light-mode`, which only re-declares the variables. No component rule is duplicated for light mode — if you reference tokens, your component themes for free.
- Both `styles.css` and `light-mode.css` are linked globally in [`base.html`](../templates/base.html). Page CSS extends, never replaces, them.
- If a component looks wrong in light mode, the fix is almost always "you hardcoded a colour" — replace it with a token.

---

## Colour tokens

### Surfaces

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg-page` | `#141518` | `#eef1f5` | Outermost page background (canvas) |
| `--bg-primary` | `#191a1e` | `#f5f6f9` | Primary surface (mid step above canvas) |
| `--bg-secondary` | `#1f2024` | `#ffffff` | Cards, panels (dark: clearly lighter than canvas) |
| `--bg-tertiary` | `#2a2b30` | `#f2f4f7` | Raised/nested surface (sunken), table headers |
| `--bg-nav` | `#1f2024` | `#ffffff` | Sidebar / topbar |
| `--bg-input` | `#2a2b30` | `#ffffff` | Form inputs |
| `--bg-hover` | `rgba(47,107,246,.06)` | `rgba(47,107,246,.08)` | Hover states (accent-tinted) |
| `--bg-card` | dark gradient | white gradient | Card backgrounds |
| `--overlay-bg` | `rgba(0,0,0,.60)` | `rgba(0,0,0,.40)` | Modal/scrim overlay |

### Text

| Token | Dark | Light | Use |
|---|---|---|---|
| `--text-primary` | `#dee1e5` | `#0f172a` | Body / headings (high contrast, soft off-white) |
| `--text-secondary` | `#b4b8bf` | `#334155` | Supporting text |
| `--text-muted` | `#9da1a9` | `#5b6472` | Labels, hints, captions — never lighter than this on white |
| `--text-on-color` | `#ffffff` | `#ffffff` | Text on a coloured fill (buttons, badges) |
| `--link-color` | `#77a5ff` | `#1d4ed8` | Text hyperlinks / report actions (higher contrast than the `#2f6bf6` accent) |

### Semantic colours

Each has three variants: base (`-color`), `-hover` (darker, for hover), and `-subtle` (low-opacity tint for backgrounds/badges).

| Family | Base (dark) | Base (light) | Meaning |
|---|---|---|---|
| `--primary-*` | `#2f6bf6` | `#2f6bf6` | Primary actions, links, active state (Atlas Cobalt accent — same hue both modes) |
| `--success-*` | `#22c55e` | `#12924a` | Success, received, paid, positive |
| `--warning-*` | `#f5a524` | `#b07d0a` | Warnings, pending, attention |
| `--danger-*` | `#f04452` | `#e5484d` | Errors, delete, overdue, negative |
| `--info-*` | `#38bdf8` | `#2e9dba` | Informational, neutral highlight |
| `--purple-*` | `#8b5cf6` | `#7c3aed` | Secondary accent / categorisation |

Example: a "pending" badge uses `background: var(--warning-subtle); color: var(--warning-color);`. A primary button uses `background: var(--primary-color);` and `:hover { background: var(--primary-hover); }`.

> **Note:** the primary brand colour token is `--primary-color`, *not* `--accent-color`. `--accent-glow` exists (a soft glow tint) but there is no `--accent-color` token — don't reference it. The Atlas Cobalt gradient (`#2563EB → #38BDF8`, 135°) is reserved for decorative/hero surfaces (avatars, badges, logo) — it isn't a token, reference it directly as a `linear-gradient(...)` where needed.

---

## Layout, spacing, shape

### Spacing rhythm — use these, don't invent pixel values

| Token | Value |
|---|---|
| `--sp-1` | 4px |
| `--sp-2` | 8px |
| `--sp-3` | 12px |
| `--sp-4` | 14px |
| `--sp-5` | 18px |
| `--sp-6` | 22px |

### Radius

| Token | Value | Use |
|---|---|---|
| `--radius-sm` | 9px | Buttons, inputs, tabs, small controls, swatches |
| `--radius-md` | 14px | Cards, panels |
| `--radius-lg` | 18px | Large containers, modals |
| `--radius-pill` | = `--radius-sm` (8px) | Status badges & pills — squared off to read as rectangular buttons (no longer fully rounded) |
| `--radius-round` | 999px | Genuinely round elements only: toggle-switch tracks and circular dots/indicators. Use this, not `--radius-pill`, when the shape must stay a circle or stadium. |

### Shadows

`--shadow-sm`, `--shadow-md`, `--shadow-lg` — increasing elevation. Light mode uses softer/lighter shadows automatically.

### Borders & motion

- `--border-color` — the one border colour for dividers, card edges, input borders. (`#34363b` dark / `#dce1e9` light — kept deliberately visible so cards/tables separate from the canvas.)
- `--transition` — `all 0.2s ease`. Use it for hover/focus transitions instead of writing your own timing.

### Typography

- `--font-display` — `'Chakra Petch'` — headings (`h1`–`h6`), `.page-title`, `.stats-number`, `.stat-value`. Weight 600.
- `--font-body` — `'Manrope'` — the default `body` font-family; everything not covered above.
- Both are loaded via the Google Fonts `@import` at the top of `styles.css`. Don't reference "Inter" — it's no longer the site's body font.

**Font-size scale.** Never hardcode a `font-size` in px or a raw rem — use a `--fs-*` token. The scale (defined in `styles.css` `:root`, theme-independent):

| token | value | px | use |
|---|---|---|---|
| `--fs-2xs` | 0.5rem | 8 | micro labels |
| `--fs-xs` | 0.625rem | 10 | tiny badges / meta |
| `--fs-sm` | 0.75rem | 12 | secondary / dense text, most badges |
| `--fs-md` | 0.875rem | 14 | **base UI/body text — the `body` default** |
| `--fs-lg` | 1rem | 16 | emphasis, section labels |
| `--fs-xl` | 1.125rem | 18 | card titles |
| `--fs-2xl` | 1.25rem | 20 | subheadings |
| `--fs-3xl` | 1.5rem | 24 | page/section headings |
| `--fs-4xl` | 2rem | 32 | scale cap — large icons, hero numbers |

- `body` sets `font-size: var(--fs-md)`, so most text needs **no** explicit `font-size` — only declare one when a component genuinely differs from 14px.
- These are the *only* sizes the UI should use. `python manage.py lint_css --fix` maps any stray px/rem font-size to the nearest token (capped at `--fs-4xl`) and enforces it.

### Layout dimensions

`--sidebar-width` (260px), `--sidebar-collapsed-width` (72px), `--topbar-height` (52px). Reference these when positioning fixed/absolute elements relative to the chrome — don't hardcode.

---

## Class naming

Loose BEM-ish: **block**, then **block-element**, then modifier. Match the prefix already used by the file/feature you're editing rather than inventing a new vocabulary.

```css
.admin-card { }
.admin-card-header { }
.admin-card-body { }
.wk-card { }                 /* "wk" = work/order card family */
.wk-card.remedial-card { }   /* modifier as a second class */
```

- One CSS file per page/feature, named to match the template/view (`dashboard.css` ↔ `dashboard.html`).
- Page-specific tweaks may live in a `{% block extra_css %}` inline `<style>` in the template (see the `dr-*` prefixed rules in `admin_design_rules.html`) — but anything reusable belongs in a `.css` file.

---

## Icons

Bootstrap Icons (`<i class="bi bi-..."></i>`) are the icon set already in use (e.g. `bi bi-palette`). Use them sparingly and only where they aid scanning — don't decorate every label. No emoji in the UI.

---

## Checklist before adding/editing CSS

1. Am I using tokens for every colour, radius, shadow, spacing, transition, and **font-size** (`--fs-*`)? (No raw hex, no magic pixel gaps, no px/raw-rem font sizes.)
2. Does it still read correctly in **both** themes? (If you only tested dark, you probably hardcoded something.)
3. Is my class prefix consistent with the feature's existing block name?
4. Is this in the right file — the feature's own CSS, not `styles.css`? (`styles.css` is global chrome + the token definitions only.)
