import { annotationsText, reviewMeta, irTypeLabel, gateLabel } from '../lib/stageLabel'
import type { ReviewRecord } from '../api/workflow'

/** 知识关回写结果元信息 → 人话（后端历史值：'written' / 'skipped（…）' / 'failed: …' / 摘要串 / 旧版逐项计数对象）。 */
function writebackText(v: unknown): string {
  if (typeof v === 'object' && v !== null) {
    const w = v as { written?: { written?: number; deprecated?: number }; ir_version?: number }
    const parts: string[] = []
    if (w.written?.written != null) parts.push(`新增 ${w.written.written}`)
    if (w.written?.deprecated != null) parts.push(`废弃 ${w.written.deprecated}`)
    if (w.ir_version != null) parts.push(`IR5 v${w.ir_version}`)
    return `已写入图谱${parts.length ? `（${parts.join(' · ')}）` : ''}`
  }
  const s = String(v)
  if (s === 'written') return '已写入图谱'
  if (s.startsWith('skipped')) return `未写入图谱（${s.includes('驳回') ? '本次驳回' : '跳过'}）`
  if (s.startsWith('failed')) return '回写失败（候选保留，可重试）'
  return s
}

/** 裁决历史「批注」单元格：批注正文 + 机器字段归一为可读副行（trace_id 不外显，仍可经溯源链反查）。 */
export function ReviewAnnotation({ record }: { record: ReviewRecord }) {
  const meta = reviewMeta(record.annotations)
  const bits: string[] = []
  if (typeof meta.reviewed_ir === 'string' && meta.reviewed_ir) bits.push(`审阅对象 ${irTypeLabel(meta.reviewed_ir)}`)
  if (typeof meta.rework_stage === 'string' && meta.rework_stage) bits.push(`回退 ${meta.rework_stage}`)
  if (Array.isArray(meta.invalidated) && meta.invalidated.length > 0) bits.push(`失效 ${meta.invalidated.length} 份下游快照`)
  if (meta.writeback !== undefined && meta.writeback !== '') bits.push(writebackText(meta.writeback))
  return (
    <div style={{ fontSize: 12 }}>
      <div>{annotationsText(record.annotations) || '—'}</div>
      {bits.length > 0 && (
        <div style={{ color: '#8b949e', fontSize: 11, marginTop: 2 }}>
          {gateLabel(record.gate)} · {bits.join('，')}
        </div>
      )}
    </div>
  )
}
