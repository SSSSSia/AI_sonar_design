import { del, get, post } from './client'

// ---- 通知中心（T11.1 通知持久化，P2-4）----
// 服务端事实源：notify 广播的关键信封（review_request/alert）按用户扇出落库；
// WS 仍负责实时性，本模块负责登录后拉取与已读/删除/清理的持久化。

export interface ServerNotification {
  id: string
  /** 幂等去重键 = 会话|类型|信封时间戳（与 WS 信封同构，合并去重用） */
  client_key: string
  session_id: string
  type: string
  title?: string | null
  payload?: Record<string, unknown>
  read: boolean
  created_at?: string
}

export interface NotificationListResp {
  items: ServerNotification[]
  total: number
  unread: number
}

export function listNotifications(limit = 50) {
  return get<NotificationListResp>('/notifications', { limit })
}

export function markNotificationRead(id: string) {
  return post<{ updated: number }>(`/notifications/${id}/read`)
}

export function markAllNotificationsRead() {
  return post<{ updated: number }>('/notifications/read-all')
}

export function deleteNotification(id: string) {
  return del<{ deleted: number }>(`/notifications/${id}`)
}

export function clearNotifications(scope: 'read' | 'all') {
  return del<{ deleted: number }>('/notifications', { scope })
}
