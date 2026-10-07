import { get } from './client'

// ---- 审计中心（T13.1：§9.4 审计留痕查询面，admin；AdminPage 数据源） ----

export interface AuditLogRow {
  id: number
  user_id: string | null
  username: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  detail: Record<string, unknown> | string | null
  ip: string | null
  created_at: string
}

export interface AuditSearchResp {
  logs: AuditLogRow[]
  total: number
}

export interface AuditSearchParams {
  username?: string
  action?: string
  resource_type?: string
  hours?: number
  limit?: number
  offset?: number
}

/** 检索参数 → query 对象（空值剔除） */
export function buildAuditSearchParams(p: AuditSearchParams): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === '') continue
    out[k] = v
  }
  return out
}

export function searchAuditLogs(p: AuditSearchParams) {
  return get<AuditSearchResp>('/audit/logs', buildAuditSearchParams(p))
}
