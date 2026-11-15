// T15.2/T15.3：IR3 param_mapping / display_transforms 展示文案（纯函数，便于 vitest 覆盖）。
// 旧快照无字段 → null（不渲染该行）；枚举未知值降级为原文（A-16 不修饰）。
import type { Ir3DisplayTransform, Ir3MappedParam, Ir3ParamMapping } from '../../api/workflow'

const STATUS_LABEL: Record<string, string> = {
  verified: '已验证',
  ambiguous: '歧义',
  unmatched: '未匹配',
}

const DIM_LABEL: Record<string, string> = {
  length: '长度',
  diameter: '孔径',
  width: '宽度',
  height: '高度',
  thickness: '壁厚',
  other: '其他',
}

// 身份依据文案（T15.4）：声明制=声明对象；legacy 几何候选=候选参数（不是声明，A-16 不修饰）；
// 未知 basis 原样透出。
const IDENTITY_NOUN: Record<string, string> = {
  declared_objects: '声明对象',
  geometry_candidates: '候选参数',
}
const IDENTITY_LABEL: Record<string, string> = {
  declared_objects: '声明制身份',
  geometry_candidates: '几何候选身份',
}

/** 映射摘要行：`对象映射：19 个声明对象 · 17 已验证 · 2 歧义 · 0 未匹配（声明制身份 v1）`。
 * legacy 几何候选映射 → `对象映射：3 个候选参数 · …（几何候选身份 v1）`。
 * 无字段/无对象 → null。 */
export function formatParamMapping(pm?: Ir3ParamMapping): string | null {
  if (!pm || !Array.isArray(pm.objects) || pm.objects.length === 0) return null
  const count = (s: string) => pm.objects.filter((o) => o.status === s).length
  const basis = pm.identity_basis ?? 'declared_objects'
  const noun = IDENTITY_NOUN[basis] ?? '对象'
  const label = IDENTITY_LABEL[basis] ?? basis
  const parts = [
    `对象映射：${pm.objects.length} 个${noun}`,
    `${count('verified')} 已验证`,
    `${count('ambiguous')} ${STATUS_LABEL.ambiguous}`,
    `${count('unmatched')} ${STATUS_LABEL.unmatched}`,
  ]
  const version = pm.version ? `（${label} v${pm.version}）` : `（${label}）`
  return `${parts.join(' · ')}${version}`
}

/** 单对象的已验证参数摘要（用于对象级展开场景；当前 Ir3Card 只用汇总行，预留）。 */
export function formatMappedObjectParams(obj: {
  object_id: string
  role: string
  status: string
  params: Ir3MappedParam[]
}): string | null {
  if (obj.status !== 'verified' || obj.params.length === 0) return null
  const parts = obj.params.map((p) => {
    const dim = DIM_LABEL[p.dim_type] ?? p.dim_type
    if (typeof p.measured_mm === 'number') return `${dim} ${p.measured_mm} mm`
    return `${dim} 未测`
  })
  return `${obj.object_id}（${obj.role}）：${parts.join('、')}`
}

/** 显示变换行：`显示变换：参数 P-AMP 物理 1500 → 视觉 450 mm（min(...)）`，多条以「；」相连。
 * 无字段/空数组 → null。 */
export function formatDisplayTransforms(list?: Ir3DisplayTransform[]): string | null {
  if (!list || list.length === 0) return null
  const items = list.map((t) => {
    const label = t.param_id ? `参数 ${t.param_id}` : '（无关联参数）'
    const rule = t.rule ? `，规则 ${t.rule}` : ''
    return `${label} 物理 ${t.physical_mm} → 视觉 ${t.visual_mm} mm${rule}`
  })
  return `显示变换：${items.join('；')}`
}
