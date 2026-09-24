import type { TuneRound, TuneSession } from '../../api/cadAgent'

export interface TuneRoundRow {
  roundNo: number
  status: TuneRound['status']
  prompt: string
  irVersion: number | null
  toolCount: number
  failedError: string
  isQuestion: boolean
}

export interface TuneSummary {
  active: TuneSession | null
  latest: TuneSession | null
  activeRoundCount: number
  rows: TuneRoundRow[]
}

const STATUS_CN: Record<TuneRound['status'], string> = {
  running: '进行中',
  awaiting_upload: '待上传',
  done: '完成',
  failed: '失败',
}

export function roundStatusLabel(status: TuneRound['status']): string {
  return STATUS_CN[status] ?? status
}

/** 微调时间线数据整形（纯函数，vitest 覆盖）：最新会话的轮次倒序 + 活跃会话信息。 */
export function summarizeTunes(sessions: TuneSession[] | undefined | null): TuneSummary {
  const list = sessions ?? []
  const latest = list.length ? list[0] : null
  const active = list.find((s) => s.status === 'active') ?? null
  const rounds = [...(latest?.rounds ?? [])].sort((a, b) => b.round_no - a.round_no)
  const rows: TuneRoundRow[] = rounds.map((r) => ({
    roundNo: r.round_no,
    status: r.status,
    prompt: (r.prompt ?? '').slice(0, 60),
    irVersion: r.result_ir3_version ?? null,
    toolCount: (r.llm_trace?.tools ?? []).length,
    failedError: r.status === 'failed' ? (r.error ?? (r.llm_trace?.runner?.error ?? '')).slice(0, 120) : '',
    isQuestion: r.status === 'done' && !r.result_ir3_version,
  }))
  const activeRoundCount = (active?.rounds ?? []).filter(
    (r) => r.status === 'running' || r.status === 'awaiting_upload',
  ).length
  return { active, latest, activeRoundCount, rows }
}
