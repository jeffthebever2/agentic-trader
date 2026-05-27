import api from './client'
import type { BacktestResult } from '@/types'

export const getBacktestResults = () =>
  api.get<BacktestResult[]>('/backtest/results').then(r => r.data)
export const getBacktestResult  = (id: string) =>
  api.get<BacktestResult>(`/backtest/results/${id}`).then(r => r.data)
export const screenBacktest     = (body: Record<string, unknown>) =>
  api.post('/backtest/screen', body).then(r => r.data)

export const getHistory         = (page = 1, pageSize = 10) =>
  api.get(`/history?page=${page}&page_size=${pageSize}`).then(r => r.data)
export const getHistoryStats    = () => api.get('/history/stats').then(r => r.data)
export const getTickerHistory   = (ticker: string, date: string) =>
  api.get(`/history/${ticker}/${date}`).then(r => r.data)
export const getScannerTickers  = () =>
  api.get('/scanner/tickers').then(r => r.data)
