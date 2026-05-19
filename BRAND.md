# Brand & Visual Direction: TradingAgents

> Art-direction spec covering brandkit (identity board plan), imagegen-frontend-web,
> imagegen-frontend-mobile, and image-to-code. No raster image tool exists in this
> environment, so this is the implementation-ready direction those skills generate
> *from* — render externally (Stitch / image model) using these exact prompts.

## 1. Brand Strategy

- **Category:** dark product / operator — personal quantitative trading suite.
- **Audience:** one power user (the owner): runs ML signals, backtests, paper + live
  Fidelity/Webull trading. Expert, glance-driven, distrusts hype.
- **Personality:** instrument-grade, calm under volatility, precise, no theatrics.
- **Promise:** every position under control; signals you can audit, not a black box.
- **Core metaphor:** the **ascending trace** — a price line breaking up through a
  level. Already the live logo (`<polyline points="22 7 13.5 15.5 8.5 10.5 2 17">`).
- **Avoid:** casino/crypto neon, rocket "to the moon", gold-and-navy fintech cliché,
  AI purple. (Matches the codebase memory: honest numbers, no vanity gloss.)

## 2. Logo System

Method: **product action + negative space.** The mark is a breakout trace — a
polyline ascending through an implied resistance level, with an arrowhead formed
by the two short top segments (negative-space direction, not a literal arrow).

- **Icon:** 18px stroke 2.2, white-on-accent rounded-8 tile (as shipped).
- **Wordmark:** `TradingAgents` Geist 700, `-0.01em`, lockup with 10px gap.
- **Mono badge:** `TA//` JetBrains Mono for terminal/favicon contexts.
- One mark, scaled — never recolored per surface. Accent tile in light, accent
  glyph on `--surface` in dark.

## 3. Brandkit Board Plan (2×3, dark operator mode)

Render at 16:10, charcoal `#0D0E12` canvas, strong gutters, sparse type:

1. **Logo cover** — breakout-trace mark, wordmark, huge negative space.
2. **Construction** — trace on a measured grid, level line, arrowhead geometry.
3. **Digital application** — the actual dashboard header + stat row crop (cockpit).
4. **Brand essence** — tagline (Section 6), large Geist, quiet.
5. **Color + type** — Signal Amber `#E03E00` chip, neutral ramp, Geist/JetBrains pair.
6. **System detail** — verdict badges, ticker tape strip, focus-ring chip.

Palette discipline: charcoal + Signal Amber + washed up-green/down-rose only.
Accent repeats every panel. No rainbow, no glow.

## 4. imagegen-frontend-web — section direction (one image per section)

Marketing/login surface, if ever built. Horizontal images, single amber palette:

- **Hero (mid scale, asymmetric):** left — wordmark + tagline + one CTA; right —
  live-looking candlestick on dark, amber breakout trace, film grain. No stats in hero.
- **Capability strip:** 2-col zig-zag (signals / backtest), not 3 equal cards.
- **Proof:** one honest equity curve, real-feeling messy numbers (e.g. `+18.4%`,
  `47.2% win`), cost-adjusted note. No fake round figures.
- **CTA:** single amber pill, mono subtext `localhost:8000`.

## 5. imagegen-frontend-mobile — companion screen direction

Inside a subtle iPhone frame, app content the focus:

- **Watch screen:** ticker list, mono prices, up-green/down-rose, large balance header.
- **Signal detail:** verdict pill, confidence, agent steps timeline.
- **Consistency:** same Geist/JetBrains, same amber, 44px targets, dark theme default.

## 6. Tagline

Primary: **"Every position under control."**
Mono alt: `nothing random.` (nods to the ML-rigor project memory.)

## 7. image-to-code mapping (already implemented)

The shipped `web/static/index.html` already realizes this direction: breakout-trace
logo, Signal Amber accent, Geist + JetBrains Mono, cockpit density, grain + ambient
depth, film-grain pseudo-element, dark/light parity. New screens must be built to
match this spec and [DESIGN.md](DESIGN.md), not redesigned from scratch.
