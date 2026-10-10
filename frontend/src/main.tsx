// antd v5 × React 19 官方兼容补丁（必须在任何 antd 组件渲染前引入一次）
import '@ant-design/v5-patch-for-react-19'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { App as AntdApp, ConfigProvider, theme as antdTheme } from 'antd'
import type { ThemeConfig } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import { queryClient } from './lib/queryClient'
import App from './App'
import './styles/theme.css'

dayjs.locale('zh-cn')

// 风格基线（宪法 §7.3 + ADR-011，2026-10-10 起）：深色海洋科技风；
// 交互 accent 青 #06b6d4 仅用于可操作语义元素，语义四色不用于装饰。
// token 与 docs/mockups/mockup.css 及 styles/theme.css 同源。
const theme: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: '#06b6d4',
    colorInfo: '#06b6d4',
    colorLink: '#06b6d4',
    colorSuccess: '#10b981',
    colorWarning: '#f59e0b',
    colorError: '#ef4444',
    colorText: '#e2e8f0',
    colorTextSecondary: '#94a3b8',
    colorTextTertiary: '#64748b',
    colorTextQuaternary: '#475569',
    colorBorder: 'rgba(148, 163, 184, .30)',
    colorBorderSecondary: 'rgba(148, 163, 184, .14)',
    colorSplit: 'rgba(255, 255, 255, .05)',
    colorBgLayout: '#070f1e',
    colorBgContainer: '#0f2035',
    colorBgElevated: '#14263f',
    borderRadius: 6,
    fontFamily:
      "-apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', Arial, sans-serif",
    fontSize: 13,
    controlHeight: 30,
    boxShadow: '0 4px 14px rgba(0, 0, 0, .4), 0 1px 3px rgba(0, 0, 0, .3)',
    boxShadowSecondary: '0 8px 24px rgba(0, 0, 0, .5)',
  },
  components: {
    Layout: { siderBg: 'transparent', headerBg: 'rgba(7, 15, 30, .9)', headerHeight: 52, bodyBg: '#070f1e' },
    Menu: {
      itemBg: 'transparent',
      itemColor: '#94a3b8',
      itemHoverBg: 'rgba(255, 255, 255, .05)',
      itemHoverColor: '#e2e8f0',
      itemSelectedBg: 'rgba(6, 182, 212, .14)',
      itemSelectedColor: '#06b6d4',
      itemHeight: 34,
      itemMarginInline: 8,
      itemBorderRadius: 6,
    },
    Table: {
      headerBg: '#0a1628',
      headerColor: '#94a3b8',
      headerSplitColor: 'transparent',
      rowHoverBg: 'rgba(6, 182, 212, .06)',
      borderColor: 'rgba(255, 255, 255, .05)',
      cellPaddingBlock: 9,
    },
    Card: { headerFontSize: 13.5 },
    Statistic: { titleFontSize: 12, contentFontSize: 22 },
    Tabs: {
      titleFontSize: 13,
      inkBarColor: '#06b6d4',
      itemSelectedColor: '#e2e8f0',
      itemColor: '#94a3b8',
      itemHoverColor: '#e2e8f0',
    },
    Tag: { defaultBg: 'rgba(148, 163, 184, .12)', defaultColor: '#94a3b8' },
    Badge: { fontSize: 11 },
    Button: {
      primaryShadow: '0 0 12px rgba(6, 182, 212, .30)',
      defaultShadow: 'none',
      dangerShadow: 'none',
    },
    Progress: { remainingColor: 'rgba(255, 255, 255, .08)' },
    Input: { activeShadow: '0 0 0 2px rgba(6, 182, 212, .18)' },
  },
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={theme}>
        <AntdApp>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AntdApp>
      </ConfigProvider>
    </QueryClientProvider>
  </StrictMode>,
)
