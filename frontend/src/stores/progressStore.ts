import { create } from 'zustand'
import type { TaskProgressPayload } from '../ws/types'

export interface SessionProgress extends TaskProgressPayload {
  updatedAt: number
}

interface ProgressState {
  bySession: Record<string, SessionProgress>
  upsert: (sid: string, p: TaskProgressPayload) => void
  remove: (sid: string) => void
}

/** 实时任务进度（WS 推送落地处）；HTTP 对账（重新拉会话列表）后以列表为准。 */
export const useProgressStore = create<ProgressState>((set) => ({
  bySession: {},
  upsert: (sid, p) =>
    set((s) => ({ bySession: { ...s.bySession, [sid]: { ...p, updatedAt: Date.now() } } })),
  remove: (sid) =>
    set((s) => {
      const next = { ...s.bySession }
      delete next[sid]
      return { bySession: next }
    }),
}))

export type SocketState = { status: string; attempt: number }

interface WsStatusState {
  bySession: Record<string, SocketState>
  upsert: (sid: string, st: SocketState) => void
  remove: (sid: string) => void
}

export const useWsStatusStore = create<WsStatusState>((set) => ({
  bySession: {},
  upsert: (sid, st) => set((s) => ({ bySession: { ...s.bySession, [sid]: st } })),
  remove: (sid) =>
    set((s) => {
      const next = { ...s.bySession }
      delete next[sid]
      return { bySession: next }
    }),
}))
