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
  return unwrapAnnotationPayload(a).text
}

/** 裁决记录的机器元信息（reviewed_ir / rework_stage / invalidated / trace_id…）。
 *  annotations 列存的是复合负载（历史设计：{annotations, trace_id, reviewed_ir, ...}），
 *  界面只展示批注正文，元信息单独取用。 */
export function reviewMeta(a: unknown): Record<string, unknown> {
  return unwrapAnnotationPayload(a).meta
}

function unwrapAnnotationPayload(a: unknown): { text: string; meta: Record<string, unknown> } {
  if (typeof a === 'string') {
    try {
      return unwrapAnnotationPayload(JSON.parse(a))
    } catch {
      return { text: a, meta: {} }
    }
  }
  if (Array.isArray(a)) {
    return { text: a.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join('；'), meta: {} }
  }
  if (a != null && typeof a === 'object') {
    const obj = a as Record<string, unknown>
    if (Array.isArray(obj.annotations)) {
      const { annotations, ...meta } = obj
      return { text: annotationsText(annotations), meta }
    }
    return { text: JSON.stringify(a), meta: {} }
  }
  return { text: '', meta: {} }
}

/** IR1 是否为降级产物（LLM 规划不可用，目标仅登记原始需求）。 */
export function ir1Degraded(ir1: { missing_items: string[] } | null | undefined): boolean {
  return !!ir1?.missing_items?.some((m) => m.includes('LLM 规划不可用'))
}
