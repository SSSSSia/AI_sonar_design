import axios from 'axios'
import { useAuthStore } from '../stores/authStore'

/** 平台统一响应包（宪法 §5.3）：{ code, message, data, traceId } */
export class ApiError extends Error {
  code: number
  constructor(code: number, message: string) {
    super(message)
    this.code = code
  }
}

export const http = axios.create({ baseURL: '/api/v1', timeout: 30_000 })

http.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

function handleUnauthorized() {
  useAuthStore.getState().clear()
  if (location.pathname !== '/login') {
    if (onUnauthorized) onUnauthorized()
    else location.href = '/login'
  }
}

/** 401 后的跳转（可注入以便测试；缺省 location 跳 /login）。 */
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

http.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code !== 0) {
        if (body.code === 401) handleUnauthorized()
        return Promise.reject(new ApiError(body.code, body.message ?? '请求失败'))
      }
      resp.data = body.data
    }
    return resp
  },
  (error) => {
    const status: number | undefined = error.response?.status
    if (status === 401) handleUnauthorized()
    const message = error.response?.data?.message ?? error.message ?? '网络错误'
    return Promise.reject(new ApiError(status ?? -1, message))
  },
)

export async function get<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  return (await http.get<T>(url, { params })).data
}

/** 服务可达性探测专用：短超时（审查 P0-2），失败即如实标「不可达」，不悬挂 30s。 */
export async function probeGet<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  return (await http.get<T>(url, { params, timeout: 5_000 })).data
}

export async function post<T>(url: string, data?: unknown): Promise<T> {
  return (await http.post<T>(url, data)).data
}

export async function patch<T>(url: string, data?: unknown): Promise<T> {
  return (await http.patch<T>(url, data)).data
}

export async function del<T>(url: string): Promise<T> {
  return (await http.delete<T>(url)).data
}
