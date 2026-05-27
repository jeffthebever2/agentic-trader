import { useRef, useEffect } from 'react'
import {
  Chart,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Filler,
  Tooltip,
  Legend,
  CategoryScale,
} from 'chart.js'

Chart.register(
  LineController, LineElement, PointElement,
  LinearScale, TimeScale, Filler, Tooltip, Legend, CategoryScale,
)

interface Dataset {
  label: string
  data: Array<{ x: string | number; y: number }>
  color: string
  fill?: boolean
}

interface LineChartProps {
  datasets: Dataset[]
  height?: number
  showLegend?: boolean
  yFormatter?: (v: number) => string
}

export function LineChart({ datasets, height = 200, showLegend = false, yFormatter }: LineChartProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef  = useRef<Chart<'line'> | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    if (chartRef.current) chartRef.current.destroy()

    const isDark = document.body.classList.contains('theme-dark')
    const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)'
    const tickColor = isDark ? '#7C8493' : '#736C61'

    chartRef.current = new Chart(canvas, {
      type: 'line',
      data: {
        datasets: datasets.map(d => ({
          label: d.label,
          data:  d.data,
          borderColor: d.color,
          backgroundColor: d.fill ? `${d.color}18` : 'transparent',
          fill: d.fill ?? false,
          tension: 0.3,
          pointRadius: d.data.length < 50 ? 2 : 0,
          borderWidth: 2,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: showLegend },
          tooltip: {
            callbacks: {
              label: ctx => {
                const val: number = ctx.parsed.y ?? 0
                return yFormatter ? yFormatter(val) : val.toFixed(2)
              },
            },
          },
        },
        scales: {
          x: {
            grid: { color: gridColor },
            ticks: { color: tickColor, maxRotation: 0, font: { size: 11 } },
          },
          y: {
            grid: { color: gridColor },
            ticks: {
              color: tickColor,
              font: { size: 11 },
              callback: v => yFormatter ? yFormatter(v as number) : v,
            },
          },
        },
      },
    }) as unknown as Chart<'line'>

    return () => { chartRef.current?.destroy() }
  }, [datasets])

  return <canvas ref={canvasRef} style={{ height, width: '100%' }} />
}
