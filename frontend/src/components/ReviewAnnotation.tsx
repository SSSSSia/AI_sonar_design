import { annotationsText, reviewMeta, irTypeLabel, gateLabel } from '../lib/stageLabel'
import type { ReviewRecord } from '../api/workflow'

/** 知识关回写结果元信息 → 人话（后端历史值：written / skipped（…）/ 逐项计数 map）。 */
function writebackText(v: unknown): string {
  const s = typeof v === 'string' ? v : ''
  if (s === 'written') return '已写入图谱'
  if (s.startsWith('skipped')) return `未写入图谱（${s.includes('驳回') ? '本次驳回' : '跳过'}）`
  return `回写 ${String(v)}`
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
