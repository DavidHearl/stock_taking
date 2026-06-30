# CSS Design System

The standing design rules for Sliderobes Atlas. **The single source of truth is the token block in [`static/css/styles.css`](../static/css/styles.css) (`:root`), overridden for light mode in [`static/css/light-mode.css`](../static/css/light-mode.css).** There is also a live, rendered reference page in the app at **Admin → Design Rules** ([`admin_design_rules.html`](../stock_take/templates/stock_take/admin_design_rules.html)) — use it to eyeball swatches/spacing.

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
| `--bg-page` | `#0d0d0f` | `#eceef2` | Outermost page background |
| `--bg-primary` | `#181818` | `#f0f1f3` | Primary surface |
| `--bg-secondary` | `#2c2c30` | `#ffffff` | Cards, panels |
| `--bg-tertiary` | `#3a3a3e` | `#e6e8ec` | Raised/nested surface |
| `--bg-nav` | `#2a2a2e` | `#ffffff` | Sidebar / topbar |
| `--bg-input` | `#232326` | `#ffffff` | Form inputs |
| `--bg-hover` | `rgba(255,255,255,.06)` | `rgba(46,122,191,.08)` | Hover states |
| `--bg-card` | dark gradient | white gradient | Card backgrounds |
| `--overlay-bg` | `rgba(0,0,0,.60)` | `rgba(0,0,0,.40)` | Modal/scrim overlay |

### Text

| Token | Dark | Light | Use |
|---|---|---|---|
| `--text-primary` | `#d8d8da` | `#1a1c20` | Body / headings |
| `--text-secondary` | `#a6a6aa` | `#4d5260` | Supporting text |
| `--text-muted` | `#6a6a6e` | `#888e9c` | Labels, hints, captions |
| `--text-on-color` | `#ffffff` | `#ffffff` | Text on a coloured fill (buttons, badges) |

### Semantic colours

Each has three variants: base (`-color`), `-hover` (darker, for hover), and `-subtle` (low-opacity tint for backgrounds/badges).

| Family | Base (dark) | Base (light) | Meaning |
|---|---|---|---|
| `--primary-*` | `#5a9de6` | `#2e7abf` | Primary actions, links, active state |
| `--success-*` | `#4cc080` | `#2d9d5e` | Success, received, paid, positive |
| `--warning-*` | `#efb040` | `#c88b1f` | Warnings, pending, attention |
| `--danger-*` | `#e05258` | `#c23035` | Errors, delete, overdue, negative |
| `--info-*` | `#56c4e0` | `#2e9dba` | Informational, neutral highlight |
| `--purple-*` | `#8b5cf6` | `#7c3aed` | Secondary accent / categorisation |

Example: a "pending" badge uses `background: var(--warning-subtle); color: var(--warning-color);`. A primary button uses `background: var(--primary-color);` and `:hover { background: var(--primary-hover); }`.

> **Note:** the primary brand colour token is `--primary-color`, *not* `--accent-color`. `--accent-glow` exists (a soft glow tint) but there is no `--accent-color` token — don't reference it.

---

## Layout, spacing, shape

### Spacing scale — use these, don't invent pixel values

| Token | Value |
|---|---|
| `--sp-1` | 4px |
| `--sp-2` | 8px |
| `--sp-3` | 12px |
| `--sp-4` | 16px |
| `--sp-5` | 20px |
| `--sp-6` | 24px |

### Radius

| Token | Value | Use |
|---|---|---|
| `--radius-sm` | 8px | Inputs, small buttons, swatches |
| `--radius-md` | 12px | Cards, panels |
| `--radius-lg` | 16px | Large containers, modals |
| `--radius-pill` | 16px | Pills / search bars |

### Shadows

`--shadow-sm`, `--shadow-md`, `--shadow-lg` — increasing elevation. Light mode uses softer/lighter shadows automatically.

### Borders & motion

- `--border-color` — the one border colour for dividers, card edges, input borders. (`rgba(255,255,255,.1)` dark / `rgba(0,0,0,.12)` light.)
- `--transition` — `all 0.2s ease`. Use it for hover/focus transitions instead of writing your own timing.

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

1. Am I using tokens for every colour, radius, shadow, spacing, and transition? (No raw hex, no magic pixel gaps.)
2. Does it still read correctly in **both** themes? (If you only tested dark, you probably hardcoded something.)
3. Is my class prefix consistent with the feature's existing block name?
4. Is this in the right file — the feature's own CSS, not `styles.css`? (`styles.css` is global chrome + the token definitions only.)
