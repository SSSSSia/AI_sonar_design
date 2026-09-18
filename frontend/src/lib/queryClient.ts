import { QueryClient } from '@tanstack/react-query'

/**
 * 全局 QueryClient（模块级导出，供 WS 事件做缓存失效对账）。
 * query key 约定见 PHASE-004 §2.7。
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5_000,
      refetchOnWindowFocus: false,
    },
  },
})
