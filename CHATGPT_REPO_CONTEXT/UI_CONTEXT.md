# UI Context

## Real Frontend/UI Files
The primary frontend code for the application is located in `web/static/`. The core files driving the user interface are:
- `web/static/index.html`: The main dashboard page structure and markup.
- `web/static/premium-static-ui.css`: Custom stylesheet overriding defaults to define the visual language.
- `web/static/premium-static-ui.js`: The application logic, handling API calls, state updates, chart rendering, and DOM manipulation.

## Main Page Structure
`index.html` serves as a Single Page Application (SPA) dashboard. It generally consists of:
- A navigation header or sidebar.
- A hero/summary section displaying portfolio balances and global metrics.
- Data grids/cards for active positions, open orders, and historical trades.
- Charting containers for displaying asset price history and backtest performance.
- Settings/Configuration modals or sections.

## Important Element IDs/Classes Used by JS
The JavaScript (`premium-static-ui.js`) relies heavily on specific DOM IDs to bind data and instantiate charts. While changing styles, **do not change the `id` attributes** of data containers such as:
- Chart canvas IDs (e.g., `#portfolioChart`, `#priceChart`).
- Data table bodies (e.g., `#positionsTableBody`, `#ordersTableBody`).
- Form inputs and submit buttons used for settings or trade execution.
- Alert/notification containers.

## Files Safe to Edit
These files contain the custom logic and presentation layer and should be targeted for redesigns:
- `web/static/index.html`
- `web/static/premium-static-ui.css`
- `web/static/premium-static-ui.js`

## Files Not Safe to Edit (Vendor/Minified Files to Ignore)
Do not modify these vendor dependencies. They are third-party libraries:
- `web/static/tailwind.min.css`
- `web/static/chart.umd.min.js`
- `web/static/chartjs-adapter-date-fns.min.js`
- `web/static/chartjs-financial.min.js`
- `web/static/marked.min.js`

Any other `.min.js`, `.min.css`, or asset files (like `agentic-trader-icon.png`) should be left as-is unless explicitly replacing an asset.
