import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// 开发期代理：/api → Java 网关（8080）；/ws → notify 推送服务（8084，路径前缀保持 /ws 与生产 nginx 一致）。
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
      '/ws': {
        target: 'http://localhost:8084',
        ws: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
