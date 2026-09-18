import { create } from 'zustand'

export interface SessionLogLine {
  at: number
  level: 'debug' | 'info' | 'warn' | 'error' | 'ok'
  text: string
}

interface LogState {
  bySession: Record<string, SessionLogLine[]>
  push: (sid: string, line: SessionLogLine) => void
  clear: (sid: string) => void
}

const MAX_LINES = 200

/**
 * 会话实时日志（WS log 消息，T4.x 优化项）。
 * 日志是流不是账本：不落库、刷新即空——权威记录是 IR 快照与 trace 链（A-12/A-16）。
 */
export const useLogStore = create<LogState>((set) => ({
  bySession: {},
  push: (sid, line) =>
    set((s) => {
      const list = [...(s.bySession[sid] ?? []), line]
      return { bySession: { ...s.bySession, [sid]: list.slice(-MAX_LINES) } }
    }),
  clear: (sid) => set((s) => ({ bySession: { ...s.bySession, [sid]: [] } })),
}))
