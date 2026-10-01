import axios from 'axios'
import type { AxiosRequestConfig } from 'axios'
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

// ---- T9.4（PHASE-009）429/503 幂等 GET 指数退避 ----
/** 退避参数可注入（测试改小延迟）；POST/审阅类写操作一律不自动重试（防重复裁决）。 */
export const retryConfig = {
  delaysMs: [1000, 2000, 4000],
  jitterRatio: 0.2, // 每档延迟附加 0–20% 随机抖动，防同步重试风暴
}

/** 拦截器错误侧的结构形状（axios 内部 error 的宽松视图） */
interface HttpErrorLike {
  config?: AxiosRequestConfig & { __retryCount?: number }
  response?: {
    status?: number
    headers?: Record<string, unknown>
    data?: { message?: unknown; retryAfterSeconds?: unknown }
  }
  message?: string
}

function retryDelayMs(retryCount: number): number {
  const delays = retryConfig.delaysMs
  const base = delays[Math.min(retryCount, delays.length - 1)]
  return base + Math.random() * base * retryConfig.jitterRatio
}

/** 从 429/503 错误响应提取服务端建议等待（Retry-After 头或 body.retryAfterSeconds，秒）。 */
function retryAfterSeconds(error: HttpErrorLike): number | null {
  const headers = error.response?.headers ?? {}
  const headerVal = String(headers['retry-after'] ?? headers['Retry-After'] ?? '')
  const headerNum = Number(headerVal)
  if (Number.isFinite(headerNum) && headerNum > 0) return headerNum
  const bodyNum = Number(error.response?.data?.retryAfterSeconds)
  if (Number.isFinite(bodyNum) && bodyNum > 0) return bodyNum
  return null
}

function toApiError(error: HttpErrorLike): ApiError {
  const status: number | undefined = error.response?.status
  if (status === 401) handleUnauthorized()
  const message = String(error.response?.data?.message ?? error.message ?? '网络错误')
  return new ApiError(status ?? -1, message)
}

/** 429/503 时幂等 GET 指数退避重试（最多 delaysMs.length 次，尊重服务端 retryAfterSeconds）；
 * 其余状态与所有写操作（POST/PATCH/DELETE）直接转 ApiError 不自动重试（防重复裁决）。 */
async function retryIdempotentGet(error: HttpErrorLike): Promise<unknown> {
  const cfg = error.config
  const status = error.response?.status
  const method = (cfg?.method ?? '').toLowerCase()
  if (!cfg || (status !== 429 && status !== 503) || method !== 'get') {
    return Promise.reject(toApiError(error))
  }
  const retryCount = cfg.__retryCount ?? 0
  if (retryCount >= retryConfig.delaysMs.length) {
    return Promise.reject(toApiError(error))
  }
  cfg.__retryCount = retryCount + 1
  const seconds = retryAfterSeconds(error)
  const delayMs = seconds != null ? seconds * 1000 : retryDelayMs(retryCount)
  await new Promise((r) => setTimeout(r, delayMs))
  return http.request(cfg)
}

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
  (error) => retryIdempotentGet(error as HttpErrorLike),
)

export async function get<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  return (await http.get<T>(url, { params })).data
}

/** 二进制下载（T11.5 docx 导出等；blob 无 code 字段，响应拦截器原样放行）。 */
export async function getBlob(url: string): Promise<{ blob: Blob; disposition: string | null }> {
  const resp = await http.get(url, { responseType: 'blob' })
  return {
    blob: resp.data as Blob,
    disposition: (resp.headers['content-disposition'] as string | undefined) ?? null,
  }
}

/** 服务可达性探测专用：短超时（审查 P0-2），失败即如实标「不可达」，不悬挂 30s。 */
export async function probeGet<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  return (await http.get<T>(url, { params, timeout: 5_000 })).data
}

export async function post<T>(url: string, data?: unknown): Promise<T> {
  return (await http.post<T>(url, data)).data
}

/** PUT（T11.2 用户管理 PATCH 语义沿用 Spring 风格 PUT/JSON body）。 */
export async function put<T>(url: string, data?: unknown): Promise<T> {
  return (await http.put<T>(url, data)).data
}

export async function patch<T>(url: string, data?: unknown): Promise<T> {
  return (await http.patch<T>(url, data)).data
}

export async function del<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  return (await http.delete<T>(url, { params })).data
}

/** DELETE 带 JSON body（PHASE-006 批量移出归属等少量端点用；axios 原生支持 data）。 */
export async function delWithBody<T>(url: string, data: unknown): Promise<T> {
  return (await http.delete<T>(url, { data })).data
}

/** 用户可见的统一错误文案：Error/ApiError 取 message；其余不把对象直接 String 给用户。 */
export function errText(e: unknown, max = 140): string {
  let t = e instanceof Error ? e.message : typeof e === 'string' ? e : ''
  if (!t) t = '操作失败，请稍后重试'
  return t.length > max ? `${t.slice(0, max)}…` : t
}
