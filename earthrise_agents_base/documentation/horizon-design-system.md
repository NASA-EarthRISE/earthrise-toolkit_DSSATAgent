# NASA Horizon Design System — Reference

Source: https://website.nasa.gov/horizon-design-system/

---

## Color Tokens

| Token              | Hex       | Usage                                      |
|--------------------|-----------|--------------------------------------------|
| Primary blue       | `#0170B9` | Links, borders, chart lines, layer badges  |
| Action blue        | `#1c67e3` | Secondary chart lines, interactive accents |
| Hover / CTA red    | `#f64137` | Button hover, chart data points, play state|
| Dark nav           | `#2e2e32` | Header, modal headers, timeline bar        |
| Body text          | `#3a3a3a` | Default text color                         |
| Meta / muted text  | `#58585b` | Labels, chart tick text, subtitles         |
| Background         | `#F5F5F5` | Page background                            |
| Surface            | `#FFFFFF` | Cards, panels, modal bodies                |
| Border             | `#dddddd` | Dividers, input borders, card borders      |
| Focus              | `#444447` | Focus rings (dotted outline)               |

---

## Typography

| Role     | Family       | Weights      | CDN Variant                        |
|----------|--------------|--------------|------------------------------------|
| Body     | Public Sans  | 400, 600, 700| `family=Public+Sans:wght@400;600;700` |
| Headings | Inter        | 400, 500, 700| `family=Inter:wght@400;500;700`    |

**Scale:**
- Base: 16px, line-height 1.65em
- H1: 36px / 700
- H2: 30px / 500
- H3: 24px / 500

**Google Fonts import (add to `<head>`):**
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&family=Public+Sans:wght@400;600;700&display=swap" rel="stylesheet">
```

---

## CSS Custom Properties (`:root`)

```css
:root {
  --color-primary:   #0170B9;
  --color-action:    #1c67e3;
  --color-hover:     #f64137;
  --color-dark:      #2e2e32;
  --color-text:      #3a3a3a;
  --color-text-meta: #58585b;
  --color-bg:        #F5F5F5;
  --color-surface:   #FFFFFF;
  --color-border:    #dddddd;
  --color-focus:     #444447;
  --radius:          4px;
  --shadow-card:     0px 0px 4px 0 rgba(0,0,0,0.34);
  --shadow-menu:     0px 4px 10px -2px rgba(0,0,0,0.1);
  --container:       1200px;
  --font-body:       'Public Sans', sans-serif;
  --font-heading:    'Inter', sans-serif;
}
```

---

## Spacing & Layout

- Max container width: **1200px**
- Button padding: `15px 30px` (responsive — reduce at narrower breakpoints)
- Border radius: **4px** (all components)
- Card/panel padding: typically `16px`–`20px`

---

## Shadows

```css
/* Cards and panels */
box-shadow: 0px 0px 4px 0 rgba(0,0,0,0.34);

/* Dropdown menus / floating elements */
box-shadow: 0px 4px 10px -2px rgba(0,0,0,0.1);
```

---

## Buttons

```css
/* Default state */
background: #3a3a3a;
color: #ffffff;
border: none;
border-radius: 4px;
font-family: var(--font-body);
padding: 10px 24px;
cursor: pointer;
transition: background 0.15s, border-color 0.15s;

/* Hover state */
background: #f64137;
```

For dark-background buttons (timeline, map controls), use semi-transparent white:
```css
background: rgba(255,255,255,0.08);
border: 1px solid rgba(255,255,255,0.2);
color: rgba(255,255,255,0.85);
```

---

## Form Inputs

```css
background: var(--color-bg);        /* #F5F5F5 */
color: var(--color-text);           /* #3a3a3a */
border: 1px solid var(--color-border); /* #dddddd */
border-radius: var(--radius);       /* 4px */
font-family: var(--font-body);
font-size: 13px–14px;

