// T15.6/C-2 首批读侧纯函数（ADR-014 §5）：批准组合卡视图数据。
// 只读 UI——不承担批准判定（服务端政策校验为准，DEV-001 §3 C-2）。
import type {
  Ir1, Ir2Param, Ir4, IrComboEntry, IrComboPolicy, IrReviewMetadata, ReviewRecord,
} from '../../api/workflow'

/** review_records.metadata 是 jsonb 字符串（'' = 组合契约前历史记录）——解析失败如实返回 null。 */
export function parseReviewMetadata(record: ReviewRecord | undefined | null): IrReviewMetadata | null {
  const raw = record?.metadata
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as IrReviewMetadata
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

/** 最近一次 gate2 批准记录（组合凭证完整）——正式导出与组合卡的数据源。 */
export function latestApprovedGate2(
  reviews: ReviewRecord[] | undefined | null,
): { record: ReviewRecord; metadata: IrReviewMetadata } | null {
  for (const r of reviews ?? []) {
    // 列表按 decided_at DESC 返回——首个 gate2 approved 即最近一次
    if (r.gate !== 'gate2' || r.decision !== 'approved') continue
    const metadata = parseReviewMetadata(r)
    if (metadata?.snapshot_combo && Object.keys(metadata.snapshot_combo).length > 0) {
      return { record: r, metadata }
    }
  }
  return null
}

export interface ComboVersionRow {
  ir_type: 'IR2' | 'IR3' | 'IR4'
  combo_version: number | null
  latest_version: number | null
  snapshot_id: string | null
  sha16: string | null
  valid_at_decision: string | null
  /** 组合钉定版本是否仍是当前最新（false=批准后已产生新版本，正式导出将被服务端拒绝） */
  pinned_is_latest: boolean | null
}

const COMBO_IR_TYPES = ['IR2', 'IR3', 'IR4'] as const

/** 全链版本清单：组合钉定版 vs 当前 latest 对照（钉定失配以行内状态呈现，不做判定拦截）。 */
export function buildVersionList(
  combo: Partial<Record<'IR2' | 'IR3' | 'IR4', IrComboEntry>> | undefined,
  latestVersions: { IR2?: number; IR3?: number; IR4?: number },
): ComboVersionRow[] {
  return COMBO_IR_TYPES.map((ir_type) => {
    const entry: IrComboEntry | undefined = combo?.[ir_type]
    const latest = latestVersions[ir_type] ?? null
    return {
      ir_type,
      combo_version: entry?.version ?? null,
      latest_version: latest,
      snapshot_id: entry?.snapshot_id ?? null,
      sha16: entry?.content_sha256 ? entry.content_sha256.slice(0, 16) : null,
      valid_at_decision: entry?.valid_at_decision ?? null,
      pinned_is_latest: entry?.version == null || latest == null ? null : entry.version === latest,
    }
  })
}

export interface UnresolvedItem {
  source: string
  ref: string
  /** 重复来源去重后仍保留首次出现位置（凭证内条目在前，现场条目在后） */
  from_record: boolean
}

/** 未决项聚合：批准凭证内 unresolved + 现场 IR4 unverified + IR1 missing_items（按 source+ref 去重）。 */
export function aggregateUnresolved(
  policy: IrComboPolicy | undefined,
  ir4: Ir4 | null,
  ir1: Ir1 | null,
): UnresolvedItem[] {
  const merged: UnresolvedItem[] = []
  const seen = new Set<string>()
  const push = (source: string, ref: string, fromRecord: boolean) => {
    const key = `${source}::${ref}`
    if (!ref || seen.has(key)) return
    seen.add(key)
    merged.push({ source, ref, from_record: fromRecord })
  }
  for (const item of policy?.unresolved ?? []) {
    push(item.source, item.ref, true)
  }
  for (const issue of ir4?.issues ?? []) {
    if (issue.severity === 'unverified') push('IR4', issue.constraint_id, false)
  }
  for (const missing of ir1?.missing_items ?? []) {
    push('IR1', missing, false)
  }
  return merged
}

export interface ParamDiffRow {
  param_id: string
  name: string
  unit: string
  combo_value: string
  latest_value: string
  state: 'changed' | 'added' | 'removed' | 'same'
}

const paramKey = (p: Ir2Param) => String(p.id ?? '')
const paramValue = (p: Ir2Param) => `${String(p.value ?? '')}${p.unit ? ` ${p.unit}` : ''}`

/** 全参数 diff（C-2）：批准组合 IR2 为基准，与当前 latest IR2 按 param_id 行级对照。
 *  changed=值/单位不同；added=批准后新增；removed=批准后移除；same=一致。 */
export function diffIr2Params(comboParams: Ir2Param[] | undefined, latestParams: Ir2Param[] | undefined): ParamDiffRow[] {
  const comboList = comboParams ?? []
  const latestList = latestParams ?? []
  const latestById = new Map(latestList.map((p) => [paramKey(p), p]))
  const rows: ParamDiffRow[] = []
  for (const cp of comboList) {
    const lp = latestById.get(paramKey(cp))
    if (!lp) {
      rows.push({ param_id: paramKey(cp), name: String(cp.name ?? ''), unit: String(cp.unit ?? ''),
        combo_value: paramValue(cp), latest_value: '—', state: 'removed' })
    } else if (paramValue(cp) !== paramValue(lp)) {
      rows.push({ param_id: paramKey(cp), name: String(cp.name ?? ''), unit: String(cp.unit ?? ''),
        combo_value: paramValue(cp), latest_value: paramValue(lp), state: 'changed' })
    } else {
      rows.push({ param_id: paramKey(cp), name: String(cp.name ?? ''), unit: String(cp.unit ?? ''),
        combo_value: paramValue(cp), latest_value: paramValue(lp), state: 'same' })
    }
  }
  const comboIds = new Set(comboList.map(paramKey))
  for (const lp of latestList) {
    if (!comboIds.has(paramKey(lp))) {
      rows.push({ param_id: paramKey(lp), name: String(lp.name ?? ''), unit: String(lp.unit ?? ''),
        combo_value: '—', latest_value: paramValue(lp), state: 'added' })
    }
  }
  return rows
}
