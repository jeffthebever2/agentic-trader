import api from './client'

// ── Webull ────────────────────────────────────────────────────────────────
export const getWebullStatus    = () => api.get('/webull/status').then(r => r.data)
export const getWebullAccount   = () => api.get('/webull/account').then(r => r.data)
export const getWebullPositions = () => api.get('/webull/positions').then(r => r.data)
export const getWebullOrders    = (status?: string) =>
  api.get(`/webull/orders${status ? `?status=${status}` : ''}`).then(r => r.data)
export const loginWebull        = (body: Record<string, unknown>) => api.post('/webull/login', body)
export const logoutWebull       = () => api.post('/webull/logout')
export const requestWebullMfa   = (body: Record<string, unknown>) => api.post('/webull/request-mfa', body)
export const refreshWebull      = () => api.post('/webull/refresh')
export const setWebullTradePin  = (pin: string) => api.post('/webull/trade-pin', { pin })

// ── Fidelity ──────────────────────────────────────────────────────────────
export const getFidelityStatus    = () => api.get('/fidelity/status').then(r => r.data)
export const getFidelitySummary   = () => api.get('/fidelity/summary').then(r => r.data)
export const getFidelityPositions = () => api.get('/fidelity/positions').then(r => r.data)
export const fidelityTrade        = (body: Record<string, unknown>) =>
  api.post('/fidelity/trade', body).then(r => r.data)
export const fidelityDebugTrade   = (body: Record<string, unknown>) =>
  api.post('/fidelity/debug-trade', body).then(r => r.data)
export const logoutFidelity       = () => api.post('/fidelity/logout')
