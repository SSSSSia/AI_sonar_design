import { Tag } from 'antd'
import type { SourceType } from '../api/workflow'

/** source_type 四色徽标（PHASE-004 §2.5：不含主蓝，避免与交互语义混淆）。 */
export const SOURCE_META: Record<SourceType, { label: string; color: string }> = {
  formula: { label: 'formula 公式', color: 'purple' },
  case: { label: 'case 案例', color: 'cyan' },
  standard: { label: 'standard 军标', color: 'green' },
  estimated: { label: 'estimated 估算', color: 'orange' },
}

export function SourceBadge({ type }: { type: SourceType }) {
  const meta = SOURCE_META[type] ?? { label: type, color: 'default' }
  return <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>{meta.label.split(' ')[0]}</Tag>
}

export function ConfidenceBadge({ value }: { value: 'high' | 'medium' }) {
  return <Tag color={value === 'high' ? 'green' : 'default'} style={{ marginInlineEnd: 0 }}>{value}</Tag>
}
