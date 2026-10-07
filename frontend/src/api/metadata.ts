import { del, get, patch, post } from './client'

// ---- 基础元数据管理（T13.7，§5.16 + 附录 D；写端点 admin） ----

export type MetadataTable = 'sonar_types' | 'freq_bands' | 'perf_specs' | 'prompt_templates'

export interface MetadataRow {
  id: number
  [key: string]: unknown
}

export function listMetadata(table: MetadataTable, enabledOnly = false) {
  return get<{ rows: MetadataRow[] }>(`/metadata/${table}`, enabledOnly ? { enabled_only: true } : undefined)
}

export function createMetadata(table: MetadataTable, fields: Record<string, unknown>) {
  return post<{ row: MetadataRow }>(`/metadata/${table}`, fields)
}

export function updateMetadata(table: MetadataTable, id: number, fields: Record<string, unknown>) {
  return patch<{ row: MetadataRow }>(`/metadata/${table}/${id}`, { fields })
}

export function deleteMetadata(table: MetadataTable, id: number) {
  return del<{ deleted: boolean }>(`/metadata/${table}/${id}`)
}
