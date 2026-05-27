import api from './client'
import type { PaperStatus, PaperEquityPoint, PaperAnalytics, HilPending } from '@/types'

export const getPaperStatus  = () => api.get<PaperStatus>('/paper/status').then(r => r.data)
// API returns { strategy: rows[] } — normalize to flat array with .strategy injected
export const getPaperEquity = (): Promise<PaperEquityPoint[]> =>
  api.get<Record<string, PaperEquityPoint[]>>('/paper/equity').then(r => {
    const obj = r.data ?? {}
    return Object.entries(obj).flatMap(([strategy, rows]) =>
      (Array.isArray(rows) ? rows : []).map(row => ({ ...row, strategy }))
    )
  })
export const getPaperAnalytics = () => api.get<PaperAnalytics>('/paper/analytics').then(r => r.data)
export const getPaperSystemHealth = () => api.get('/paper/system-health').then(r => r.data)
export const getPaperAutostart = () => api.get('/paper/autostart').then(r => r.data)
export const setPaperAutostart = (enabled: boolean) => api.post('/paper/autostart', { enabled })
export const getPaperBacktestIndex = () => api.get('/paper/backtest-index').then(r => r.data)

export const getCandidatesHistory = (days = 7, limit = 150) =>
  api.get(`/paper/candidates-history?days=${days}&limit=${limit}`).then(r => r.data)

export const getPaperQuotes = (tickers: string[]) =>
  api.get(`/paper/quotes?tickers=${tickers.join(',')}`).then(r => r.data)

export const startPaperRunner  = (body: Record<string, unknown>) =>
  api.post('/paper/start', body).then(r => r.data)
export const stopPaperRunner   = () => api.post('/paper/stop').then(r => r.data)

export const getHilPending = () => api.get<HilPending[]>('/paper/hil/pending').then(r => r.data)
export const resolveHil    = (id: string, action: 'approve' | 'reject') =>
  api.post('/paper/hil/resolve', { id, action })

export const testEmail = () => api.post('/paper/email/test')
export const getSmsStatus = () => api.get('/paper/sms/status').then(r => r.data)
export const testSms = (number: string) => api.post('/paper/sms/test', { number })
