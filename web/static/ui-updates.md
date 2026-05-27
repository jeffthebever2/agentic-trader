# UI Redesign: Financial Terminal Direction
**Date:** 2026-05-26  
**Goal:** Push AI design risk from 87/100 → near 0  
**Constraint:** Preserve all functionality, routes, IDs, JS behavior

---

## AI Design Smell Inventory

### Critical — kill on sight

| File | Line(s) | Problem |
|------|---------|---------|
| `premium-static-ui.css` | 43–48 | `html` background has two radial gradient blobs (cyan + indigo). Classic AI SaaS background. |
| `premium-static-ui.css` | 55–71 | `.ta-mesh-bg` — animated multi-radial gradient blob hero class |
| `premium-static-ui.css` | 73–87 | `.ta-spotlight` — mouse-reactive cyan glow overlay |
| `premium-static-ui.css` | 96–115 | `.ta-card` — glassmorphism (backdrop-filter: blur) + hover translateY |
| `premium-static-ui.css` | 117–121 | `.ta-glass` — pure glassmorphism |
| `premium-static-ui.css` | 21,152–155 | `--ta-primary: #38bdf8` (sky cyan) + `ta-btn-primary` blue-to-cyan gradient |
| `premium-static-ui.css` | 33–35 | `--ta-radius-xl: 1.25rem`, `--ta-shadow-card: 0 18px 50px` — oversized radius + dramatic shadow |
| `premium-static-ui.css` | 6 | `color-scheme: dark` in `:root` — conflicts with app light mode |
| `premium-static-ui.js` | 15–41 | `createSpotlight()` — injects cyan mouse-glow div into body |
| `premium-static-ui.js` | 113–140 | `initGSAP()` — stagger fade-up on all `.ta-card, .card` at load = decorative entrance |
| `index.html` | 1055–1060 | `.rowIn` stagger — ALL dashboard rows fade in from bottom on load (decorative, not state) |
| `index.html` | 1259–1268 | `.tilt-glare::after` — radial gradient glare on cards (parallax shine = AI SaaS card effect) |
| `index.html` | 1032–1047 | Nav/button hover radial wash — pointer-position radial gradient on hover |

### High — fix
| File | Line(s) | Problem |
|------|---------|---------|
| `index.html` | 1148–1165 | `#nav-curtain` — accent gradient sweep wipe on navigation (decorative) |
| `index.html` | 60–63 | Gradient toast backgrounds in `initToasts()` |

### Already mitigated (keep monitoring)
- `ta-no-gradient-repair` block at line 1997 already disables many effects for dark mode
- Warm tinted token system (`--canvas:#F5F4F1`) at line 1644 is solid
- HTML structure (sidebar, header, ticker, stat row, grid) is already financial terminal quality

---

## Design Direction

**Target feel:** Bloomberg data density + TradingView utility + Linear polish  
**Not:** AI startup demo, shadcn card grid, glassmorphism showcase

The app already has a strong foundation. The problem is entirely cosmetic overlays — gradient blobs and glow effects injected on top of an already-professional layout.

---

## Token System (preserve existing, extend)

### Existing tokens (index.html :root — DO NOT CHANGE)
```
--canvas: #F5F4F1        (warm paper background)
--surface: #FCFBFA       (panel background)
--surface-soft: #F2F1ED  (inset surface)
--surface-raised: #EBE9E4 (raised surface)
--surface-rule: #E2DFD8  (border/divider)
--ink: #1A1714           (text primary)
--ink-muted: #534E47     (text secondary)
--ink-faint: #736C61     (text muted)
--accent: #D63A00        (warm orange-red)
--accent-hover: #B83100
--accent-subtle: rgba(214,58,0,.09)
```

### New semantic financial tokens (in premium-static-ui.css)
```
--ta-up: #047857          (positive/green, WCAG AA on white)
--ta-up-soft: rgba(4,120,87,.09)
--ta-down: #b91c1c        (negative/red, WCAG AA on white)
--ta-down-soft: rgba(185,28,28,.09)
--ta-warn: #92400e        (warning/amber, WCAG AA on white)
--ta-warn-soft: rgba(245,158,11,.10)
--ta-neutral: var(--ink-faint)
--ta-info: #1d4ed8        (blue)
--ta-info-soft: rgba(29,78,216,.09)
--ta-stale: rgba(245,158,11,.08)   (stale data background)
--ta-disabled: var(--ink-faint)
--ta-focus: rgba(214,58,0,.35)
```

