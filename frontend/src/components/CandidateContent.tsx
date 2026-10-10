import { Tag } from 'antd'
import type { Ir5Candidate } from '../api/workflow'

const KIND_LABEL: Record<string, string> = { relation: '关系', case: '案例', constraint: '约束' }
const DEDUP_LABEL: Record<string, { text: string; color: string }> = {
  new: { text: '新增', color: 'success' },
  duplicate: { text: '重复', color: 'warning' },
}

/** 知识图谱关系谓词中文化（常用于 A5 候选）。 */
const RELATION_LABEL: Record<string, string> = {
  CONTAINS: '包含',
  DETERMINES: '决定',
  DEFINES_CONDITION_FOR: '构成条件',
  AFFECTS: '影响',
  REQUIRES: '要求',
  MEASURED_BY: '度量',
}

function relLabel(r: string): string {
  return RELATION_LABEL[r] ?? r.toLowerCase().replaceAll('_', ' ')
}

/**
 * IR5 知识候选内容（审查 P0-1）：按类型结构化渲染，不再兜底 JSON.stringify。
 *  relation → 「A —关系→ B」+ context 灰字副行；case/constraint → 名称 + context/表达式。
 */
export function CandidateContent({ candidate }: { candidate: Ir5Candidate }) {
  const p = (candidate.payload ?? {}) as Record<string, unknown>

  if (candidate.type === 'relation') {
    const from = (p.from ?? {}) as { name?: string }
    const to = (p.to ?? {}) as { name?: string }
    const relation = typeof p.relation === 'string' ? p.relation : ''
    const context = typeof p.context === 'string' ? p.context : ''
    return (
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12.5 }}>
          <b>{from.name ?? '？'}</b>
          <span style={{ color: 'var(--text-2)', margin: '0 6px' }}>—{relLabel(relation)}→</span>
          <b>{to.name ?? '？'}</b>
        </div>
        {context && (
          <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 2, overflowWrap: 'anywhere' }}>└ {context}</div>
        )}
      </div>
    )
  }

  const name = typeof p.name === 'string' ? p.name : ''
  const expr = typeof p.expression === 'string' ? p.expression : ''
  const desc = typeof p.description === 'string' ? p.description : ''
  const context = typeof p.context === 'string' ? p.context : ''
  const params = Array.isArray(p.params) ? (p.params as { name?: string; value?: unknown; unit?: string }[]) : []
  const sub = context || desc || (params.length > 0
    ? `参数组 ${params.length} 项：${params.slice(0, 4).map((x) => `${x.name ?? '?'}=${String(x.value)}${x.unit ?? ''}`).join('、')}${params.length > 4 ? ' 等' : ''}`
    : '')
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 12.5, fontWeight: 500 }}>{name || '（未命名）'}</div>
      {expr && <div className="mono" style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 2 }}>{expr}</div>}
      {sub && <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 2, overflowWrap: 'anywhere' }}>└ {sub}</div>}
    </div>
  )
}

/** 候选类型列（中文 Tag）。 */
export function CandidateKindTag({ type }: { type: string }) {
  return <Tag style={{ marginInlineEnd: 0 }}>{KIND_LABEL[type] ?? type}</Tag>
}

/** 候选去重状态列（中文）。 */
export function CandidateDedupTag({ status }: { status: string }) {
  const meta = DEDUP_LABEL[status] ?? { text: status, color: 'default' }
  return <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>{meta.text}</Tag>
}

/** 知识关裁决 toast 文案：effects.writeback 摘要为人话（不向用户倾倒原始 JSON）。
 *  实际响应结构：{written: {written, deprecated}, selected_indexes, ir_version}；
 *  回写失败时 workflow 侧给字符串 'failed'（决策已落库、IR5 保持 pending 可重试）。 */
export function writebackToastText(writeback: unknown): string {
  if (writeback === 'failed') {
    return '裁决已记录，但知识写入失败——知识图谱暂不可用；候选仍在待归档列表，稍后再次批准即可重试'
  }
  const w = (writeback ?? {}) as Record<string, unknown>
  const inner = (w.written != null && typeof w.written === 'object' ? w.written : w) as Record<string, unknown>
  if (typeof inner.written === 'number') {
    const dep = typeof inner.deprecated === 'number' ? inner.deprecated : 0
    return `知识归档完成：已写入 ${inner.written} 条${dep > 0 ? `，${dep} 条同名旧知识自动标记废弃` : ''}`
  }
  // 计数字段缺失（后端形态演进）：只说结论，不再截断倾倒 JSON
  return '知识归档完成'
}
