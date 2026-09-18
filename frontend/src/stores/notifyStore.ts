import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface AppNotification {
  id: string
  kind: 'review_request' | 'info'
  title: string
  meta?: string
  time: number
  read: boolean
}

interface NotifyState {
  items: AppNotification[]
  add: (n: Pick<AppNotification, 'kind' | 'title' | 'meta'>) => void
  markAllRead: () => void
}

const MAX_ITEMS = 50

/** 通知中心：后端无通知表，未读标记存本地（如实）；历史裁决走评审记录 API。 */
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
      markAllRead: () => set((s) => ({ items: s.items.map((i) => ({ ...i, read: true })) })),
    }),
    { name: 'sonarcad-notify' },
  ),
)
