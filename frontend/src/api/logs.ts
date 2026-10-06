import { get } from './client'

// ---- 日志中心（T12.5：跨会话日志检索，admin；AdminPage 数据源） ----

export interface LogRow {
  id: number
  session_id: string
  level: string
  text: string
  kind: string
  created_at?: string
}

export interface LogSearchResp {
  logs: LogRow[]
  total: number
  limit: number
  offset: number
}

export interface LogSearchParams {
  session_id?: string
  level?: string
  kind?: string
  keyword?: string
  hours?: number
  limit?: number
  offset?: number
}

/** 检索参数 → query 对象（空值剔除；vitest 覆盖） */
export function buildLogSearchParams(p: LogSearchParams): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '') continue
    out[k] = v
  }
  return out
}

export function searchLogs(p: LogSearchParams) {
  return get<LogSearchResp>('/logs/search', buildLogSearchParams(p))
}
