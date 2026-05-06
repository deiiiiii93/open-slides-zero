# Sumi-e Brutalist Redesign — Design Spec

**Date:** 2026-05-06
**Status:** Approved (brainstorm). Pending implementation plan.
**Scope:** Frontend chrome only. Slide iframes (sandboxed user-generated HTML) are explicitly out of scope.

---

## 1. Goal

Replace the current "Apple Refined" cool-gray frontend chrome with a single coherent **Sumi-e Brutalist** identity: warm cream paper background, sumi-ink black borders and headlines, display serif (Playfair Display) for hierarchy, zero rounded corners on chrome, and a small set of deepened functional accents (oxblood / moss / sienna).

The redesign is purely visual. **No structural refactoring**, no component reorganization, no behavior changes. It is a token swap + typography upgrade across `styles.css` plus the inline-styled component files.

This spec also fixes a known regression: the earlier Apple Refined pass missed `HitlReviewPanel.tsx`, `AdvancedChatPanel.tsx`, `PlaygroundPanel.tsx`, `CommentLayer.tsx`, and `Markdown.tsx`, all of which still carry the original blue palette (`#2563eb`, `#eff6ff`, `#bfdbfe`, `#1d4ed8`, etc.). Those files are brought into the system in this pass.

---

## 2. Decisions Locked

| # | Decision | Choice |
|---|---|---|
| 1 | Direction | E · Sumi-e Brutalist |
| 2 | Reading-area treatment | B · Brutalist frame, calm body |
| 3 | Display typography | A · Playfair Display 900 (Google Fonts) |

Decision 2 is load-bearing: the brutalist treatment lives at the *outer chrome* (page header, panel borders, buttons, form controls, section rules). Inside reading-heavy areas (LiveStream token feed, HITL review panel descriptions, slide thumbnails sidebar, AdvancedChatPanel transcript), the body remains a calm system sans at 1.55–1.7 line-height, low-decoration. The brutalism *frames* content; it does not compete with it.

---

## 3. Token System

### 3.1 Palette

| Token | Hex | Role |
|---|---|---|
| `paper` | `#f5f3ee` | Page background. Warm cream. |
| `paper-recess` | `#e8e3d8` | Inputs, recessed cells, active stage backgrounds. |
| `ink` | `#0a0a0a` | All chrome borders, display headlines, primary buttons. |
| `ink-soft` | `#1c1c1e` | Body text. |
| `ink-muted` | `#5c5852` | Secondary text, eyebrow labels (warm-gray, not cool). |
| `ink-tertiary` | `#948e83` | Metadata, disabled state, pending thumbnails. |
| `oxblood` | `#8b1a1a` | Errors. Borders + text only, no fills. |
| `moss` | `#3d5a2a` | Ready / approved / success. |
| `sienna` | `#8a5a14` | Required / warning / will-retry. |
| `code-dark` | `#0b1020` | LiveStream HTML buffer pre-block. **Single permitted inversion.** Kept for legibility of streaming HTML. |

**Removed:** every blue (`#2563eb`, `#eff6ff`, `#bfdbfe`, `#1d4ed8`, `#dbeafe`, `#93c5fd`, `#1e3a8a`, `#3730a3`, `#eef2ff`, `#e0e7ff`), every cool-gray (`#172033`, `#0f172a`, `#475569`, `#64748b`, `#94a3b8`, `#cbd5e1`, `#d8dee8`, `#e5e7eb`, `#f8fafc`, `#fbfcfe`), and the Apple Refined neutrals (`#1c1c1e` survives as `ink-soft`; `#fafafa` `#aeaeb2` `#6e6e73` `#ebebeb` `#e0e0e0` `#c8c8c8` `#f5f5f5` `#f7f7f7` `#bcbcbc` `#f0f0f0` `#f3f3f3` are all replaced).

### 3.2 Geometry

- `border-radius: 0` everywhere on app chrome. **No exceptions** in panels, buttons, inputs, pills, cards, or focus rings. (Status pills are also rectangular.)
- Standard border: `1.5px solid #0a0a0a`.
- Emphasis border (header bottoms, footer tops, primary action separators): `2px solid #0a0a0a`.
- Section rule (between rows inside a panel): `1px solid #0a0a0a`.
- Thumbnail-active border: `2px solid #0a0a0a`. Inactive: `1.5px solid #0a0a0a`. Pending: `1.5px solid #948e83`.
- `box-shadow: none` everywhere on chrome. The hairlines do the work.

