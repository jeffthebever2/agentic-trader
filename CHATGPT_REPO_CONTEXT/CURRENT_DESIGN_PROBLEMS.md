# Current Design Problems

An automated Anti-AI Design Audit has flagged this repository's UI as **OBVIOUSLY AI / TEMPLATE-CODED** (Score: 86.0/100 AI Risk). The design relies on generic, overused patterns rather than domain-specific, authored aesthetics.

## Exact Files Causing It
- `web/static/index.html` (Primary offender)
- `web/static/tailwind.min.css` (Generic glassmorphism/utility usage)
- `web/static/premium-static-ui.css`

## Repeated Visual Patterns
- **shadcn/ui Default Gravity:** The layout falls into the predictable, out-of-the-box shadcn/ui aesthetic without any unique brand system.
- **Pill Badges:** Overuse of "rounded pill badge eyebrow before hero" patterns.
- **Card Primitives:** Repeated use of generic card primitives across all sections.

## Bad Colors / Surfaces
- **Default Dark SaaS Palette:** Usage of standard generic dark mode backgrounds.
- **Primary/Secondary/Accent Language:** Color tokens are abstract (primary/secondary) rather than domain-specific (e.g., profit/loss/neutral).
- **Glassmorphism Clichés:** Glass card stacks with `border-white/10` and blurred purple/cyan blobs behind everything without a product reason.
- **Noise/Grid Overlays:** Applied universally without serving a functional or aesthetic purpose specific to trading.

## Bad Layout Patterns
- **3-Card Feature Grid Formula:** Sections default to 3-card grids with Lucide icons enclosed in circles.
- **Repetitive Constraints:** `max-w-7xl mx-auto` repeated identically across every section, creating a monotonous vertical rhythm.
- **Landing Page Boilerplate:** The app structure mimics a generic marketing landing page (hero + features + pricing + FAQ) instead of a functional trading terminal.

## Generic Copy
- Heavy use of vague, hype-driven SaaS copywriting instead of domain-specific language.
- Smell words include: *"unlock"*, *"seamless"*, *"powerful"*, *"revolutionize"*, and *"at your fingertips"*.

## Weak Interaction / Motion Patterns
- **One-Size-Fits-All Motion:** `transition-all duration-300` and hover-scale applied lazily to almost every interactive element.
- **Scroll Reveals:** Overuse of "reveal-on-view" tropes or Framer Motion fade-up/stagger animations that serve no product reason.
- **Lack of Depth:** Missing focus states, reduced-motion support, and real product states (loading, error, empty, offline).
