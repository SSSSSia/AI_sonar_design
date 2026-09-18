import type { DesignSession } from '../api/types'

export const STAGE_LABEL: Record<string, string> = {
  A1: 'A1 需求解析',
  A2: 'A2 方案生成',
  A3: 'A3 CAD 建模',
  A4: 'A4 合规校验',
  A5: 'A5 知识沉淀',
  gate1: '审阅关①（IR2）',
  gate2: '审阅关②（IR3+IR4）',
}

export const STATUS_TAG: Record<string, { color: string; text: string }> = {
  pending: { color: 'default', text: '待启动' },
  running: { color: 'processing', text: '运行中' },
  paused: { color: 'warning', text: '待审阅' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '失败' },
}

export function statusTag(status: DesignSession['pipelineStatus']) {
  return STATUS_TAG[status] ?? { color: 'default', text: status }
}

export function stageLabel(stage?: string | null): string {
  if (!stage) return '—'
  return STAGE_LABEL[stage] ?? stage
}
