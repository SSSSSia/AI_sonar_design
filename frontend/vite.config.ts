import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// 开发期代理：/api → Java 网关（8080）；/ws → notify 推送服务（8084，路径前缀保持 /ws 与生产 nginx 一致）。
// UI 改动须先 mockup 确认（宪法 §7.3）；蓝色仅用于交互/可操作语义元素。
export default defineConfig({
  plugins: [react()],
  build: {
    // T12.6（P2-8）手动分包：重库独立 vendor 包（随路由 lazy 按需加载），主包只留框架。
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules')) {
            if (id.includes('three')) return 'vendor-three'
            if (id.includes('d3-') || id.includes('/d3/')) return 'vendor-d3'
            if (id.includes('antd') || id.includes('@ant-design') || id.includes('rc-')) return 'vendor-antd'
            if (id.includes('react') || id.includes('scheduler')) return 'vendor-react'
            return 'vendor-misc'
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    // 5173 被占即报错退出，禁止顺延 5174/5175——全栈文档/插件/WS 代理都只认 5173，
    // 静默换端口会造出多个并存的 vite 进程（2026-09-24 实发：5173/5174/5175 三个并存）。
    strictPort: true,
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
