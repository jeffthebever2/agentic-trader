# Niche AI Design Tells v3

These are the subtle patterns that make a website feel obviously AI/vibe-coded even when it looks polished.

## 1. The AI SaaS hero stack

Common pattern:

```txt
small pill badge → massive centered headline → gradient word → vague subheadline → two CTA buttons → floating dashboard mockup
```

Why it feels AI-made:
- It is a layout shortcut, not an information architecture decision.
- It works for almost anything, which means it belongs to nothing.

Human alternative:
- Start with the actual job the user came to do.
- Make the first screen useful, not just impressive.

## 2. Purple/cyan tech candy

Common pattern:

```txt
bg-slate-950 + purple/cyan gradients + blurred blobs + glowing borders
```

Why it feels AI-made:
- AI reaches for “tech” colors without a product reason.
- The same palette appears on AI tools, fake SaaS apps, dashboards, and portfolios.

Human alternative:
- Pick colors from the product domain: radar/status colors, finance risk colors, home-control warmth, aviation instrumentation, etc.

## 3. Glass card overload

Common pattern:

```txt
backdrop-blur bg-white/10 border-white/10 rounded-2xl shadow-xl
```

Why it feels AI-made:
- It creates visual polish without design hierarchy.
- Everything becomes equally shiny and equally unimportant.

Human alternative:
- Build surface levels: base, panel, raised, inset, critical, selected, disabled.

## 4. Lucide icon feature grid

Common pattern:

```txt
3 columns, each card has a round icon, heading, two-line description
```

Why it feels AI-made:
- Icons become decoration instead of function.
- Feature cards all have the same weight.

Human alternative:
- Use different module types based on importance and user flow.
- Replace generic icons with data, controls, screenshots, or domain-specific symbols.

## 5. Motion as filler

Common pattern:

```txt
transition-all duration-300 hover:scale-105 motion.div opacity 0 → 1 y 20
```

Why it feels AI-made:
- Everything moves the same way.
- Motion does not communicate state or consequence.

Human alternative:
- Define motion roles: feedback, navigation, reveal, progress, warning, completion.

## 6. Vague hype copy

Common pattern:

```txt
Unlock seamless powerful workflows with an intuitive next-generation platform.
```

Why it feels AI-made:
- The sentence sounds good but says nothing.

Human alternative:
- Use nouns and verbs from the actual product.
- Say what changes for the user.

## 7. Default component-library smell

Common pattern:

```txt
<Card><CardHeader><CardContent>
<Button variant="default">
<Badge variant="secondary">
```

Why it feels AI-made:
- The component library is visible instead of the product’s design system.

Human alternative:
- Wrap primitives into product components with domain names and distinct anatomy.

## 8. Missing real app states

Common pattern:
- No empty states
- No offline states
- No stale-data state
- No retry state
- No disabled/busy distinction

Why it feels AI-made:
- AI designs the happy path only.

Human alternative:
- Design for reality: loading, empty, partial, stale, degraded, error, retry, disabled, syncing, success.

## 9. Perfect symmetry everywhere

Common pattern:
- Centered everything
- Equal cards
- Equal gaps
- Equal section heights

Why it feels AI-made:
- It avoids hard design decisions.

Human alternative:
- Give important things more weight.
- Use asymmetry and density intentionally.

## 10. Generic tokens

Common pattern:

```css
--background;
--foreground;
--primary;
--secondary;
--accent;
```

Why it feels AI-made:
- The tokens describe UI mechanics, not product meaning.

Human alternative:

```css
--surface-map;
--surface-panel;
--status-live;
--status-stale;
--risk-warning;
--risk-critical;
--focus-ring;
```