### Motion tokens
```
--ta-t-fast: 90ms
--ta-t-base: 140ms
--ta-t-slow: 180ms
--ta-t-very-slow: 240ms
--ta-ease: cubic-bezier(0.16, 1, 0.3, 1)
```

---

## Typography System (existing, unchanged)
- Primary: `"Geist"` — variable weight, warm
- Mono: `"JetBrains Mono"` — for prices, codes, timestamps
- Base: 13.5–14px
- Label: 10–11px, uppercase, tracked
- No gradient text. No decorative lettering.

---

## Layout System (existing, unchanged)
- Sidebar 236px | Header 52px | Main flex-1
- Dashboard: ticker tape + stat row + 2-col grid (65%/35%)
- Terminal-style dividers (1px solid rule, no gaps)

---

## Component Changes

### Cards
- REMOVE: backdrop-filter blur, translateY hover, oversized radius
- KEEP: 1px border, flat opaque background, tight radius (5–7px)
- NEW: no hover lift; row highlight via background change only

### Badges  
- REMOVE: pill border-radius 999px (keep for status-only badges)
- KEEP: semantic colors (up/down/warn)

### State containers (.ta-state)
- REMOVE: dashed border-radius-xl treatment
- USE: solid border, flat background, functional not decorative

### Skeleton
- KEEP: shimmer is legitimate loading UX

### Tables
- KEEP: sticky header, row hover
- REMOVE: blue row hover tint from premium-static-ui.css

---

## Motion System

### Keep (purposeful state/feedback motion)
- `flashUp` / `flashDown` — value change indicators
- `statSwap` — stat number update
- `stepPulse` — running agent breathing
- `sonar` — live connection ping
- `popIn` — details/dropdown open
- Focus ring transitions
- Button press feedback (transform scale)
- Page title swap

### Remove (decorative)
- `taMeshShift` — animated gradient blob background
- Spotlight mouse-reactive glow
- `rowIn` stagger on all dashboard rows
- `.tilt-glare` radial gradient on cards
- Nav hover radial wash (`::after` pointer-position gradient)
- GSAP stagger fade-up on card load

### Simplify
- Nav curtain: remove gradient, keep functional page-switch feedback OR remove entirely
- Logo draw animation: keep (1.1s, subtle, not marketing)

---

## Accessibility (existing is already good, preserve)
- Focus-visible ring: `outline: 2px solid var(--accent)` — keep
- Reduced-motion: `@media (prefers-reduced-motion: reduce)` block — keep
- Skip to main content link — keep
- aria labels on interactive elements — keep

---

## Files to Change
1. `web/static/premium-static-ui.css` — full rewrite
2. `web/static/premium-static-ui.js` — remove spotlight, remove GSAP stagger
3. `web/static/index.html` — targeted inline CSS edits (rowIn, tilt-glare, nav wash)

## Files NOT to Touch
- `web/static/tailwind.min.css`
- `web/static/chart.umd.min.js`
- `web/static/chartjs-financial.min.js`
- `web/static/marked.min.js`
- All Python/backend files
- All JavaScript function logic in index.html

---

## Verification Checklist
- [ ] No radial gradient blobs on html or body background
- [ ] No cyan/sky-blue/indigo color in any visible element
- [ ] No spotlight overlay injected by JS
- [ ] No glassmorphism (backdrop-filter: blur) on main app chrome
- [ ] No translateY hover on cards
- [ ] No stagger fade-up on page load
- [ ] No tilt-glare radial gradient on cards
- [ ] Dashboard loads without row animation stagger
- [ ] All charts still render
- [ ] All navigation still works
- [ ] All API calls and auth still work
- [ ] Dark mode still works
- [ ] Skeleton shimmer still works
- [ ] Value flash animations (up/down) still work
- [ ] Focus rings still visible
- [ ] Reduced-motion respected
