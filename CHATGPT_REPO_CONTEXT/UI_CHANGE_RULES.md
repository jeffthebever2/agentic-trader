# UI Change Rules

When applying redesigns or updating the frontend, strictly adhere to the following rules to eliminate AI-generated "smells" and ensure a high-end, human-authored feel.

## What Must Not Break
- **Application Logic:** Do not break the data fetching, websocket connections, or state management in `premium-static-ui.js`.
- **Core Layout Integrity:** The dashboard must remain functional for its primary use case (trading, monitoring, backtesting).

## What IDs/Selectors Must Be Preserved
- **Data Bindings:** All `id` attributes on data tables, chart canvases (`<canvas id="...">`), forms, and notification containers must remain exactly as they are so `premium-static-ui.js` can find them.
- **Data Attributes:** Preserve any `data-*` attributes used for state tracking or DOM querying.

## How to Change CSS Safely
- **Authored Styles:** Move away from relying purely on generic Tailwind utility strings (`bg-gray-900 border border-gray-800 rounded-xl`). Write custom, authored CSS in `premium-static-ui.css` for complex components.
- **Purposeful Surfaces:** Replace decorative gradients and blurred blobs with purposeful surfaces. Use color strictly for hierarchy, status, and navigation.
- **Domain-Specific Tokens:** Rename abstract color variables (e.g., `--primary`) to domain-specific tokens (e.g., `--color-bullish`, `--color-bearish`, `--surface-panel`).

## How to Change JS Safely
- **Add Real States:** Implement discrete states for Loading, Empty, Error, Offline, Disabled, Stale, and Retry. This makes the UI feel like a real application.
- **Event Listeners:** When modifying DOM structure, ensure existing event listeners in `premium-static-ui.js` still attach correctly (e.g., don't change a `<button id="submit">` to a `div`).

## How to Avoid Generic AI-Looking Design
- **Break the Rhythm:** Avoid the standard "Hero + 3-Card Grid + FAQ" layout. Design a layout suited for dense data, such as a terminal, bento grid, or multi-pane dashboard.
- **Remove Lazy Motion:** Strip out universal `transition-all duration-300` and `hover:scale-105` classes. Use motion *only* for feedback, state changes, or orientation.
- **Concrete Copywriting:** Replace AI hype words ("seamless", "revolutionize", "powerful") with concrete, domain-specific nouns and direct actions.
- **No Glass/Blobs:** Remove `border-white/10`, backdrop blurs, and background glow blobs unless specifically directed for a specific aesthetic.

## How to Verify UI Changes
- **Accessibility Check:** Ensure all interactive elements have explicit focus states (e.g., `focus-visible:ring`).
- **Reduced Motion:** Ensure animations respect `@media (prefers-reduced-motion)`.
- **Review Against Audit:** Cross-reference changes against `CURRENT_DESIGN_PROBLEMS.md` to guarantee no AI tropes were reintroduced.
