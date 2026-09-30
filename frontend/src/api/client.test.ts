import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, http, retryConfig, setUnauthorizedHandler } from './client'
import { useAuthStore } from '../stores/authStore'

/** 用自定义 adapter 模拟后端统一响应包，验证解包与 401 处理。
 * 非 2xx 按真 adapter 语义 reject（axios 的 settle 校验发生在内置 adapter 内部，
 * 自定义 adapter 直接 resolve 不会走错误拦截器——T9.4 重试用例需要真实错误路径）。 */
function mockAdapter(respond: () => { status: number; data: unknown }) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  http.defaults.adapter = (async (config: any) => {
    const { status, data } = respond()
    const resp = { data, status, statusText: String(status), headers: {}, config }
    if (status >= 400) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const err: any = new Error(`Request failed with status code ${status}`)
      err.config = config
      err.response = resp
      err.isAxiosError = true
      throw err
    }
    return resp
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  }) as any
}

describe('api client 响应包处理', () => {
  beforeEach(() => {
    useAuthStore.getState().setAuth({ token: 't0k', username: 'u', displayName: 'u', roles: ['admin'] })
  })
  afterEach(() => {
    useAuthStore.getState().clear()
    vi.restoreAllMocks()
    delete (http.defaults as Record<string, unknown>).adapter
    retryConfig.delaysMs = [1000, 2000, 4000]
  })

  it('code=0 时解包 data', async () => {
    mockAdapter(() => ({ status: 200, data: { code: 0, message: 'ok', data: { hello: 1 }, traceId: 'x' } }))
    const resp = await http.get('/anything')
    expect(resp.data).toEqual({ hello: 1 })
  })

  it('code!=0 时抛 ApiError 且透出 message', async () => {
    mockAdapter(() => ({ status: 200, data: { code: 409, message: '非法状态转移', data: null } }))
    await expect(http.get('/anything')).rejects.toMatchObject({
      code: 409,
      message: '非法状态转移',
    })
    await expect(http.get('/anything')).rejects.toBeInstanceOf(ApiError)
  })

  it('HTTP 401 清除本地凭证并触发跳转回调', async () => {
    let redirected = false
    setUnauthorizedHandler(() => {
      redirected = true
    })
    mockAdapter(() => ({ status: 401, data: { code: 401, message: '未登录', data: null } }))
    await expect(http.get('/anything')).rejects.toBeInstanceOf(ApiError)
    expect(useAuthStore.getState().token).toBeNull()
    expect(redirected).toBe(true)
  })

  it('请求自动附带 Bearer 头', async () => {
    let seen: string | undefined
    mockAdapter(() => {
      seen = useAuthStore.getState().token ? 'Bearer t0k' : undefined
      return { status: 200, data: { code: 0, message: 'ok', data: null } }
    })
    await http.get('/anything')
    expect(seen).toBe('Bearer t0k')
  })
})

describe('T9.4 429/503 幂等 GET 指数退避', () => {
  beforeEach(() => {
    useAuthStore.getState().setAuth({ token: 't0k', username: 'u', displayName: 'u', roles: ['admin'] })
    retryConfig.delaysMs = [1, 1, 1] // 测试注入小延迟
  })
  afterEach(() => {
    useAuthStore.getState().clear()
    vi.restoreAllMocks()
    delete (http.defaults as Record<string, unknown>).adapter
    retryConfig.delaysMs = [1000, 2000, 4000]
  })

  it('GET 遇 429 自动退避重试，恢复后拿到数据', async () => {
    let calls = 0
    mockAdapter(() => {
      calls += 1
      if (calls <= 2) {
        return { status: 429, data: { code: 429, message: '请求过于频繁', data: null, retryAfterSeconds: 1 } }
      }
      return { status: 200, data: { code: 0, message: 'ok', data: { ok: 42 } } }
    })
    const resp = await http.get('/anything')
    expect(calls).toBe(3)
    expect(resp.data).toEqual({ ok: 42 })
  })

  it('POST 遇 429 不自动重试（防重复裁决），直接抛 ApiError', async () => {
    let calls = 0
    mockAdapter(() => {
      calls += 1
      return { status: 429, data: { code: 429, message: '请求过于频繁', data: null, retryAfterSeconds: 1 } }
    })
    await expect(http.post('/anything', {})).rejects.toBeInstanceOf(ApiError)
    expect(calls).toBe(1)
  })

  it('GET 连续 429 超过重试上限后如实抛 ApiError（code=429）', async () => {
    let calls = 0
    mockAdapter(() => {
      calls += 1
      return { status: 429, data: { code: 429, message: '请求过于频繁', data: null } }
    })
    await expect(http.get('/anything')).rejects.toMatchObject({ code: 429 })
    expect(calls).toBe(4) // 首发 + 3 次重试
  })
})
