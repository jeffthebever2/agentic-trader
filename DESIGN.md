# Design System: TradingAgents — Personal Trading Suite

> Single source of truth for the web dashboard (`web/static/index.html`) and any new
> screens (Stitch / hand-built). Encodes the combined pass from emil-design-eng,
> redesign-existing-projects, impeccable, high-end-visual-design, design-taste-frontend,
> minimalist-ui, gpt-taste, stitch-design-taste, and industrial-brutalist-ui.

## 1. Visual Theme & Atmosphere

A cockpit-dense financial terminal that still breathes. Register is **product**: the
design serves the data, never upstages it. Density 8 (Cockpit), Variance 5 (offset,
not chaotic — money UIs must stay legible), Motion 5 (fluid CSS, no spectacle).

The mood: a quiet, instrument-grade trading desk at the open bell. Light by default
(daylight desk, glance-driven, numbers must read in a bright room), full dark theme
available (`body.theme-dark`) for after-hours screen time. Single warm-amber accent,
film grain to kill flatness, one slow ambient radial so no surface is dead-flat.

## 2. Color Palette & Roles

Light (default):
- **Surface Soft** `#F6F7F9` — app canvas / scroll background
- **Surface** `#FFFFFF` — cards, sidebar, header, inputs
- **Surface Raised** `#EDEEF2` — table headers, hover fills
- **Surface Rule** `#E0E2E8` — every 1px divider and border (no side-stripes)
- **Ink** `#0E1118` — primary text (never `#000`)
- **Ink Muted** `#52576A` — secondary text, descriptions
- **Ink Faint** `#8890A4` — labels, metadata, captions
- **Accent (Signal Amber)** `#E03E00` / hover `#C43500` — sole accent: CTAs, active nav,
  focus rings, active charts. Saturation kept under 80%. ≤10% of surface (Restrained).

Dark theme overrides: Surface `#13141A`, Soft `#0D0E12`, Raised `#1C1E26`,
Rule `#2C2F3A`, Ink `#F0F1F5`, Muted `#A8B0C2`, Faint `#6B7490`.

Semantic data colors (financial convention, not decoration):
- Up / Buy `#34d399` · Down / Sell `#f87171` · Hold `#fbbf24`
- Tints are washed (`rgba(...,.08–.2)`) — verdict surfaces use full border + tint,
  **never** a colored left stripe.

Banned: pure black/white, neon/outer-glow shadows, AI purple-blue, gradient text,
warm/cool gray mixing.

## 3. Typography Rules

- **Display / UI:** `Geist` (400–800). Tight tracking on numbers (`-0.035em`),
  `text-wrap: balance` on h1–h3. Hierarchy by weight + color, not screaming size.
- **Numeric / Mono:** `JetBrains Mono`. Mandatory for all numbers at this density —
  `font-variant-numeric: tabular-nums`, `font-feature-settings: "tnum","zero"`.
- **Body:** Geist, line-height 1.6–1.7, secondary in Ink Muted.
- **Micro labels:** uppercase, `letter-spacing: .08em`, 10.5px.
- **Banned:** Inter, system serif, any serif in this dashboard, all-caps body.

## 4. Component Stylings

- **Buttons:** flat, `--shadow-1`, no glow. `:active` → `scale(.96)`. Primary = accent
  fill; secondary = surface + 1px rule; danger = rose text on surface. Header CTA is
  magnetic (leans to cursor, springs back via `cubic-bezier(.32,.72,0,1)`).
- **Cards:** 6–8px radius, `--shadow-1`, hover → `--shadow-2`. Card-in-card strips its
  own shadow/border (no nested elevation). Used only where elevation = hierarchy;
  dense data uses 1px rules + negative space instead.
- **Inputs:** label above, accent focus ring (`0 0 0 3px rgba(224,62,0,.10)`),
  hover border = Ink Faint. Error text below, inline, no `alert()`.
- **Loaders:** shimmer skeletons matching layout shape. Spinner is fast (`.7s`) for
  better perceived performance. No generic centered circular spinner as a state.
- **Empty states:** composed "connect Fidelity / run an analysis" prompts, not bare text.
- **Badges:** small square-ish tints for verdicts; status pills only for live state.

## 5. Layout Principles

- Fixed 220px sidebar nav + 52px header + scroll panel. CSS Grid for all multi-col
  (`1fr 320px` dashboard split, `repeat(4,1fr)` stat row), never flex % math.
- Offset, not symmetric: left-rail nav, asymmetric main/aside split, stat cells
  divided by 1px rules rather than boxed cards.
- Hard responsive collapse: <980px sidebar → horizontal scroll bar; <760px single
  column, `min-height: 100dvh` (never `h-screen`), 44px min tap targets, no x-overflow.
- z-index discipline: grain/ambient on fixed pointer-events-none pseudo-elements only.

## 6. Motion & Interaction

- Easing tokens: `--ease-out cubic-bezier(.23,1,.32,1)`, `--ease-in-out
  cubic-bezier(.77,0,.175,1)`, `--ease-spring cubic-bezier(.34,1.56,.64,1)`.
- Press feedback on every interactive surface (`scale .96–.97`). Nav + panels enter
  staggered (≤40–290ms). Cards scroll-reveal once (fade-up + de-blur, IntersectionObserver,
  no scroll listener). Tab swap re-arms reveal.
- GPU only: `transform` / `opacity` / `clip-path`. Keyboard-repeated actions: no animation.
- Full `prefers-reduced-motion` reset — opacity kept, movement dropped, ambient stilled.

## 7. Anti-Patterns (Banned)

No emojis in markup/alt. No Inter, no serif. No `#000`/`#fff`. No neon or outer-glow
shadows (auto-downgraded to `--shadow-1`). No gradient text. No colored side-stripe
borders. No nested-card elevation. No 3-equal-card feature rows. No AI clichés
("Elevate", "Seamless", "Unleash", "Next-Gen"). No em dashes in UI copy. No generic
placeholder names or fake round numbers. No `h-screen`. No broken Unsplash links.
No `transition: all`. No `window.addEventListener('scroll')` for reveals.
