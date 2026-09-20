import { get, post } from './client'

/** IR 快照接口（contracts/ir/*.schema.json 对齐）。content 为 JSON 字符串，用 parseIr 解。 */
export interface IrEnvelope {
  ir_type: 'IR0' | 'IR1' | 'IR2' | 'IR3' | 'IR4' | 'IR5'
  version: number
  schema_version: string
  created_by: string
  invalidated: boolean
  content: string | unknown
}

// ---- IR1 ----
export interface KbSelectionBase {
  kb_id: string
  kb_name: string
  /** Agent 路由探测命中数（manual 模式为 -1，无依据可展示） */
  hits: number
  selected: boolean
}
export interface KbSelection {
  mode: 'manual' | 'auto'
  bases: KbSelectionBase[]
  /** auto 模式全部零命中时回退全部库（如实标注，A-16） */
  fallback_all?: boolean
}
export interface Ir1 {
  objectives: { dimension: string; value: unknown; explicit: boolean }[]
  missing_items: string[]
  sub_queries: string[]
  retrieved_context: { doc_id: string; location: string; snippet: string; kb_id?: string }[]
  /** 知识库选择留痕（PHASE-006 D6-3/D6-7；旧快照无此字段） */
  kb_selection?: KbSelection
}

// ---- IR2 ----
export type SourceType = 'formula' | 'case' | 'standard' | 'estimated'
export interface Ir2Param {
  id: string
  name: string
  value: unknown
  unit: string
  source_type: SourceType
  source_ref: string
  derivation: string
  confidence: 'high' | 'medium'
  trace_id: string
}
export interface Ir2 {
  design_document: string
  parameters: Ir2Param[]
}

// ---- IR3 ----
export interface Ir3 {
  cadquery_script: string
  step_file: { bucket: string; object_key: string }
  features: { feature_id: string; segment: string; geometry: Record<string, unknown>; linked_param_id: string }[]
}

// ---- IR4 ----
export type Severity = 'error' | 'warning' | 'info'
export interface Ir4Issue {
  constraint_id: string
  severity: Severity
  message?: string
  related_params: string[]
  related_features: string[]
  suggestion?: 'back_to_A2' | 'back_to_A3'
}
export interface Ir4 {
  overall: 'pass' | 'fail'
  issues: Ir4Issue[]
}

// ---- IR5 ----
export interface Ir5Candidate {
  type: 'relation' | 'case' | 'constraint'
  payload: Record<string, unknown>
  dedup_status: 'new' | 'duplicate'
}
export interface Ir5 {
  candidates: Ir5Candidate[]
  writeback_status: 'pending' | 'confirmed' | 'written'
}

// ---- 溯源链 ----
export interface TraceStep {
  step_no: number
  actor: string
  action: string
  ir_snapshot_id?: string
  payload?: Record<string, unknown>
  created_at: string
}
export interface TraceChain {
  trace_id: string
  steps: TraceStep[]
  context: {
    session_id?: string
    ir0_raw_text?: string
    ir1_objectives?: unknown
    ir1_retrieved_refs?: unknown
    ir2_param?: Record<string, unknown>
  }
}

// ---- 评审 ----
export interface ReviewRecord {
  id: string
  gate: string
  decision: string
  ir_snapshot_id?: string
  annotations: unknown[]
  reviewer_id?: string
  /** 实际操作者用户名（后端 join users；历史缺失时前端回退短 id） */
  reviewer_name?: string
  decided_at: string
}

export interface ReviewEffects {
  gate: string
  decision: string
  record_id: string
  resumed_to?: string
  rework_stage?: string
  invalidated?: string[]
  /** py-engine 回写响应对象；失败时为状态字符串 'failed'（决策已落库、IR5 保持 pending）。 */
  writeback?: unknown
}

/** content 可能是 JSON 字符串或对象（契约实现差异收敛处）。 */
export function parseIr<T>(env: IrEnvelope | undefined | null): T | null {
  if (!env) return null
  if (typeof env.content === 'string') {
    try {
      return JSON.parse(env.content) as T
    } catch {
      return null
    }
  }
  return (env.content as T) ?? null
}

export function getIr(sid: string, type: 'IR0' | 'IR1' | 'IR2' | 'IR3' | 'IR4' | 'IR5') {
  return get<IrEnvelope>(`/workflow/sessions/${sid}/irs/${type}`)
}

export function getTrace(traceId: string) {
  return get<TraceChain>(`/trace/${traceId}`)
}

export function listReviews(sid: string) {
  return get<ReviewRecord[]>(`/workflow/sessions/${sid}/reviews`)
}

export function postReview(
  sid: string,
  gate: 'gate1' | 'gate2' | 'knowledge',
  body: { decision: 'approved' | 'rejected'; annotations: unknown[]; reworkStage?: string },
) {
  return post<ReviewEffects>(`/workflow/sessions/${sid}/reviews/${gate}`, body)
}
