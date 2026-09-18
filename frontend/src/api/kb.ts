import { del, get, http, patch, post } from './client'

// ---- 文档 ----
export interface KbDocument {
  doc_id: string
  filename: string
  format: string
  status: string
  units?: number
  chunks?: number
  ocr_pages?: number
  ocr_pending?: number
  error?: string
  uploaded_at?: string
}

export function listDocuments() {
  return get<{ documents: KbDocument[] }>('/kb/documents')
}

export async function uploadDocument(file: File) {
  const form = new FormData()
  form.append('file', file)
  const resp = await http.post('/kb/documents', form, { timeout: 300_000 })
  return resp.data
}

export function deleteDocument(docId: string) {
  return del<{ doc_id: string; deleted: boolean; cleanup: Record<string, unknown> }>(`/kb/documents/${docId}`)
}

export function chunkDocument(docId: string, size?: number, overlap?: number) {
  return post<{ doc_id: string; chunks: number }>(`/kb/documents/${docId}/chunk`, { size, overlap })
}

export function startExtract(docId: string, entityTypes?: string[]) {
  return post<{ doc_id: string; task_id: string }>(`/kb/documents/${docId}/extract`, { entity_types: entityTypes })
}

export function getTask(taskId: string) {
  return get<{ task_id: string; state: string; result?: unknown; error?: string }>(`/kb/extract/${taskId}`)
}

export function listExtractions(docId: string, status?: 'pending' | 'confirmed' | 'rejected') {
  return get<{ extractions: Record<string, unknown>[] }>(`/kb/documents/${docId}/extractions`, { status })
}

export function decideExtraction(extractionId: string, decision: 'confirmed' | 'rejected') {
  return patch<{ extraction_id: string; status: string }>(`/kb/extractions/${extractionId}`, { status: decision })
}

export function startBuild(docIds?: string[]) {
  return post<{ task_id: string; state: string }>(`/kb/build`, { doc_ids: docIds })
}

export function getBuildTask(taskId: string) {
  return get<{ task_id: string; state: string; result?: unknown; error?: string }>(`/kb/build/${taskId}`)
}

// ---- 三库 ----
export type RegistryKind = 'formulas' | 'constraints' | 'standards'
export interface RegistryRow {
  row_id?: string
  id?: string
  key?: string
  name?: string
  enabled?: boolean
  confidence?: string
  [k: string]: unknown
}

export function listRegistry(kind: RegistryKind) {
  return get<{ rows: RegistryRow[] }>(`/kb/registry/${kind}`)
}

export function createRegistryRow(kind: RegistryKind, fields: Record<string, unknown>) {
  return post<{ row_id: string }>(`/kb/registry/${kind}`, { fields })
}

export function updateRegistryRow(kind: RegistryKind, rowId: string, fields: Record<string, unknown>) {
  return patch<RegistryRow>(`/kb/registry/${kind}/${rowId}`, { fields })
}

export function deleteRegistryRow(kind: RegistryKind, rowId: string) {
  return del<{ deleted: boolean }>(`/kb/registry/${kind}/${rowId}`)
}

// ---- LLM 供应商 ----
export interface LlmProvider {
  name: string
  base_url: string
  model?: string
  priority?: number
  timeout_s?: number
  enabled?: boolean
  api_key_masked?: string
}

export function listProviders() {
  return get<{ providers: LlmProvider[] }>('/kb/llm-providers')
}

export function createProvider(body: { name: string; base_url: string; api_key: string; model?: string; priority?: number; timeout_s?: number; enabled?: boolean }) {
  return post<{ name: string }>('/kb/llm-providers', body)
}

export function updateProvider(name: string, fields: Record<string, unknown>) {
  return patch<{ name: string }>(`/kb/llm-providers/${name}`, fields)
}

export function deleteProvider(name: string) {
  return del<{ deleted: boolean }>(`/kb/llm-providers/${name}`)
}

// ---- 图谱 ----
export interface GraphData {
  nodes: { key: string; label: string; name?: string; type?: string; deprecated?: boolean; source_doc?: string; source_session?: string }[]
  edges: { source: string; target: string; relation?: string }[]
  truncated?: boolean
}

export function getGraph(limit = 300) {
  return get<GraphData>(`/kb/graph`, { limit })
}

export function getNeighborhood(key: string, depth = 2, limit = 100) {
  return get<GraphData>(`/kb/graph/neighborhood`, { key, depth, limit })
}

// ---- 状态（系统管理页） ----
export interface KbStatus {
  kb_levels_enabled?: boolean
  graphrag_sdk?: boolean
  dynamic_corpus_chunks?: number
  documents?: number
  chunks?: number
  extractions_confirmed?: number
  extractions_pending?: number
  milvus_available?: boolean
  neo4j_available?: boolean
  [k: string]: unknown
}

export function getKbStatus() {
  return get<KbStatus>('/kb/status')
}
