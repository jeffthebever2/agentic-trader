import api from './client'
import type { AppSettings } from '@/types'

export const getSettings  = () => api.get<AppSettings>('/settings').then(r => r.data)
export const saveSettings = (data: Partial<AppSettings>) =>
  api.post<AppSettings>('/settings', data).then(r => r.data)
