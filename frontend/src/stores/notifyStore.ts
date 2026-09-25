import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface AppNotification {
  id: string
  kind: 'review_request' | 'info'
  title: string
  meta?: string
  sessionId?: string
  time: number
  read: boolean
}

interface NotifyState {
  items: AppNotification[]
  add: (n: Pick<AppNotification, 'kind' | 'title' | 'meta'> & { sessionId?: string }) => void
  markRead: (id: string) => void
  markAllRead: () => void
  remove: (id: string) => void
  clearRead: () => void
  clearAll: () => void
}

const MAX_ITEMS = 50

/** 通知中心：后端无通知表，未读标记存本地（如实）；历史裁决走评审记录 API。
 *  管理（2026-09-25 用户反馈）：单条删除 / 清除已读 / 清空——persist 到 localStorage，刷新仍在。 */
export const useNotifyStore = create<NotifyState>()(
  persist(
    (set) => ({
      items: [],
      add: (n) =>
        set((s) => ({
          items: [
            { ...n, id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, time: Date.now(), read: false },
            ...s.items,
        ].slice(0, MAX_ITEMS),
        })),
        markRead: (id) => set((s) => ({ items: s.items.map((i) => (i.id === id ? { ...i, read: true } : i)) })),
        markAllRead: () => set((s) => ({ items: s.items.map((i) => ({ ...i, read: true })) })),
        remove: (id) => set((s) => ({ items: s.items.filter((i) => i.id !== id) })),
        clearRead: () => set((s) => ({ items: s.items.filter((i) => !i.read) })),
        clearAll: () => set({ items: [] }),
    }),
    { name: 'sonarcad-notify' },
  ),
)
