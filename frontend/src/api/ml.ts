import api from './client'
import type { MLStatus } from '@/types'

export const getMlStatus = () => api.get<MLStatus>('/ml/status').then(r => r.data)
export const getRlStatus = () => api.get('/rl/status').then(r => r.data)
