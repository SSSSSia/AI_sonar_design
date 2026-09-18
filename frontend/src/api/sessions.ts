import { get } from './client'
import type { DesignSession, PageResult, Project } from './types'

export function listSessions(params?: { projectId?: string; page?: number; size?: number }) {
  return get<PageResult<DesignSession>>('/workflow/sessions', params)
}

export function getSession(id: string) {
  return get<DesignSession>(`/workflow/sessions/${id}`)
}

export function listProjects(params?: { page?: number; size?: number; keyword?: string }) {
  return get<PageResult<Project>>('/projects', params)
}
