import { get, http, patch, post } from './client'

// ---- 参考代码范例库（T16.1 B1，ADR-015：ref_scripts 表 V21 + MinIO ref-scripts 桶）----
// 状态机 pending|active|disabled：检索只读 active；pending 激活走 confirm；无删除（撤回走状态）。

export type RefScriptStatus = 'pending' | 'active' | 'disabled'

export interface RefScriptRow {
  id: string
  ref_id: string
  version: number
  file_name: string
  title?: string | null
  description?: string | null
  domain?: string | null
  domain_rules?: string | null
  sonar_types: string[]
  keywords: string[]
  bucket: string
  object_key: string
  content_sha256: string
  status: RefScriptStatus
  source_type: 'catalog_import' | 'manual_upload' | 'deposit'
  source_note?: string | null
  created_by?: string | null
  created_at: string
  updated_at: string
  [k: string]: unknown
}

export interface RefImportItem {
  ref_id: string
  file: string
  action: 'created' | 'unchanged' | 'error'
  row_id?: string
  version?: number
  status?: string
  content_sha256?: string
  error?: string
}

export function listRefScripts(refId?: string) {
  const params: Record<string, unknown> = {}
  if (refId) params.ref_id = refId
  return get<{ rows: RefScriptRow[] }>('/kb/references', params)
}

export function getRefContent(rowId: string) {
  return get<{ row_id: string; ref_id: string; version: number; file_name: string; content: string; content_sha256: string; sha256_match: boolean }>(
    `/kb/references/${rowId}/content`,
  )
}

/** 上传登记新脚本/新版本（multipart）→ pending 待确认。 */
export async function uploadRefScript(
  file: File,
  meta: {
    ref_id?: string
    title?: string
    description?: string
    domain?: string
    domain_rules?: string
    sonar_types?: string[]
    keywords?: string[]
  },
) {
  const form = new FormData()
  form.append('file', file)
  if (meta.ref_id) form.append('ref_id', meta.ref_id)
  if (meta.title) form.append('title', meta.title)
  if (meta.description) form.append('description', meta.description)
  if (meta.domain) form.append('domain', meta.domain)
  if (meta.domain_rules) form.append('domain_rules', meta.domain_rules)
  for (const t of meta.sonar_types ?? []) form.append('sonar_types', t)
  for (const k of meta.keywords ?? []) form.append('keywords', k)
  const resp = await http.post('/kb/references', form)
  return resp.data as { row: RefScriptRow }
}

/** 元数据编辑 + 启停（active↔disabled；pending 激活须走 confirm）。 */
export function patchRefScript(
  rowId: string,
  body: {
    title?: string
    description?: string
    domain?: string
    domain_rules?: string
    sonar_types?: string[]
    keywords?: string[]
    status?: 'active' | 'disabled'
  },
) {
  return patch<{ row: RefScriptRow }>(`/kb/references/${rowId}`, body)
}

/** 人工确认入库（pending→active；同 ref_id 其余 active 自动停用，单活互斥）。 */
export function confirmRefScript(rowId: string) {
  return post<{ row: RefScriptRow }>(`/kb/references/${rowId}/confirm`)
}

/** 文件源幂等导入（hash/内容/状态对照报告；导入不自动确认）。 */
export function importRefCatalog(dir?: string) {
  return post<{
    source_dir: string
    catalog_version: string
    items: RefImportItem[]
    created: number
    unchanged: number
  }>('/kb/references/import-catalog', dir ? { dir } : {})
}

// ---- B3 候选沉淀（T16.3，ADR-015 v1.1 §9）：真实 gate2 批准 → pending 范例候选 ----

/** 沉淀候选（扫描结果条目）：depositable=false 时 reason 如实（A-16）。 */
export interface DepositCandidate {
  session_id: string
  record_id: string
  session_title?: string | null
  decided_at?: string | null
  reviewer_id?: string | null
  depositable: boolean
  ir3_version?: number
  ir4_overall?: string
  script_sha256?: string
  ref_id?: string
  already_deposited?: boolean
  existing_row?: RefScriptRow | null
}

export interface DepositBlocked {
  session_id: string
  record_id: string
  session_title?: string | null
  decided_at?: string | null
  reason: string
  detail?: unknown
}

/** 扫描可沉淀的 gate2 批准会话（判定链+组合校验+质量门+幂等对照；不含重放）。 */
export function listDepositCandidates(limit = 50) {
  return get<{ candidates: DepositCandidate[]; blocked: DepositBlocked[] }>(
    '/kb/references/deposit-candidates',
    { limit },
  )
}

/** 执行沉淀：真沙箱重放（失败 409 不落行）→ pending 行 + 凭证；同内容幂等返回既有行。 */
export function depositFromRecord(sessionId: string, recordId: string) {
  return post<{ already: boolean; row: RefScriptRow; ref_id: string; script_sha256: string }>(
    '/kb/references/deposit',
    { session_id: sessionId, record_id: recordId },
  )
}
