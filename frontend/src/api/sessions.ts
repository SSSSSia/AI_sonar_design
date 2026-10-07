import { del, get, patch, post } from './client'
import type { DesignSession, PageResult, Project } from './types'

export function listSessions(params?: { projectId?: string; page?: number; size?: number }) {
  return get<PageResult<DesignSession>>('/workflow/sessions', params)
}

export function getSession(id: string) {
  return get<DesignSession>(`/workflow/sessions/${id}`)
}

export function createSession(body: {
  projectId: string
  title: string
  requirement: string
  /** 知识库多选（PHASE-006）；不传/空数组 = Agent 自主决策模式 */
  kbIds?: string[]
}) {
  return post<DesignSession>('/workflow/sessions', body)
}

export function getAllowedEvents(id: string) {
  return get<{ events: string[] }>(`/workflow/sessions/${id}/allowed-events`)
}

export function fireEvent(id: string, event: 'START' | 'PAUSE' | 'RESUME' | 'COMPLETE' | 'FAIL' | 'RETRY') {
  return post<DesignSession>(`/workflow/sessions/${id}/events`, { event })
}

/** 删除会话（admin）：终态直接删；运行中/暂停须 force（任务可能仍在途）。 */
export function deleteSession(id: string, force = false) {
  return del<{ deleted: boolean; ir_snapshots: number; review_records: number; trace_chain_rows: number; minio?: Record<string, unknown> }>(
    `/workflow/sessions/${id}${force ? '?force=true' : ''}`,
  )
}

export interface BatchDeleteResult {
  results: { id: string; deleted: boolean; error?: string }[]
  deleted_count: number
  failed_count: number
}

/** 批量删除会话（admin）：逐条独立事务，部分失败不拖垮整批（结果带逐条明细）。 */
export function deleteSessions(ids: string[], force = false) {
  return del<BatchDeleteResult>('/workflow/sessions', { ids: ids.join(','), ...(force ? { force: 'true' } : {}) })
}

export function listProjects(params?: { page?: number; size?: number; keyword?: string }) {
  return get<PageResult<Project>>('/projects', params)
}

export function createProject(body: { name: string; code?: string; description?: string }) {
  return post<Project>('/projects', body)
}

/** 删除空项目（admin；项目下仍有会话时后端 409 拒绝）。 */
export function deleteProject(id: string) {
  return del<{ id: string; deleted: boolean; name: string }>(`/projects/${id}`)
}

/** 编辑项目（名称/代号/说明局部更新；空串表示清空该字段）。 */
export function patchProject(id: string, body: { name?: string; code?: string; description?: string }) {
  return patch<Project>(`/projects/${id}`, body)
}

// ---- 项目成员管理（T13.4，§9.4 项目级粒度；管理 = admin/owner） ----

export interface ProjectMember {
  user_id: string
  role: string
  username: string | null
  display_name: string | null
  created_at?: string
}

export function listProjectMembers(id: string) {
  return get<ProjectMember[]>(`/projects/${id}/members`)
}

/** 添加成员（按用户名解析；角色固定 member，owner 由 projects.owner_id 承担）。 */
export function addProjectMember(id: string, username: string) {
  return post<ProjectMember>(`/projects/${id}/members`, { username })
}

export function removeProjectMember(id: string, userId: string) {
  return del<{ removed: boolean }>(`/projects/${id}/members/${userId}`)
}