### 3.3 Spacing

- Panel padding: 24–32px.
- Hero block padding: 48px top / 32px sides.
- Step-row padding: 18–24px vertical / 28–32px horizontal.
- Section gap: 32px between major sections; 14px between sibling form fields.
- The 8pt grid is preserved internally; all chrome dimensions remain multiples of 4.

### 3.4 Motion

- Transitions: `border-color 100ms linear, background 100ms linear`. **No easing curves**, no fades over 100ms. The brutalist aesthetic snaps; it does not glide.
- No hover scale, no shadow lift, no animated underlines.

### 3.5 Focus

Focus replaces the current soft `box-shadow` ring with a 2px-offset hard outline:
```css
.osz-control:focus {
  outline: 2px solid #0a0a0a;
  outline-offset: 1px;
  border-color: #0a0a0a;
  box-shadow: none;
}
```

---

## 4. Typography

### 4.1 Faces

- **Display:** Playfair Display, weight 900 only. Loaded from Google Fonts via `<link>` in `frontend/index.html` (preconnect + woff2 subset). Used for: page hero, panel section headings, step badge numerals (e.g., "01"), HITL review headlines, thumbnail plate numerals, slide-row titles.
- **Body / UI:** system sans stack — `ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`. No webfont loaded for body.
- **Mono:** `ui-monospace, "SF Mono", monospace`. Used for: thread IDs, model IDs, char counts, elapsed timers.

### 4.2 Scale

| Role | Face | Size | Weight | Tracking | Line-height |
|---|---|---|---|---|---|
| Page hero | Playfair | 54px | 900 | -0.022em | 1.0 |
| Panel section | Playfair | 22–26px | 700 | -0.015em | 1.1 |
| Step badge numeral | Playfair | 18px | 900 | 0 | 1.0 |
| Slide-row title | Playfair | 18px | 700 | -0.012em | 1.15 |
| Eyebrow label | system sans | 11px | 700 | 2.5px | 1.0 |
| Body | system sans | 15px | 400 | normal | 1.65–1.7 |
| Body-secondary | system sans | 13px | 400 | normal | 1.55 |
| Button | system sans | 12–13px | 700 | 1.4–2px | 1.0 |
| Mono | ui-monospace | 11–13px | 400 | 0 | 1.4 |

All eyebrow labels and all button labels are **uppercase**. Headlines (Playfair) stay in title case.

### 4.3 Webfont loading

