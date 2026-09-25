import { beforeEach, describe, expect, it } from 'vitest'
import { useNotifyStore } from '../stores/notifyStore'

const add = (title: string) => useNotifyStore.getState().add({ kind: 'info', title })

describe('notifyStore 管理（2026-09-25 用户反馈：通知没法删除）', () => {
  beforeEach(() => {
    useNotifyStore.setState({ items: [] })
  })

  it('remove 删除单条，其余保留', () => {
    add('a')
    add('b')
    add('c')
    const target = useNotifyStore.getState().items.find((i) => i.title === 'b')!.id
    useNotifyStore.getState().remove(target)
    const titles = useNotifyStore.getState().items.map((i) => i.title)
    expect(titles).toHaveLength(2)
    expect(titles).toContain('a')
    expect(titles).toContain('c')
  })

  it('clearRead 只清已读，未读保留', () => {
    add('keep')
    useNotifyStore.getState().add({ kind: 'review_request', title: 'drop' })
    useNotifyStore.getState().markAllRead()
    add('unread-later')
    useNotifyStore.getState().clearRead()
    expect(useNotifyStore.getState().items.map((i) => i.title)).toEqual(['unread-later'])
  })

  it('clearAll 清空', () => {
    add('x')
    add('y')
    useNotifyStore.getState().clearAll()
    expect(useNotifyStore.getState().items).toEqual([])
  })

  it('markAllRead 后全部已读', () => {
    add('a')
    add('b')
    useNotifyStore.getState().markAllRead()
    expect(useNotifyStore.getState().items.every((i) => i.read)).toBe(true)
  })
})
