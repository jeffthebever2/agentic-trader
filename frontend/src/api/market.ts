import api from './client'
import type { MarketChart, Quote } from '@/types'

export const getMarketChart = (symbol: string, period = '60d', interval = '1h') =>
  api.get<MarketChart>(`/market/chart?symbol=${encodeURIComponent(symbol)}&period=${period}&interval=${interval}`)
     .then(r => r.data)

export const getQuotes = (tickers: string[]) =>
  api.get<Record<string, Quote>>(`/market/quotes?tickers=${tickers.join(',')}`)
     .then(r => r.data)

export const getSparklines = (tickers: string[]) =>
  api.get(`/market/sparklines?tickers=${tickers.join(',')}`)
     .then(r => r.data)
