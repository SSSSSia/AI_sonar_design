// antd v5 × React 19 官方兼容补丁（必须在任何 antd 组件渲染前引入一次）
import '@ant-design/v5-patch-for-react-19'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { App as AntdApp, ConfigProvider } from 'antd'
import type { ThemeConfig } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import { queryClient } from './lib/queryClient'
import App from './App'
import './styles/theme.css'

dayjs.locale('zh-cn')

// 风格基线（宪法 §7.3 + PHASE-004 §2.5）：浅色中性生产力美学；
// 主蓝仅用于交互语义元素，语义色/灰阶与 docs/mockups/mockup.css 同源。
const theme: ThemeConfig = {
  token: {
    colorPrimary: '#2563eb',
    colorInfo: '#2563eb',
    colorLink: '#2563eb',
    colorSuccess: '#2b8a3e',
    colorWarning: '#d97706',
    colorError: '#c92a2a',
    colorText: '#1f2329',
    colorTextSecondary: '#57606a',
    colorTextTertiary: '#8b949e',
    colorTextQuaternary: '#8b949e',
    colorBorder: '#e5e7eb',
    colorBorderSecondary: '#f0f1f3',
    colorSplit: '#f0f1f3',
    colorBgLayout: '#f5f6f8',
    colorBgContainer: '#ffffff',
    borderRadius: 6,
    fontFamily:
      "-apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', Arial, sans-serif",
    fontSize: 13,
    controlHeight: 30,
    boxShadow: '0 4px 14px rgba(31, 35, 41, .08), 0 1px 3px rgba(31, 35, 41, .05)',
    boxShadowSecondary: '0 8px 24px rgba(31, 35, 41, .10)',
  },
  components: {
    Layout: { siderBg: '#fafbfc', headerBg: '#ffffff', headerHeight: 52, bodyBg: '#f5f6f8' },
    Menu: {
      itemBg: 'transparent',
      itemColor: '#57606a',
      itemHoverBg: '#f0f1f3',
      itemHoverColor: '#1f2329',
      itemSelectedBg: '#eef2fe',
      itemSelectedColor: '#2563eb',
      itemHeight: 34,
      itemMarginInline: 8,
      itemBorderRadius: 6,
    },
    Table: {
      headerBg: '#fcfcfd',
      headerColor: '#57606a',
      headerSplitColor: 'transparent',
      rowHoverBg: '#f7f9fd',
      borderColor: '#f0f1f3',
      cellPaddingBlock: 9,
    },
    Card: { headerFontSize: 13.5 },
    Statistic: { titleFontSize: 12, contentFontSize: 22 },
    Tabs: {
      titleFontSize: 13,
      inkBarColor: '#2563eb',
      itemSelectedColor: '#1f2329',
      itemColor: '#57606a',
      itemHoverColor: '#1f2329',
    },
    Tag: { defaultBg: '#f0f1f3' },
    Badge: { fontSize: 11 },
    Button: { primaryShadow: 'none', defaultShadow: 'none', dangerShadow: 'none' },
    Progress: { remainingColor: '#edeff2' },
    Input: { activeShadow: '0 0 0 2px rgba(37, 99, 235, .12)' },
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
