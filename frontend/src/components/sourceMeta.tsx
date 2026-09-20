import { Tag } from 'antd'
import type { SourceType } from '../api/workflow'

/** source_type 五色徽标（PHASE-004 §2.5：不含主蓝，避免与交互语义混淆）。中文标签（审查反馈：裸英文枚举不可读）。 */
export const SOURCE_META: Record<SourceType, { label: string; color: string }> = {
  formula: { label: '公式', color: 'purple' },
  case: { label: '案例', color: 'cyan' },
  standard: { label: '军标', color: 'green' },
  estimated: { label: '估算', color: 'orange' },
  retrieved: { label: '知识', color: 'geekblue' },
}

/** 文本场景的来源类型中文名（溯源抽屉等）。 */
export const SOURCE_CN: Record<string, string> = {
  formula: '公式计算（solver 求值，非 LLM 给数）',
  case: '案例参考（同类设计取值）',
  standard: '军标条款（GJB 等规范）',
  estimated: '经验估算（LLM 提议，需人工复核）',
  retrieved: '知识库检索（A1 检索命中的库内知识）',
}

export function SourceBadge({ type }: { type: SourceType }) {
  const meta = SOURCE_META[type] ?? { label: type, color: 'default' }
  return <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>{meta.label}</Tag>
}

export function ConfidenceBadge({ value }: { value: 'high' | 'medium' | string }) {
  return <Tag color={value === 'high' ? 'green' : 'default'} style={{ marginInlineEnd: 0 }}>{value === 'high' ? '高' : value === 'medium' ? '中' : value}</Tag>
}
