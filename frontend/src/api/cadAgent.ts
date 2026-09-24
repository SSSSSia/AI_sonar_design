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
