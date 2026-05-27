# Anti-AI Design Audit v3

Generated: `2026-05-26T13:55:57.994800+00:00`  
Target: `.`  
Mode: `repo`  
Strictness: `3`

## Overall score

- **AI design risk:** `86.0/100`
- **Human design score:** `14.0/100`
- **Verdict:** **OBVIOUSLY AI / TEMPLATE-CODED**

## Category scores

|Category|Risk points|Cap|Filled|
|---|---|---|---|
|Composition and layout rhythm|30|30|100%|
|Component/library clichés|30|30|100%|
|Palette & color tells|28|28|100%|
|Surface, glow, blur, glass|28|28|100%|
|Motion and animation taste|26|26|100%|
|Copywriting AI smell|24|24|100%|
|Domain specificity vs template UI|20|20|100%|
|Typography decisions|18|18|100%|
|Iconography and decoration|16|16|100%|
|State depth and product reality|0.0|22|0%|
|Accessibility basics|0.0|22|0%|
|Design system maturity|0.0|24|0%|
|Repo hygiene / scan quality|0.0|14|0%|

## Scan health

- Scanned files: `65`
- Scanned bytes: `10151155`
- Domain-specific noun hits: `44`
- Motion found: `True`
- Reduced-motion support found: `True`
- Focus system found: `True`
- Real app states found: `True`
- Semantic tokens found: `True`

## Top offending files

|File|Risk points|
|---|---|
|web/static/index.html|536.32|
|web/static/tailwind.min.css|112.08|
|.agents/skills/impeccable/scripts/live-browser.js|70.98|
|.agents/skills/impeccable/scripts/command-metadata.json|65.06|
|web/static/chart.umd.min.js|60.46|
|web/static/premium-static-ui.css|55.97|
|.agents/skills/impeccable/scripts/modern-screenshot.umd.js|46.83|
|anti-ai-brutal-audit/design-smell-rubric-v3.json|40.06|
|.agents/skills/impeccable/scripts/live-wrap.mjs|32.67|
|.github/workflows/codeql.yml|27.62|
|.agents/skills/impeccable/scripts/live.mjs|27.02|
|web/static/chartjs-financial.min.js|23.49|
|web/static/premium-static-ui.js|17.18|
|.agents/skills/impeccable/scripts/live-server.mjs|16.26|
|.agents/skills/impeccable/scripts/cleanup-deprecated.mjs|7.61|
|backtest_results_20260517_162811.json|6.35|
|backtest_results_20260517_170131.json|6.35|
|backtest_results_20260517_172848.json|6.35|
|.agents/skills/impeccable/scripts/live-session-store.mjs|3.46|
|.agents/skills/impeccable/scripts/live-poll.mjs|2.36|

## Highest-value findings

|Severity|Category|Finding|Points|File|
|---|---|---|---|---|
|critical|Component/library clichés|shadcn/ui default gravity|85.49|web/static/index.html|
|critical|Composition and layout rhythm|3-card feature grid formula|45.74|web/static/index.html|
|critical|Surface, glow, blur, glass|Glassmorphism recipe|41.83|web/static/tailwind.min.css|
|high|Component/library clichés|Rounded pill badge eyebrow before hero|41.54|web/static/index.html|
|high|Component/library clichés|Rounded pill badge eyebrow before hero|30.65|web/static/chart.umd.min.js|
|high|Copywriting AI smell|Landing-page section boilerplate|29.93|web/static/index.html|
|critical|Copywriting AI smell|Generic AI/SaaS hype copy|29.88|web/static/index.html|
|high|Palette & color tells|Default dark SaaS background palette|28.58|web/static/index.html|
|high|Component/library clichés|Rounded pill badge eyebrow before hero|26.43|web/static/premium-static-ui.css|
|medium|Component/library clichés|Default card primitives repeated|25.43|web/static/index.html|
|critical|Motion and animation taste|transition-all duration-300 default everywhere|23.0|web/static/index.html|
|medium|Palette & color tells|Primary/secondary/accent-only color language|22.6|web/static/index.html|
|critical|Composition and layout rhythm|Niche AI-template combo detected: shadcn_default_dark_saas|22.5|<repo-wide>|
|critical|Copywriting AI smell|Generic AI/SaaS hype copy|22.02|.agents/skills/impeccable/scripts/command-metadata.json|
|critical|Motion and animation taste|transition-all duration-300 default everywhere|20.0|anti-ai-brutal-audit/design-smell-rubric-v3.json|
|critical|Component/library clichés|shadcn/ui default gravity|20.0|.agents/skills/impeccable/scripts/live-wrap.mjs|
|medium|Motion and animation taste|Scroll reveal / reveal-on-view trope|18.99|web/static/index.html|
|critical|Copywriting AI smell|Generic AI/SaaS hype copy|17.71|.agents/skills/impeccable/scripts/live.mjs|
|medium|Motion and animation taste|One-size-fits-all easing/duration|17.44|web/static/index.html|
|high|Component/library clichés|Rounded pill badge eyebrow before hero|17.15|.agents/skills/impeccable/scripts/live-browser.js|
|high|Domain specificity vs template UI|Template nouns dominate over domain nouns|16.48|web/static/index.html|
|high|Palette & color tells|Default dark SaaS background palette|16.25|web/static/tailwind.min.css|
|medium|Motion and animation taste|Scroll reveal / reveal-on-view trope|15.4|.agents/skills/impeccable/scripts/live-browser.js|
|medium|Surface, glow, blur, glass|Noise/grid overlay without product reason|15.37|web/static/index.html|
|medium|Copywriting AI smell|Vague benefit nouns instead of domain language|15.21|web/static/chart.umd.min.js|

## What v3 is stricter about

This version catches niche AI tells that normal audits miss:

- Hero badge + gradient text + two CTA buttons
- Blurred purple/cyan blobs behind everything
- Glass card stacks with `border-white/10`
- `max-w-7xl mx-auto` repeated across every section
- 3-card feature grids with Lucide icons in circles
- shadcn defaults that were never turned into a brand system
- `transition-all duration-300` and hover-scale on everything
- Framer Motion fade-up/stagger animations with no product reason
- Copy like “unlock,” “seamless,” “powerful,” “revolutionize,” and “at your fingertips”
- Missing reduced-motion support, focus states, and real loading/error/empty/offline states
- Generic `primary/secondary/accent` tokens instead of domain-aware tokens

## Fix strategy

1. **Choose a domain-specific art direction.** Do not use a generic SaaS template.
2. **Replace decorative gradients with purposeful surfaces.** Use color for hierarchy, status, and navigation.
3. **Break the landing-page rhythm.** Avoid hero + features + pricing + FAQ unless the product actually needs it.
4. **Make components feel authored.** Buttons, cards, nav, inputs, and badges should have project-specific anatomy.
5. **Use motion only for feedback, state changes, or orientation.** Remove filler hover-scale/reveal animations.
6. **Write product-specific copy.** Replace hype words with concrete nouns and actions.
7. **Add product states.** Loading, empty, error, offline, disabled, stale, and retry states make the UI feel real.
8. **Add accessibility polish.** Focus states and reduced-motion support are non-negotiable.

