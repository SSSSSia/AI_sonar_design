import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface AppNotification {
  id: string
  /** 幂等去重键（T11.1）：= 会话|类型|信封时间戳，服务端 client_key 同构；WS 实时项与登录拉取合并时去重 */
  key?: string
  kind: 'review_request' | 'alert' | 'ir_updated' | 'info'
  title: string
  meta?: string
  sessionId?: string
  time: number
  read: boolean
}

interface NotifyState {
  items: AppNotification[]
  add: (n: Pick<AppNotification, 'kind' | 'title' | 'meta'> & {
    sessionId?: string
    key?: string
    read?: boolean
    time?: number
  }) => void
  /** T11.1：登录后以服务端列表为准合并（保留 WS 实时新增；无 key 的旧 localStorage 项淘汰） */
  hydrate: (rows: AppNotification[]) => void
  markRead: (id: string) => void
  markAllRead: () => void
  remove: (id: string) => void
  clearRead: () => void
  clearAll: () => void
}

const MAX_ITEMS = 50

/** 通知中心（T11.1 起服务端持久化为准，按用户隔离）：WS 推送仍实时进店（带 key 幂等去重），
 *  已读/删除/清理动作同步调 /api/v1/notifications（AppLayout 接线），刷新/换设备登录后不丢。 */
export const useNotifyStore = create<NotifyState>()(
  persist(
    (set) => ({
      items: [],
      add: (n) =>
        set((s) => {
          if (n.key && s.items.some((i) => i.key === n.key)) return s
          return {
            items: [
              {
                ...n,
                id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                time: n.time ?? Date.now(),
                read: n.read ?? false,
              },
              ...s.items,
            ].slice(0, MAX_ITEMS),
          }
        }),
      hydrate: (rows) =>
        set((s) => {
          const serverKeys = new Set(rows.map((r) => r.key).filter(Boolean))
          const extra = s.items.filter((i) => i.key && !serverKeys.has(i.key))
          return { items: [...rows, ...extra].sort((a, b) => b.time - a.time).slice(0, MAX_ITEMS) }
        }),
      markRead: (id) => set((s) => ({ items: s.items.map((i) => (i.id === id ? { ...i, read: true } : i)) })),
      markAllRead: () => set((s) => ({ items: s.items.map((i) => ({ ...i, read: true })) })),
      remove: (id) => set((s) => ({ items: s.items.filter((i) => i.id !== id) })),
      clearRead: () => set((s) => ({ items: s.items.filter((i) => !i.read) })),
      clearAll: () => set({ items: [] }),
    }),
    { name: 'sonarcad-notify' },
  ),
)
