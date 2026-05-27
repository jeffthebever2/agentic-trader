# Anti-AI Design Fix Prompt v3

Use this audit to make the UI look less AI-generated and more intentionally designed.

Input files to read:
- `ANTI_AI_DESIGN_AUDIT_V3.md`
- `anti-ai-score-v3.json`
- `anti-ai-findings-v3.csv`
- `NICHE_AI_DESIGN_TELLS.md`
- `HUMAN_DESIGN_REWRITE_BRIEF.md`

Current audit result:
- AI design risk: `86.0/100`
- Verdict: `OBVIOUSLY AI / TEMPLATE-CODED`

## Instructions

Do **not** redesign by adding more gradients, glassmorphism, blobs, or hover-scale animations.

First write a plan to `ui-updates.md`. Do not code until the plan exists.

The plan must include:
1. The current AI-looking design tells found in the repo
2. The chosen human design direction
3. Color system replacement
4. Surface/card replacement
5. Layout rhythm changes
6. Component anatomy changes
7. Typography changes
8. Motion/microinteraction rules
9. Loading/empty/error/offline/disabled state improvements
10. Accessibility improvements
11. Exact files to change
12. Files and logic not to touch

## Anti-AI design requirements

Avoid:
- Purple/blue/cyan gradient hero sections
- Gradient text as the main visual identity
- Glass cards and `backdrop-blur` everywhere
- Floating blurred blobs/orbs
- `max-w-7xl mx-auto` on every section
- Hero badge + huge headline + two CTA buttons
- Three-card feature grids with Lucide icons
- Default shadcn cards/buttons/badges with no brand layer
- `transition-all duration-300`
- Hover-scale on every card
- Framer Motion fade-up/stagger animation spam
- Generic copy like “unlock,” “seamless,” “powerful,” “revolutionize,” “next-generation”

Do instead:
- Make the interface fit the product domain
- Use color for status, priority, affordance, and brand memory
- Create a real surface system: base, panel, raised, inset, critical, interactive
- Use asymmetry, density changes, and section rhythm intentionally
- Replace generic cards with domain-specific modules
- Use motion for state changes, feedback, navigation, and attention only
- Add reduced-motion support
- Add real app states: loading, empty, error, offline, stale, disabled, syncing
- Add focus-visible styles and keyboard-friendly controls

## Verification

After changes:
- Run the app locally
- Check mobile and desktop
- Check auth/data loading still works
- Check no broken routes
- Re-run the auditor and reduce AI design risk by at least 30%
