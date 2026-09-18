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

/** 裁决批注归一化：后端可能是数组/JSON 字符串/null（jsonb 映射差异）。 */
export function annotationsText(a: unknown): string {
  if (Array.isArray(a)) {
    return a.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join('；')
  }
  if (typeof a === 'string') {
    try {
      return annotationsText(JSON.parse(a))
    } catch {
      return a
    }
  }
  if (a == null) return ''
  return JSON.stringify(a)
}
