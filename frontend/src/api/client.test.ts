import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, http, setUnauthorizedHandler } from './client'
import { useAuthStore } from '../stores/authStore'

/** 用自定义 adapter 模拟后端统一响应包，验证解包与 401 处理。 */
function mockAdapter(respond: () => { status: number; data: unknown }) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  http.defaults.adapter = (async () => {
    const { status, data } = respond()
    return { data, status, statusText: String(status), headers: {}, config: http.defaults }
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
