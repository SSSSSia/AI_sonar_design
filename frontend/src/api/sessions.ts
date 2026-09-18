import { get, post } from './client'
import type { DesignSession, PageResult, Project } from './types'

export function listSessions(params?: { projectId?: string; page?: number; size?: number }) {
  return get<PageResult<DesignSession>>('/workflow/sessions', params)
}

export function getSession(id: string) {
  return get<DesignSession>(`/workflow/sessions/${id}`)
}

export function createSession(body: { projectId: string; title: string; requirement: string }) {
  return post<DesignSession>('/workflow/sessions', body)
}

export function getAllowedEvents(id: string) {
  return get<{ events: string[] }>(`/workflow/sessions/${id}/allowed-events`)
}

export function fireEvent(id: string, event: 'START' | 'PAUSE' | 'RESUME' | 'COMPLETE' | 'FAIL' | 'RETRY') {
  return post<DesignSession>(`/workflow/sessions/${id}/events`, { event })
}

export function listProjects(params?: { page?: number; size?: number; keyword?: string }) {
  return get<PageResult<Project>>('/projects', params)
}

export function createProject(body: { name: string; code?: string; description?: string }) {
  return post<Project>('/projects', body)
}
