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

export interface NewsItem {
  title: string
  summary: string
  url: string
  source: string
  published: string
}

export interface QuoteDetail {
  symbol: string
  price: number | null
  change: number | null
  change_pct: number | null
  prev_close: number | null
  day_high: number | null
  day_low: number | null
  week52_high: number | null
  week52_low: number | null
  volume: number | null
  avg_volume: number | null
  market_cap: number | null
  pe_ratio: number | null
  short_name: string
  sector: string
  industry: string
}

export const getMarketNews = (symbol: string) =>
  api.get<{ symbol: string; news: NewsItem[] }>(`/market/news?symbol=${encodeURIComponent(symbol)}`)
     .then(r => r.data)

export const getQuoteDetail = (symbol: string) =>
  api.get<QuoteDetail>(`/market/quote-detail?symbol=${encodeURIComponent(symbol)}`)
     .then(r => r.data)