Add to `frontend/index.html` `<head>` (above `<title>`):

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@900&display=swap">
```

`display=swap` accepts a brief FOUT — fallback `Georgia, serif` is acceptable in the swap window. Self-hosting via `@fontsource/playfair-display` is a follow-up if external font requests are blocked in any deployment.

---

## 5. Component Recipes

These are the canonical CSS-class definitions. All chrome components reduce to one of these.

### 5.1 Buttons

```css
.osz-button {
  border: 1.5px solid #0a0a0a;
  border-radius: 0;
  background: #f5f3ee;
  color: #0a0a0a;
  padding: 10px 22px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1.4px;
  text-transform: uppercase;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 100ms linear, background 100ms linear, color 100ms linear;
}
.osz-button:hover:not(:disabled) {
  background: #0a0a0a;
  color: #f5f3ee;
}
.osz-button:disabled {
  border-color: #948e83;
  color: #948e83;
  cursor: default;
}
.osz-button-primary {
  background: #0a0a0a;
  color: #f5f3ee;
}
.osz-button-primary:hover:not(:disabled) {
  background: #f5f3ee;
  color: #0a0a0a;
}
.osz-header-btn {
  padding: 6px 14px;
  font-size: 11px;
  letter-spacing: 1.2px;
  /* otherwise inherits .osz-button base */
}
.osz-button-danger {
  border-color: #8b1a1a;
  color: #8b1a1a;
}
.osz-button-danger:hover:not(:disabled) {
  background: #8b1a1a;
  color: #f5f3ee;
}
```

### 5.2 Inputs and textareas

```css
.osz-control {
  width: 100%;
  box-sizing: border-box;
  border: 1.5px solid #0a0a0a;
  border-radius: 0;
  background: #e8e3d8;
  color: #1c1c1e;
  padding: 10px 12px;
  font-size: 14px;
  line-height: 1.4;
  outline: none;
  transition: outline-color 100ms linear;
}
.osz-control:focus {
  outline: 2px solid #0a0a0a;
  outline-offset: 1px;
}
.osz-control:disabled {
  background: #f5f3ee;
  color: #948e83;
}
```

### 5.3 Panels

```css
.osz-panel {
  border: 1.5px solid #0a0a0a;
  border-radius: 0;
  background: #f5f3ee;
  box-shadow: none;
}
.osz-panel + .osz-panel {
  margin-top: 0; /* panels stack flush — top border of next IS bottom border of previous */
  border-top: 0;
}
.osz-panel-body {
  padding: 24px 28px;
}
```

### 5.4 Step badge

```css
.osz-step-badge {
  display: inline-grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 0;
  background: #0a0a0a;
  color: #f5f3ee;
  font-family: 'Playfair Display', Georgia, serif;
  font-weight: 900;
  font-size: 18px;
}
```

### 5.5 Status pills

```css
.osz-status {
  display: inline-flex;
  align-items: center;
  border-radius: 0;
  padding: 5px 12px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.4px;
  text-transform: uppercase;
  background: transparent;
}
.osz-status-ready    { border: 1px solid #3d5a2a; color: #3d5a2a; }
.osz-status-required { border: 1px solid #8a5a14; color: #8a5a14; }
.osz-status-error    { border: 1px solid #8b1a1a; color: #8b1a1a; }
.osz-status-busy     { background: #0a0a0a; color: #f5f3ee; border: 1px solid #0a0a0a; }
```

### 5.6 Header bar

The page header is **inverted** (sumi black with cream type). This is the system's signature element and the strongest aesthetic signal.

```css
.osz-app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 28px;
  border-bottom: 2px solid #0a0a0a;
  background: #0a0a0a;
  color: #f5f3ee;
}
.osz-app-header .osz-header-btn {
  background: transparent;
  color: #f5f3ee;
  border-color: #f5f3ee;
}
.osz-app-header .osz-header-btn:hover:not(:disabled) {
  background: #f5f3ee;
  color: #0a0a0a;
}
```

### 5.7 Eyebrow label (utility)

```css
.osz-eyebrow {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 2.5px;
  text-transform: uppercase;
  color: #5c5852;
  font-family: ui-sans-serif, -apple-system, sans-serif;
}
```

### 5.8 Section rules

A new utility for the in-panel hairlines that separate step rows and stage rows:

```css
.osz-rule { border-top: 1px solid #0a0a0a; }
.osz-rule-emphasis { border-top: 2px solid #0a0a0a; }
```

---

## 6. File-by-file Changes

This section enumerates every file that changes. **No new files are created** other than the spec itself; no files are deleted.

### 6.1 `frontend/index.html`

**Change:** add Playfair Display webfont links to `<head>`.
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@900&display=swap">
```

### 6.2 `frontend/src/styles.css` (425 lines → ~480 lines)

**Mechanical token swaps** across every selector. Specific changes:

- `:root`: `color: #1c1c1e; background: #f5f3ee;` (replaces `#fafafa`).
- `body` gradient: replace with flat `background: #f5f3ee;` (the gradient was an Apple Refined holdover; brutalism is flat).
- `.osz-create-shell`: unchanged.
- `.osz-create-hero h1`: `font-family: 'Playfair Display', Georgia, serif; font-weight: 900; font-size: 54px; letter-spacing: -1.2px; line-height: 1.0; color: #0a0a0a;`.
- `.osz-create-hero p`: `color: #1c1c1e; font-size: 15px; line-height: 1.7; max-width: 640px;`.
- `.osz-error`: `border: 1.5px solid #8b1a1a; background: #f5f3ee; color: #8b1a1a; border-radius: 0;`.
- `.osz-panel`: see §5.3. Remove `box-shadow`; set `border-radius: 0`.
- `.osz-panel-body`: padding 24px 28px.
- `.osz-section-header`: add `padding-bottom: 14px; border-bottom: 1px solid #0a0a0a;` to give each section a hard inner rule.
- `.osz-step-badge`: see §5.4 (replaces the round 26px `#1c1c1e` badge).
- `.osz-section-title h2`: Playfair 700, 22–26px, color `#0a0a0a`.
- `.osz-muted`: `color: #5c5852;`.
- `.osz-field`: `color: #0a0a0a; font-size: 11px; letter-spacing: 2.5px; font-weight: 700; text-transform: uppercase;` (the field labels become eyebrow labels).
- `.osz-control`: see §5.2.
- `.osz-grid-2 / .osz-grid-3`: unchanged (layout only).
- `.osz-status*`: see §5.5.
- `.runtime-config-*`: token swaps; `.runtime-model-card` becomes `border: 1px solid #0a0a0a; background: #e8e3d8; border-radius: 0;`.
- `.osz-disclosure`: border-top `1px solid #0a0a0a`. The `+` / `-` glyph in `::after` becomes a hard square (`background: #0a0a0a; color: #f5f3ee; border-radius: 0;`).
- `.osz-button*`: see §5.1.
- `.osz-create-actions`: padding-top 24px; add `border-top: 2px solid #0a0a0a;`.
- `.osz-file-list`: border `1.5px solid #0a0a0a; border-radius: 0;`. Row dividers `1px solid #0a0a0a`.
- `.osz-warning`: `border: 1px solid #8a5a14; background: #f5f3ee; color: #8a5a14; border-radius: 0;`.
- `.osz-header-btn`: rewritten per §5.1.
- **New utilities:** `.osz-app-header`, `.osz-eyebrow`, `.osz-rule`, `.osz-rule-emphasis`, `.osz-status-busy`, `.osz-button-danger`, `.osz-status-error`.
- Media query at 820px: hero font-size drops from 54px → 40px (was 30px → 28px). Confirm tracking still reads.

### 6.3 `frontend/src/App.tsx` (2465 lines)

Header restructure (lines ~696–745):
- Root `<div>`: keep `padding: 16, maxWidth: 1520, margin: "0 auto"`. Background already comes from `:root`.
- Header bar: replace inline style with `className="osz-app-header"`. The black header replaces the current cream bottom-bordered bar.
- App-name span: `fontFamily: "'Playfair Display', Georgia, serif", fontWeight: 900, fontSize: 20, letterSpacing: -0.3`.
- Separator dots: `color: "#948e83"`.
- Stage / model text: `color: "#cfc8b9"` (cream-gray, against black bar).
- Busy pill (around L733–742): replace with `<span className="osz-status osz-status-busy">…</span>`.
- All header buttons (Config, Masterpieces, History, Export, Delete, New deck) keep `className="osz-header-btn"` — the class itself is rewritten in styles.css, so no per-button code change needed.
- Export dropdown items: keep `background: none; border: none` overrides (these are in-dropdown items, not header buttons). Update their hover color to `#0a0a0a` and the dropdown shell border to `1.5px solid #0a0a0a; border-radius: 0;` plus drop the soft shadow.

Hero (~L780–810):
- Add eyebrow `<div className="osz-eyebrow">— Compose a new deck</div>` above h1.
- h1 picks up new typography from `.osz-create-hero h1` automatically.

Step panels — the existing structure (badge + title + status pill + body) maps directly onto §5.4 / §5.5 / §5.7. No JSX restructuring. Token swap only.

Review stage pills (~L1044–1052) — currently inline-styled. Refactor to use `.osz-status-busy` pattern: selected = `background: #0a0a0a; color: #f5f3ee; border: 1px solid #0a0a0a;`; inactive = `background: #f5f3ee; color: #5c5852; border: 1px solid #0a0a0a;`.

Image insertion / picker / asset card (~L1441–1550): replace remaining `#ebebeb` / `#e0e0e0` / `#fafafa` / `#f5f5f5` / `#c8c8c8` references with `#0a0a0a` borders, `#e8e3d8` backgrounds, no border-radius. Selected asset border = `2px solid #0a0a0a`.

Masterpieces panel and recent decks list: same token swap pattern; cards become 1.5px ink-bordered rectangles on paper.

### 6.4 `frontend/src/DeckCanvas.tsx` (165 lines)

- Sidebar `borderRight`: `"1.5px solid #0a0a0a"`.
- Sidebar wrapper background: `"#f5f3ee"`.
- Add a sidebar header before the thumbnail map: small eyebrow showing `Slides · {count}` with a `borderBottom: "1px solid #0a0a0a"`.
- Thumbnail buttons: replace the rounded selected/inactive borders with hard 2px ink (selected) / 1.5px ink (ready) / 1.5px `#948e83` (pending). Background: ready = `#f5f3ee`, pending = `#f5f3ee` with `color: "#948e83"`. The "Slide N" label becomes `fontFamily: "'Playfair Display', Georgia, serif", fontWeight: 700, fontSize: 14`. The "rendering" suffix becomes its own line below in `fontSize: 10, color: "#948e83"`.
- Iframe container: `border: "2px solid #0a0a0a"; background: white;` (no `border-radius`).
- The 14px row gap between sidebar and stage stays.
- `pendingSlideHtml(...)`: replace the gradient + card with a simpler cream-paper placeholder that matches the system. Inner content: top eyebrow `RENDERING`, Playfair headline `Slide N is generating`, body line `The agent is producing this slide's HTML`. Keep `font-family: ui-sans-serif, -apple-system, sans-serif` and add `font-family: Georgia, serif` for the headline (Playfair is not loaded inside the sandboxed iframe; Georgia is a defensible fallback). Background: `#f5f3ee`. Hard 2px black border around the inner card. **No gradients.**

### 6.5 `frontend/src/LiveStream.tsx` (228 lines)

- Container: always `border: 1.5px solid #0a0a0a; border-radius: 0; background: #f5f3ee; box-shadow: none;`. The previous active/inactive border-color distinction is dropped — the LiveStream column is always visible during generation and doesn't need a chrome highlight to communicate "I am the active panel."
- Title div (small uppercase): `color: "#5c5852"; font-size: 11; letter-spacing: 2.5; font-weight: 700`.
- Subtitle: `color: "#5c5852"; font-size: 12`.
- Active node label: `font-family: "'Playfair Display', Georgia, serif"; font-weight: 700; font-size: 18; color: "#0a0a0a"`.
- Comment buffer: background `#e8e3d8`; border `1px solid #0a0a0a`; color `#1c1c1e`. Border-radius 0.
- Tag summaries (the `<details>` rows): replace the current spaced rows with hairline-separated rows. Each `<details>` wrapper gets `border-bottom: 1px solid #0a0a0a; padding: 0;`. Summary row padding `10px 14px`. Tag name color `#1c1c1e` weight 600. Elapsed time mono `#948e83` 10px.
- When a tag is open, its content area is `background: #e8e3d8; padding: 12px 14px; border-top: 1px solid #0a0a0a;` (no separate inner border). The `<Markdown>` body inside stays calm — markdown body at 12–13px is the "calm body" zone, do not force display serif here.
- HTML slides section: outer `<details>` follows the same hairline pattern. Each per-slide card: ready/streaming `border: 1px solid #0a0a0a`; queued `border: 1px solid #948e83`. Card header strip background: active card `#e8e3d8` (recessed), inactive card `#f5f3ee`. Card title (the "SLIDE NN" label): `fontFamily: "'Playfair Display', Georgia, serif", fontWeight: 700, fontSize: 13`.
- The dark `<pre>` block keeps `background: "#0b1020"`. Update the text color to `#cfc8b9` (cream gray, not the current `#d1d5db`) for warmer harmony with the paper. Border-radius 0.
- The check ✓ when slide is done: change `color: "#16a34a"` to `color: "#3d5a2a"` (moss).

### 6.6 `frontend/src/HitlReviewPanel.tsx` (803 lines) — **brought into system**

This file was missed in the previous Apple Refined pass. It still uses heavy blue accents.

- Section wrapper `style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}` → `style={{ padding: 24, border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee" }}`. Multiple occurrences.
- `border: "2px solid #2563eb"` (selected structure card) → `border: "2px solid #0a0a0a"`.
- `background: "#fafafa"` (cards) → `background: "#e8e3d8"` for recessed cells; `#f5f3ee` for non-recessed.
- `color: "#555"` and `"#666"` → `"#5c5852"`.
- `color: "#999"` → `"#948e83"`.
- Pattern card (~L466–512): replace blue active/selected/current colors:
  - `border: current ? "1px solid #93c5fd" : "1px solid #d8dee8"` → `current ? "1.5px solid #0a0a0a" : "1px solid #948e83"`.
  - `outline: selected ? "2px solid #2563eb" : "2px solid transparent"` → `selected ? "2px solid #0a0a0a" : "2px solid transparent"`.
  - `background: selected ? "#eff6ff" : "white"` → `selected ? "#e8e3d8" : "#f5f3ee"`.
  - Inner pill `background: current ? "#dbeafe" : "#f8fafc"; color: current ? "#1d4ed8" : "#475569"` → `current ? "#0a0a0a" : "transparent"; color: current ? "#f5f3ee" : "#5c5852"; border: 1px solid #0a0a0a;`.
  - `color: "#9a3412"` (caution) → `#8a5a14` (sienna).
  - `color: "#1d4ed8"` (current pattern label) → `#0a0a0a`.
- Stage tabs at top of panel: replace blue selected state with inverted `#0a0a0a` background + `#f5f3ee` text. Inactive: `color: #5c5852`, no background, hairline `1px solid #0a0a0a` between tabs.
- Slide review rows (per the workspace mockup §6 of design): rows separated by `border-bottom: 1px solid #0a0a0a`. Row with comment gets `background: #e8e3d8`. Comment text rendered with `borderLeft: "2px solid #8b1a1a", color: "#8b1a1a", paddingLeft: 10, fontStyle: "italic", fontSize: 12`.
- Action footer: `borderTop: "2px solid #0a0a0a", padding: "20px 28px", background: "#0a0a0a", color: "#f5f3ee"`. Approve button is the inverted-cream variant of `.osz-button-primary` (cream background, ink text). "Send back" button is transparent with `1.5px solid #f5f3ee`.

### 6.7 `frontend/src/AdvancedChatPanel.tsx` (475 lines) — **brought into system**

- Outer panel (~L268): `border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee"`.
- Section header bottom rule (~L277): `borderBottom: "1px solid #0a0a0a"`.
- Background bands (~L298): replace `#f8fafc` with `#e8e3d8`.
- Quick-action chips (~L141): `border: isQueued ? "1.5px solid #0a0a0a" : "1px solid #0a0a0a"; background: isQueued ? "#e8e3d8" : "#f5f3ee"; color: "#1c1c1e"; borderRadius: 0`.
- Message bubble user vs agent (~L339): user `border: "1.5px solid #0a0a0a", background: "#e8e3d8"`; agent `border: "1px solid #0a0a0a", background: "#f5f3ee"`. Both `borderRadius: 0`.
- Send-row composer (~L308, L364, L390, L418): borders `1.5px solid #0a0a0a`, radius 0, body white-on-paper retained for typing affordance — actually use `#e8e3d8` for the textarea background per §5.2.
- Empty state text (~L438, L469): `color: "#5c5852"`.

### 6.8 `frontend/src/PlaygroundPanel.tsx` (1286 lines) — **brought into system**

- Top section panel (~L673, L723, L862, L972, L1039): all `border: "1px solid #e5e5e5", borderRadius: 6` → `border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee"`.
- View-toggle buttons (~L694, L700): selected `border: "1.5px solid #0a0a0a"; background: "#0a0a0a"; color: "#f5f3ee"`; inactive `border: "1px solid #0a0a0a"; background: "#f5f3ee"; color: "#0a0a0a"`. **Drop the blue.**
- Lane card (~L834): selected `border: "1.5px solid #0a0a0a"; background: "#e8e3d8"; color: "#0a0a0a"`. Inactive `border: "1px solid #0a0a0a"; background: "#f5f3ee"`.
- Idea / suggestion / refinement chips (~L985, L1004): the two blue-ish `#eff6ff/#bfdbfe/#1e3a8a` and indigo-ish `#eef2ff/#e0e7ff/#3730a3` chips collapse into a single neutral chip: `border: "1px solid #0a0a0a"; background: "#e8e3d8"; color: "#1c1c1e"; borderRadius: 0`. The semantic distinction (suggestion vs refinement) becomes a small uppercase label inside the chip rather than color-coded.
- Modal overlay (~L1133) `crimson` error: replace with `#8b1a1a`.
- Mouse-enter hover (~L1070): `e.currentTarget.style.background = "#e8e3d8"` (was `#f5f5f5`).
- Body text colors: `#475569 / #64748b / #334155 / #0f172a` → `#1c1c1e` (body) or `#5c5852` (secondary) or `#948e83` (metadata).

### 6.9 `frontend/src/CommentLayer.tsx` (136 lines) — **brought into system**

- Drag-box live: `border: "2px dashed #0a0a0a", background: "rgba(10,10,10,0.06)"`.
- Pending box: `border: "2px solid #0a0a0a", background: "rgba(10,10,10,0.05)"`.
- Comment input shell (~L97–113): `background: "#f5f3ee", padding: 8, border: "1.5px solid #0a0a0a", borderRadius: 0`. Inner input `border: "1px solid #0a0a0a", borderRadius: 0, padding: 6`.
- Send / ✕ buttons: adopt `.osz-button` styling (or inline-equivalent: `1.5px solid #0a0a0a, background #0a0a0a/#f5f3ee, uppercase 11px 700 letter-spacing 1.4px, padding 6px 12px`).

### 6.10 `frontend/src/Markdown.tsx` (59 lines)

- `h1`: `fontFamily: "'Playfair Display', Georgia, serif", fontWeight: 700, fontSize: 22, marginTop: 14, color: "#0a0a0a"`.
- `h2`: `fontFamily: "'Playfair Display', Georgia, serif", fontWeight: 700, fontSize: 18, borderBottom: "1px solid #0a0a0a", paddingBottom: 4, marginTop: 12, color: "#0a0a0a"`.
- `h3`: keep system sans, weight 700, size 14, color `#0a0a0a`.
- `code` (inline): `background: "#e8e3d8", border: "1px solid #0a0a0a", padding: "1px 4px", borderRadius: 0, fontSize: "0.9em", color: "#1c1c1e"`.
- `pre`: `background: "#e8e3d8", padding: 12, border: "1px solid #0a0a0a", borderRadius: 0, overflow: "auto", fontSize: 12`.
- `th`: `border: "1px solid #0a0a0a", background: "#e8e3d8", padding: "5px 9px", textAlign: "left"`.
- `td`: `border: "1px solid #0a0a0a", padding: "5px 9px"`.
- `blockquote`: `borderLeft: "2px solid #0a0a0a", margin: "8px 0", paddingLeft: 12, color: "#5c5852", fontStyle: "italic"`.

### 6.11 `frontend/src/LayoutWireframe.tsx` (342 lines)

Verify only — this component renders schematic layout wireframes for the layout-pattern picker. If it uses tokens that bleed into chrome, swap them; otherwise leave as schematic placeholder rendering. Most likely it uses neutral grays that need to become `#5c5852 / #948e83 / #0a0a0a`.

---

## 7. Out of Scope

- **Slide iframe content.** `DeckCanvas.tsx`'s iframe `srcDoc` is sandboxed user-generated HTML. The redesign does not enter the iframe except via `pendingSlideHtml(...)` (the placeholder rendered while a slide is generating).
- **Backend.** No backend file changes. No API changes. No prompt changes.
- **Tests.** Frontend has no automated visual tests; manual verification only (§9).
- **Mobile responsive overhaul.** The 820px breakpoint is preserved with token-only adjustments. A separate spec would handle a true mobile reflow.
- **The Apple-Refined memory.** The `auto memory` system has no entries about the prior redesign; nothing to remove. The git commit will document the transition.

---

## 8. Risks

1. **Webfont blocking.** If Google Fonts is unreachable in any deployment environment, Playfair falls back to Georgia via `display=swap`. Georgia at 900-equivalent (it doesn't have a true 900 weight; the browser will synthesize bold) is acceptable but visibly less crisp at 54px. **Mitigation:** if this becomes a real concern, switch to `@fontsource/playfair-display` to self-host. Decision deferred until first user report.
2. **All-caps button labels reduce localization headroom.** German and Russian button labels in all-caps with 1.4–2px tracking may overflow. **Mitigation:** the codebase is currently English-only; revisit only when localization is added.
3. **High-contrast inverted header may appear "loud" relative to the calm body.** This is the intended aesthetic, not a defect. If user feedback rejects it post-implementation, the alternative is a cream header with `borderBottom: 2px solid #0a0a0a` — a 1-line CSS change in §5.6.
4. **Markdown body inside LiveStream uses calm system sans, not Playfair.** The reading-area-calm decision (§2 decision 2) makes this intentional. The headings inside the markdown body picking up Playfair (§6.10) is the *one* exception, because users read summaries of agent decisions in those panels, and serif H1/H2 anchors them visually as section titles. If users find Playfair-in-markdown jarring against the surrounding calm sans, drop §6.10 h1/h2 face changes — the rest of Markdown.tsx still adopts the palette.
5. **Color-only signals in PlaygroundPanel chips (§6.8).** Collapsing two distinct blue chip variants into one neutral chip removes a chromatic distinction. Mitigation: introduce a small uppercase label inside the chip (e.g., "SUGGESTION" / "REFINEMENT") to retain the semantic difference.
6. **Status pills are accessible.** Oxblood / moss / sienna against cream paper meet WCAG AA contrast; the rectangular geometry + uppercase weight makes them legible without color alone (border + text both carry the signal). Verify with screen-reader text in the implementation pass.
7. **The previous Apple Refined design persists in the user's git history.** No risk; that's the point of git. The commit message for this change should reference the prior commit for traceability.

---

## 9. Verification

Manual verification, executed in this order:

```bash
cd /Users/fuxinyao/open-slides-zero/frontend && npm run dev
# Open http://localhost:5174
```

**Phase 1 — styles.css + index.html:** `osz-create-shell` page renders with cream paper background, hard 1.5px ink panel borders, Playfair 54px headline, eyebrow labels, no rounded corners on chrome, no shadows. Buttons uppercase + tracked. Form inputs cream-recessed.

**Phase 2 — DeckCanvas + LiveStream:** create a deck, watch generation. Sidebar thumbnails show Playfair plate numerals. Iframe wrapped in 2px ink frame. LiveStream container hard-bordered; tag rows separated by hairlines; HTML stream block keeps dark `#0b1020` with cream-gray text. Pending-slide placeholder uses cream paper + Georgia headline (Playfair fallback inside iframe).

**Phase 3 — App.tsx (header + workspace + image picker):** header bar is inverted ink with cream type. All 6 header buttons (Config / Masterpieces / History / Export / Delete / New) consistent. Busy pill is solid black. Review stage pills inverted on selection. Image picker / asset cards use ink borders + cream-recess backgrounds.

**Phase 4 — HITL + Advanced + Playground + Comments:** trigger a HITL checkpoint. Review panel matches the workspace mockup: stage tabs with inverted-on-selected, slide rows with hairline dividers, oxblood comment quotes, footer action bar inverted. Drag a comment box on a slide — drag/pending boxes use ink (not blue). Markdown rendered inside any panel: serif H1/H2, ink hairline rules, recessed `<pre>`/`<code>`, italic-ink blockquotes.

**Phase 5 — visual sweep:** zero blue (`#2563eb` family) anywhere in the rendered DOM. Screenshot the create page and the workspace page; both should read as clearly the same design family.

```bash
# Quick grep for stragglers after implementation:
cd /Users/fuxinyao/open-slides-zero/frontend && grep -rE "#(2563eb|eff6ff|bfdbfe|1d4ed8|dbeafe|93c5fd|1e3a8a|3730a3|eef2ff|e0e7ff)" src/
# Expect: zero matches.
```

---

## 10. Open Questions

None blocking. Two cosmetic decisions can be revisited after implementation if anything feels off:

- Whether the page header inversion (§5.6) is too aggressive (alternative noted in §8.3).
- Whether Markdown headings inside reading-areas should be Playfair (§6.10) or stay calm sans (§8.4).

Both are 1-line changes in CSS / inline styles and do not require re-spec.
