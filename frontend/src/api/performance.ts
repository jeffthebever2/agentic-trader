import api from './client'
import type { PerfMetrics, PerfRow, PerfPosition, PerfCashFlow, PerfDay } from '@/types'

export type PerfRange = '1w' | '1m' | '3m' | 'ytd' | '1y' | 'all'

export const getPerfSummary = (range: PerfRange) =>
  api.get<PerfMetrics>('/performance/summary', { params: { range } }).then(r => r.data)

export const getPerfHistory = (range: PerfRange) =>
  api.get<{ rows: PerfRow[]; range: string }>('/performance/history', { params: { range } }).then(r => r.data)

export const getPerfDay = (date: string) =>
  api.get<PerfDay>(`/performance/day/${date}`).then(r => r.data)

export const getPerfPositions = () =>
  api.get<{ positions: PerfPosition[]; date: string | null; total_value?: number; cash?: number }>('/performance/positions').then(r => r.data)

export const getPerfValidate = () =>
  api.get<{ issues: Array<Record<string, unknown>> }>('/performance/validate').then(r => r.data)

export const getPerfSyncLog = () =>
  api.get<{ log: Array<Record<string, unknown>> }>('/performance/synclog', { params: { limit: 30 } }).then(r => r.data)

export const syncPerf = () =>
  api.post('/performance/sync', null, { timeout: 90_000 }).then(r => r.data)

export const getCashFlows = () =>
  api.get<{ cashflows: PerfCashFlow[] }>('/performance/cashflows').then(r => r.data)

export const addCashFlow = (cf: PerfCashFlow) =>
  api.post('/performance/cashflows', cf).then(r => r.data)

export const deleteCashFlow = (cf: PerfCashFlow) =>
  api.delete('/performance/cashflows', { params: { date: cf.date, kind: cf.kind, amount: cf.amount } }).then(r => r.data)

export const exportUrl = (fmt: 'json' | 'csv', range: PerfRange) =>
  `/api/performance/export?fmt=${fmt}&range=${range}`
