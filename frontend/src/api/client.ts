import axios, { type AxiosError } from 'axios'

const api = axios.create({
  baseURL: '/api',
  // 60s default — Playwright-backed broker endpoints (positions/summary scrape)
  // routinely take 20-40s. Order placement overrides this to 180s per-call.
  timeout: 60_000,
  withCredentials: true,   // preserve session cookies (same as old fetch)
})

// ── Response interceptor: surface error messages ──────────────────────────
api.interceptors.response.use(
  res => res,
  (err: AxiosError<{ detail?: string; error?: string }>) => {
    const detail =
      err.response?.data?.detail ??
      err.response?.data?.error ??
      err.message ??
      'Unknown error'
    // Re-throw with a cleaner message
    const enhanced = new Error(detail) as Error & { status?: number }
    enhanced.status = err.response?.status
    return Promise.reject(enhanced)
  },
)

export default api

// ── Helper: build WebSocket URL ───────────────────────────────────────────
export function wsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host  = import.meta.env.DEV ? 'localhost:8001' : window.location.host
  return `${proto}//${host}/api${path}`
}
