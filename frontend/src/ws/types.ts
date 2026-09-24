/** WS 消息类型（对齐 contracts/ws/ws-message.schema.json，宪法 §5.5） */

export type WsStage = 'A1' | 'A2' | 'A3' | 'A4' | 'A5' | 'review_gate_1' | 'review_gate_2'
export type WsStatus = 'running' | 'paused' | 'completed' | 'failed' | 'degraded'

export interface TaskProgressPayload {
  stage: WsStage
  status: WsStatus
  progress: number
  message?: string
}

export interface LogPayload {
  level: 'debug' | 'info' | 'ok' | 'warn' | 'error'
  text: string
}

export interface ReviewRequestPayload {
  gate: 'gate1' | 'gate2'
  ir_type: 'IR2' | 'IR3'
  ir_version: string
  summary?: string
}

export interface IrUpdatedPayload {
  ir_type: 'IR1' | 'IR2' | 'IR3' | 'IR4' | 'IR5'
  ir_version: string
}

export type CadTuneState =
  | 'awaiting_upload'
  | 'round_done'
  | 'version_ready'
  | 'finalized'
  | 'closed'
  | 'failed'

export interface CadTuneUpdatedPayload {
  tune_id: string
  state: CadTuneState
  round_no?: number
  ir_version?: string
}

interface Envelope<T> {
  session_id: string
  task_id?: string
  trace_id?: string
  timestamp: string
  payload: T
}

export type WsMessage =
  | ({ type: 'task_progress' } & Envelope<TaskProgressPayload>)
  | ({ type: 'log' } & Envelope<LogPayload>)
  | ({ type: 'review_request' } & Envelope<ReviewRequestPayload>)
  | ({ type: 'ir_updated' } & Envelope<IrUpdatedPayload>)
  | ({ type: 'cad_tune_updated' } & Envelope<CadTuneUpdatedPayload>)
