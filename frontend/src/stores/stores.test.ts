import { beforeEach, describe, expect, it } from 'vitest'
import { hasAnyRole, useAuthStore } from './authStore'
import { useNotifyStore } from './notifyStore'
import { useProgressStore } from './progressStore'
import type { TaskProgressPayload } from '../ws/types'

beforeEach(() => {
  useAuthStore.getState().clear()
  useNotifyStore.setState({ items: [] })
  useProgressStore.setState({ bySession: {} })
})

describe('authStore', () => {
  it('setAuth / clear 往返', () => {
    useAuthStore.getState().setAuth({ token: 'a', username: 'u', displayName: '用户', roles: ['designer'] })
    expect(useAuthStore.getState().token).toBe('a')
    expect(useAuthStore.getState().roles).toEqual(['designer'])
    useAuthStore.getState().clear()
    expect(useAuthStore.getState().token).toBeNull()
  })

  it('hasAnyRole', () => {
    expect(hasAnyRole(['viewer'], ['admin', 'designer'])).toBe(false)
    expect(hasAnyRole(['reviewer'], ['admin', 'reviewer'])).toBe(true)
  })
})

describe('notifyStore', () => {
  it('add 增加未读，markAllRead 清零', () => {
    useNotifyStore.getState().add({ kind: 'review_request', title: '审阅关① 待裁决', meta: '会话 A' })
    useNotifyStore.getState().add({ kind: 'info', title: '构建完成' })
    expect(useNotifyStore.getState().items).toHaveLength(2)
    expect(useNotifyStore.getState().items[0].read).toBe(false)
    useNotifyStore.getState().markAllRead()
    expect(useNotifyStore.getState().items.every((i) => i.read)).toBe(true)
  })
})

describe('progressStore', () => {
  it('upsert / remove', () => {
    const p: TaskProgressPayload = { stage: 'A3', status: 'running', progress: 55 }
    useProgressStore.getState().upsert('s1', p)
    expect(useProgressStore.getState().bySession.s1?.stage).toBe('A3')
    useProgressStore.getState().remove('s1')
    expect(useProgressStore.getState().bySession.s1).toBeUndefined()
  })
})
