// antd v5 × React 19 官方兼容补丁（必须在任何 antd 组件渲染前引入一次）
import '@ant-design/v5-patch-for-react-19'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import { queryClient } from './lib/queryClient'
import App from './App'

dayjs.locale('zh-cn')

// 风格基线（宪法 §7.3 + PHASE-004 §2.5）：浅色中性生产力美学；主蓝仅用于交互语义元素。
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#2563EB', borderRadius: 6 } }}>
        <AntdApp>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AntdApp>
      </ConfigProvider>
    </QueryClientProvider>
  </StrictMode>,
)
