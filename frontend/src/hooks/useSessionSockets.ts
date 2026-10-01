import { useEffect, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { SessionSocket, type SocketStatus } from '../ws/wsClient'
import { useAuthStore } from '../stores/authStore'
import { useProgressStore, useWsStatusStore } from '../stores/progressStore'
import { useNotifyStore } from '../stores/notifyStore'
import { useLogStore } from '../stores/logStore'

const GATE_LABEL: Record<string, string> = { gate1: '审阅关①', gate2: '审阅关②' }

/**
 * 为一组会话建立 WS 订阅（仪表盘的活跃会话 / 工作台的当前会话）。
 * 推送落地：task_progress → progressStore；review_request → 通知中心 + 会话列表失效；
 * ir_updated → 对应 IR 查询失效（HTTP 对账）；log → logStore（工作台实时日志面板）。
 * 重连由 SessionSocket 自管（指数退避）。
 */
export function useSessionSockets(sids: string[]) {
  const queryClient = useQueryClient()
  const key = useMemo(() => sids.join(','), [sids])

  useEffect(() => {
    const ids = key ? key.split(',') : []
    if (ids.length === 0) return
    const sockets = ids.map((sid) => {
      return new SessionSocket(
        sid,
        () => useAuthStore.getState().token,
        (m) => {
          if (m.type === 'task_progress') {
            useProgressStore.getState().upsert(m.session_id, m.payload)
          } else if (m.type === 'review_request') {
            // key 与服务端 client_key 同构（T11.1）：登录拉取合并时按 key 去重
            useNotifyStore.getState().add({
              kind: 'review_request',
              title: `${GATE_LABEL[m.payload.gate] ?? m.payload.gate} 待裁决`,
              meta: m.payload.summary,
              sessionId: m.session_id,
              key: `${m.session_id}|review_request|${m.timestamp}`,
            })
            void queryClient.invalidateQueries({ queryKey: ['sessions'] })
          } else if (m.type === 'alert') {
            // 系统告警（T10.1c 桥接信封，T11.1 起落库；实时项同样进通知中心）
            const summaries: string[] = Array.isArray(m.payload.summaries) ? m.payload.summaries : []
            useNotifyStore.getState().add({
              kind: 'alert',
              title: `系统告警 ${m.payload.status ?? ''}`.trim(),
              meta: summaries.join('；') || undefined,
              key: `|alert|${m.timestamp}`,
            })
          } else if (m.type === 'ir_updated') {
            void queryClient.invalidateQueries({ queryKey: ['ir', m.session_id] })
            void queryClient.invalidateQueries({ queryKey: ['session', m.session_id] })
          } else if (m.type === 'cad_tune_updated') {
            // 微调状态/版本更新（T8.5）：时间线与活跃状态即时刷新；version_ready 时
            // ir_updated 已同路触发预览刷新，这里补时间线数据面
            void queryClient.invalidateQueries({ queryKey: ['cad-tune', m.session_id] })
          } else if (m.type === 'log') {
            useLogStore.getState().push(m.session_id, {
              at: Date.parse(m.timestamp) || Date.now(),
              level: m.payload.level,
              text: m.payload.text,
            })
          }
        },
        (status: SocketStatus, attempt: number) => {
          useWsStatusStore.getState().upsert(sid, { status, attempt })
        },
      )
    })
    sockets.forEach((s) => s.connect())
    return () => sockets.forEach((s) => s.close())
  }, [key, queryClient])
}