/* Focus */
background: var(--color-surface);   /* #FFFFFF */
border-color: var(--color-focus);   /* #444447 */
outline: 1px dotted var(--color-focus);
box-shadow: none;
```

Form labels:
```css
font-size: 11px;
color: var(--color-text-meta);
text-transform: uppercase;
letter-spacing: 1px;
font-family: var(--font-body);
```

---

## Modals

- **Header background:** `var(--color-dark)` — `#2e2e32`
- **Header text:** white (`#ffffff`), `font-family: var(--font-heading)`, `font-size: 13px`, `font-weight: 600`, `letter-spacing: 1.8px`, `text-transform: uppercase`
- **Close button:** use Bootstrap's `.btn-close-white` on dark headers
- **Body background:** `var(--color-surface)` — `#FFFFFF`
- **Footer background:** `var(--color-surface)`, `border-top: 1px solid var(--color-border)`
- **Border radius:** `var(--radius)` — 4px

---

## Navigation / Header

```css
background: var(--color-dark);   /* #2e2e32 — solid, no gradient */
color: #ffffff;
font-family: var(--font-heading);
```

---

## Cards / Panels

```css
background: var(--color-surface);   /* #FFFFFF */
border: 1px solid var(--color-border);
border-radius: var(--radius);
box-shadow: var(--shadow-card);
padding: 16px;

/* Optional left accent bar */
border-left: 4px solid var(--color-primary);
```

---

## Chart.js Conventions

Set global font at script startup (after loading Chart.js):
```js
Chart.defaults.font.family = "'Public Sans', sans-serif";
```

| Element                   | Value                            |
|---------------------------|----------------------------------|
| Primary line / bar        | `#0170B9`                        |
| Fill under primary line   | `rgba(1, 112, 185, 0.10)`        |
| Secondary line (action)   | `#1c67e3`                        |
| Data points / high temp   | `#f64137`                        |
| Low temp line             | `#0170B9`                        |
| Grid lines                | `rgba(221, 221, 221, 0.8)`       |
| Tick / axis text          | `#58585b`                        |
| Legend text               | `#3a3a3a`                        |
| Tooltip background        | `#ffffff`                        |
| Tooltip border            | `#dddddd`                        |
| Tooltip title color       | `#0170B9`                        |
| Tooltip body color        | `#3a3a3a`                        |

---

## Map / Full-Screen App Adaptations

When Horizon is applied to a full-screen map application (e.g., Leaflet), keep the map canvas dark but apply Horizon to all UI chrome:

| Element              | Treatment                                      |
|----------------------|------------------------------------------------|
| Map canvas           | Keep dark (e.g., `#050a14` or tile default)    |
| Header / nav         | `var(--color-dark)` — `#2e2e32`                |
| Floating panels      | `var(--color-surface)` — white, Horizon shadow |
| Timeline bar         | `var(--color-dark)` — `#2e2e32`                |
| Timeline buttons     | Semi-transparent white (dark-bg variant above) |
| Leaflet custom icons | `var(--color-primary)` — `#0170B9`             |
| Draw shapes          | `color: '#0170B9'`, `fillColor: '#0170B9'`, `fillOpacity: 0.12` |
| Modals               | Full Horizon light style                       |

---

## Dark-Surface Text Colors

When text appears on a `#2e2e32` dark background:

| Purpose          | Value                       |
|------------------|-----------------------------|
| Primary text     | `#ffffff`                   |
| Muted / meta     | `rgba(255,255,255,0.45–0.6)`|
| Disabled         | `rgba(255,255,255,0.3)`     |

---

## Focus / Accessibility

- Focus outline: `1px dotted var(--color-focus)` (`#444447`)
- No `box-shadow` focus rings (use dotted outline only)
- Sufficient contrast: body text `#3a3a3a` on `#F5F5F5` background

---

## Checklist for New Projects

- [ ] Add Google Fonts `<link>` for Inter + Public Sans in `<head>`
- [ ] Define `:root` with all CSS custom properties
- [ ] Header/nav uses `#2e2e32` solid background (no gradients)
- [ ] Body background `#F5F5F5`, surface/card background `#FFFFFF`
- [ ] All borders `#dddddd`, border-radius `4px`
- [ ] Buttons: `#3a3a3a` default → `#f64137` hover
- [ ] Form inputs: light surface, `#dddddd` border, dotted focus ring
- [ ] Modal headers: `#2e2e32` with white text, `.btn-close-white`
- [ ] Cards: white surface, `var(--shadow-card)`, optional left accent border
- [ ] `Chart.defaults.font.family` set to Public Sans
- [ ] Chart colors use Horizon palette (primary `#0170B9`, accent `#f64137`)
- [ ] No old NASA dark-theme variables (`--nasa-*`, dark gradients, `#0a1122`, etc.)
