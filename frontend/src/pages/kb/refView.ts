// T16.1 范例库管理展示纯函数（对齐 paramMapping/comboView 先例；A-16：未知值不修饰原样透出）。
import type { RefImportItem, RefScriptRow, RefScriptStatus } from '../../api/references'

/** 状态 Tag 展示（label + AntD color）；未知状态原样透出。 */
export function refStatusMeta(status: string): { label: string; color: string } {
  const known: Record<RefScriptStatus, { label: string; color: string }> = {
    pending: { label: '待确认', color: 'orange' },
    active: { label: '启用中', color: 'green' },
    disabled: { label: '已停用', color: 'default' },
  }
  return known[status as RefScriptStatus] ?? { label: status, color: 'default' }
}

/** 来源类型展示；未上线类型原样透出（A-16）。 */
export function refSourceLabel(sourceType: string): string {
  const known: Record<string, string> = {
    catalog_import: '目录导入',
    manual_upload: '上传登记',
    deposit: '批准沉淀',
  }
  return known[sourceType] ?? sourceType
}

/** sha256 短显（前 n 位 + …）；空值不造值。 */
export function shortSha(sha: string | null | undefined, n = 8): string {
  const s = String(sha ?? '')
  return s ? `${s.slice(0, n)}…` : '—'
}

/** 适用范围一行文案：domain / domain_rules / 类型标签；全空返回 null（不渲染）。 */
export function formatRefScope(row: Pick<RefScriptRow, 'domain' | 'domain_rules' | 'sonar_types'>): string | null {
  const parts: string[] = []
  if (row.domain) parts.push(String(row.domain))
  if (row.domain_rules) parts.push(String(row.domain_rules))
  if (Array.isArray(row.sonar_types) && row.sonar_types.length) parts.push(row.sonar_types.join(' / '))
  return parts.length ? parts.join(' · ') : null
}

/** 最新版本 id 集合：同 ref_id 下 version 最大的行（列表已按 ref_id, version DESC 返回，取首见）。 */
export function latestVersionIds(rows: RefScriptRow[]): Set<string> {
  const latest = new Set<string>()
  const seen = new Map<string, number>()
  const ordered = [...rows].sort((a, b) =>
    a.ref_id === b.ref_id ? b.version - a.version : a.ref_id.localeCompare(b.ref_id),
  )
  for (const r of ordered) {
    if (!seen.has(r.ref_id)) {
      seen.set(r.ref_id, r.version)
      latest.add(r.id)
    }
  }
  return latest
}

/** 导入对照报告摘要文案（hash/内容/状态一致验收的 UI 呈现）。 */
export function importReportText(items: RefImportItem[], created: number, unchanged: number): string {
  const errors = items.filter((i) => i.action === 'error')
  const base = `导入完成：新增 ${created} 条（待确认），内容一致 ${unchanged} 条`
  if (errors.length) {
    return `${base}；失败 ${errors.length} 条：${errors.map((e) => `${e.ref_id}(${e.error ?? '未知'})`).join('；')}`
  }
  return base
}
