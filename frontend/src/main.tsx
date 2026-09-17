import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'

// 风格基线（宪法 §7.3）：中性生产力美学（Zotero/Notion 式），
// 拒绝装饰化 UI；蓝色仅用于交互/可操作语义元素，禁止装饰性用蓝。
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#1668dc', // 仅交互语义元素可引用
          borderRadius: 4,
        },
      }}
    >
      <App />
    </ConfigProvider>
  </StrictMode>,
)
