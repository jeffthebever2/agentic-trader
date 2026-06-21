# Agentic Trader — Frontend

The dashboard SPA for [Agentic Trader](../README.md). React 19 + Vite + TypeScript,
served by the FastAPI backend under the base path **`/app`**.

## Stack

- **React 19** + **TypeScript** (strict)
- **Vite** build → outputs to **`../web/static/dist`** (served live by the backend)
- **React Router** (`BrowserRouter basename="/app"`)
- **TanStack Query** (server state) + **Zustand** (client state)
- **Tailwind CSS v4** (`@tailwindcss/vite`), design tokens in `src/styles/tokens.css`
- Charts: `lightweight-charts`, `chart.js`, `recharts`
- Forms: `react-hook-form` + `zod`

## Commands

```bash
npm install
npm run build     # tsc -b && vite build → ../web/static/dist  (MUST be type-clean to ship)
npm run dev       # vite dev server on :5173, proxies /api · /health · /ws → localhost:8001
npm run lint
```

After `npm run build`, the backend serves the new bundle immediately — just hard-refresh
the browser (no server restart needed). The dev server (`npm run dev`) is for fast HMR
iteration and talks to a running backend on `:8001`.

## Layout

```
src/
  pages/        One folder per route: Dashboard, Broker, HIL, ThematicPortfolio,
                Performance, ML, Analyze, Backtest, Signals, History, Logs, RL,
                Admin, Settings, Privacy, Terms
  api/          Typed API clients (axios) for the backend routers
  components/   Shared UI (layout, modals, charts)
  store/        Zustand stores (theme, …)
  styles/       tokens.css + Tailwind layer
  types/        Shared TypeScript types
```

## Notes

- `tsc -b` is strict — implicit `any` / untyped code fails the build (and the deploy).
- The backend mounts this build at `/app`; API calls go to `/api/*` on the same origin
  (see [`../docs/api.md`](../docs/api.md)).
