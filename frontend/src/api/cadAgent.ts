import { get, post } from './client'

// ---- CAD 交互微调（PHASE-008，/api/v1/cad-agent/**）----

export interface TuneToolTrace {
  iteration?: number
  name?: string
  code_chars?: number
  ok?: boolean
  quality?: string | null
  error?: string | null
  result_preview?: string
  [k: string]: unknown
}

export interface TuneRound {
  id: string
  round_no: number
  baseline_ir_version: number
  prompt: string
  selection?: Record<string, unknown> | null
  llm_trace?: {
    tools?: TuneToolTrace[]
    runner?: { success?: boolean; summary?: string; error?: string | null }
  } | null
  script?: string | null
  result_ir3_version?: number | null
  status: 'running' | 'awaiting_upload' | 'done' | 'failed'
  error?: string | null
  created_at?: string
  [k: string]: unknown
}

export interface TuneSession {
  id: string
  design_session_id: string
  status: 'active' | 'finalized' | 'abandoned'
  baseline_ir_version: number
  note?: string | null
  created_by?: string
  created_at?: string
  rounds?: TuneRound[]
  [k: string]: unknown
}

export function listTunes(sid: string) {
  return get<{ sessions: TuneSession[] }>(`/cad-agent/sessions/${sid}/tune`)
}

export function createTune(sid: string) {
  return post<TuneSession>(`/cad-agent/sessions/${sid}/tune`, {})
}

// ---- E1 参数变更提案（T11.3，P2-1）：微调定稿 diff → 人工确认回写 IR2 → gate2 复审 ----
export interface ParamProposal {
  id: string
  session_id: string
  tune_id: string
  round_no: number
  ir3_version: number
  baseline_ir_version: number
  param_id: string
  param_name?: string | null
  unit: string
  old_value: number
  new_value: number
  delta_pct: number
  dimension: number
  status: 'proposed' | 'applied' | 'ignored'
  applied_ir2_version?: number | null
  created_at?: string
  [k: string]: unknown
}

export function listParamProposals(sid: string, status?: 'proposed' | 'applied' | 'ignored') {
  return get<{ proposals: ParamProposal[] }>(
    `/cad-agent/sessions/${sid}/param-proposals`, status ? { status } : undefined)
}

export function confirmParamProposals(sid: string, proposal_ids: string[]) {
  return post<{ applied: number; ir2_version: number; invalidated: string[]; redispatched: boolean }>(
    `/cad-agent/sessions/${sid}/param-proposals/confirm`, { proposal_ids })
}

export function ignoreParamProposal(proposalId: string) {
  return post<{ id: string; status: string }>(`/cad-agent/param-proposals/${proposalId}/ignore`, {})
}

/** E1 正向参数更新（T12.2）：文档→CAD，仅 paused@gate2 可发起。 */
export function updateParameter(sid: string, pid: string, body: { value: number; reason?: string }) {
  return post<{
    ir2_version: number
    param_id: string
    old_value: number | null
    new_value: number
    invalidated: string[]
    depth: number
    a3_redispatched: boolean
    note: string
  }>(`/cad-agent/sessions/${sid}/parameters/${pid}/update`, body)
}
