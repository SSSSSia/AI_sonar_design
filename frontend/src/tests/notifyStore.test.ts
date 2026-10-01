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

describe('T11.1 通知持久化：key 幂等去重与 hydrate 合并', () => {
  beforeEach(() => {
    useNotifyStore.setState({ items: [] })
  })

  it('add 相同 key 的信封不重复入店（WS 实时 vs 登录拉取同一封）', () => {
    useNotifyStore.getState().add({ kind: 'review_request', title: '审阅关① 待裁决', key: 'sess-1|review_request|2026-10-01T00:00:00Z' })
    useNotifyStore.getState().add({ kind: 'review_request', title: '审阅关① 待裁决', key: 'sess-1|review_request|2026-10-01T00:00:00Z' })
    expect(useNotifyStore.getState().items).toHaveLength(1)
  })

  it('hydrate 以服务端为准，保留 WS 新增（key 不同），按时间倒序', () => {
    useNotifyStore.getState().add({ kind: 'alert', title: '系统告警 firing', key: '|alert|2026-10-01T01:00:00Z', time: Date.parse('2026-10-01T01:00:00Z') })
    useNotifyStore.getState().hydrate([
      { id: 'srv-1', key: 'sess-1|review_request|2026-10-01T02:00:00Z', kind: 'review_request', title: '审阅关② 待裁决', time: Date.parse('2026-10-01T02:00:00Z'), read: true },
    ])
    const titles = useNotifyStore.getState().items.map((i) => i.title)
    expect(titles).toEqual(['审阅关② 待裁决', '系统告警 firing'])
  })

  it('hydrate 淘汰无 key 的旧 localStorage 项（服务端事实源）', () => {
    useNotifyStore.getState().add({ kind: 'info', title: 'legacy-local' })
    useNotifyStore.getState().hydrate([
      { id: 'srv-1', key: 'k-1', kind: 'alert', title: '系统告警', time: Date.now(), read: false },
    ])
    const titles = useNotifyStore.getState().items.map((i) => i.title)
    expect(titles).toEqual(['系统告警'])
  })
})
