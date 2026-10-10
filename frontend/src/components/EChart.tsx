/**
 * ECharts 轻封装（ADR-011 仪表盘/报告图表）：
 * 按需注册（Line/Bar/Pie + Grid/Tooltip/Legend + Canvas）控制包体；
 * 深色主题配色与 styles/theme.css token 同源；容器尺寸变化自动 resize。
 * 仅本组件触达 echarts，vendor-echarts 分包随引用页面 lazy 加载。
 */
import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

/** 图表语义色（与 theme.css --accent/--ok/--warn/--err/--info 一致；青色仅表交互/强调序列） */
export const CHART_COLORS = {
  accent: '#06b6d4',
  accent2: '#0ea5e9',
  ok: '#10b981',
  warn: '#f59e0b',
  err: '#ef4444',
  info: '#8b5cf6',
  text3: '#64748b',
  text2: '#94a3b8',
  split: 'rgba(148, 163, 184, .16)',
} as const

interface EChartProps {
  option: EChartsCoreOption
  /** 容器高度（仪表盘/报告卡内固定高度，避免循环依赖自动量测） */
  height?: number | string
  style?: React.CSSProperties
}

export default function EChart({ option, height = 260, style }: EChartProps) {
  const holder = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | undefined>(undefined)

  useEffect(() => {
    if (!holder.current) return
    chart.current = echarts.init(holder.current)
    const observer = new ResizeObserver(() => chart.current?.resize())
    observer.observe(holder.current)
    return () => {
      observer.disconnect()
      chart.current?.dispose()
      chart.current = undefined
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption(option, true)
  }, [option])

  return <div ref={holder} style={{ width: '100%', height, ...style }} />
}
