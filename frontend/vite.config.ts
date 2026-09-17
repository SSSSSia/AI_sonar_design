import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期代理：/api 转发到 Java 平台层网关（gateway, :8080）。
// UI 改动须先 mockup 确认（宪法 §7.3）；蓝色仅用于交互/可操作语义元素。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
})
