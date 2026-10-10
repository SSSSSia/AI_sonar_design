import { get, post } from './client'

// ---- LLM 供应商状态（ADR-011 顶栏状态胶囊；py-engine /api/v1/llm/*，经网关 py-engine-llm 路由） ----

/** 单供应商最近健康态（进程内口径，失败转移链可视化；宪法 §10.2 脱敏不含密钥） */
export interface LlmProviderHealth {
  name: string
  ok?: boolean
  last_error?: string | null
  [k: string]: unknown
}

export interface LlmProviderStatus {
  name: string
  priority?: number
  enabled?: boolean
  base_url?: string | null
  model?: string | null
  /** 各供应商最近一次调用成败（失败转移链） */
  health?: LlmProviderHealth[]
  [k: string]: unknown
}

/** 当前生效供应商（503 = 未配置可用供应商，胶囊转灰并如实提示） */
export function getLlmProvider() {
  return get<LlmProviderStatus>('/llm/provider')
}

/** 热重载供应商配置（admin；同时清 L1 LLM 缓存） */
export function reloadLlmProvider() {
  return post<{ status: string }>('/llm/provider/reload')
}

// ---- 算力侧存活探针（UI-3 系统状态盒「Agent 集群」数据源；网关 py-engine-health 路由） ----

/** /healthz 响应（T9.1）：status ok/degraded/unhealthy + broker 连通 + 事件循环心跳 */
export interface EngineHealth {
  status: 'ok' | 'degraded' | 'unhealthy' | string
  broker?: string
  event_loop?: string
  [k: string]: unknown
}

export function getEngineHealth() {
  return get<EngineHealth>('/healthz')
}
