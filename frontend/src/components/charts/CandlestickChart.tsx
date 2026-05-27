import { useRef, useEffect } from 'react'
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts'
import type { MarketChart } from '@/types'

interface CandlestickChartProps {
  data: MarketChart
  height?: number
  showVolume?: boolean
}

export function CandlestickChart({ data, height = 260, showVolume = true }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container || !data?.dates?.length) return

    const isDark = document.body.classList.contains('theme-dark')
    const bgColor  = isDark ? '#1a1917' : '#F5F4F1'
    const textColor = isDark ? '#b0a99f' : '#736C61'
    const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)'

    const chart = createChart(container, {
      width: container.clientWidth,
      height,
      layout: {
        background: { color: bgColor },
        textColor,
      },
      grid: {
        vertLines: { color: gridColor },
        horzLines: { color: gridColor },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: 'transparent' },
      timeScale: { borderColor: 'transparent', timeVisible: true },
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#4ade80',
      downColor: '#f87171',
      borderUpColor: '#4ade80',
      borderDownColor: '#f87171',
      wickUpColor: '#4ade80',
      wickDownColor: '#f87171',
    })

    const candleData = data.dates.map((d, i) => ({
      time: d.slice(0, 10) as unknown as import('lightweight-charts').UTCTimestamp,
      open: data.open[i] ?? 0,
      high: data.high[i] ?? 0,
      low: data.low[i] ?? 0,
      close: data.close[i] ?? 0,
    }))
    candleSeries.setData(candleData)

    if (showVolume && data.volume?.length) {
      const volSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
      })
      chart.priceScale('volume').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      })
      const volData = data.dates.map((d, i) => ({
        time: d.slice(0, 10) as unknown as import('lightweight-charts').UTCTimestamp,
        value: data.volume[i] ?? 0,
        color: (data.close[i] ?? 0) >= (data.open[i] ?? 0) ? '#4ade8044' : '#f8717144',
      }))
      volSeries.setData(volData)
    }

    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth })
    })
    ro.observe(container)

    return () => {
      ro.disconnect()
      chart.remove()
    }
  }, [data, height, showVolume])

  return (
    <div style={{ position: 'relative', width: '100%' }}>
      <div ref={containerRef} style={{ height }} />
    </div>
  )
}
