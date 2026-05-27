import api from './client'
import type { AdminFlags, RuntimeDiagnostics } from '@/types'

export const getAdminFlags      = () => api.get<AdminFlags>('/admin/flags').then(r => r.data)
export const setAdminFlags      = (flags: AdminFlags) => api.post('/admin/flags', flags)
export const getDiagnostics     = () => api.get<RuntimeDiagnostics>('/admin/runtime/diagnostics').then(r => r.data)
export const startTunnel        = () => api.post('/admin/runtime/tunnel/start')
export const stopTunnel         = () => api.post('/admin/runtime/tunnel/stop')
export const restartWeb         = () => api.post('/admin/runtime/web/restart')
export const exportData         = () => api.post('/admin/export', {}, { responseType: 'blob' })
export const getLogStats        = () => api.get('/logs/stats').then(r => r.data)
export const getLiveVerification = () => api.get('/live/verification').then(r => r.data)
