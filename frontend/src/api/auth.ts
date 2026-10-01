import { del, get, patch, post, put } from './client'
import type { LoginResult, MeInfo } from './types'

export function login(username: string, password: string) {
  return post<LoginResult>('/auth/login', { username, password })
}

export function me() {
  return get<MeInfo>('/auth/me')
}

// ---- 用户管理（T11.2，admin-only；经 gateway 透传 Bearer，auth 侧二次验签 + admin 门禁） ----

export interface ManagedUser {
  id: string
  username: string
  displayName?: string | null
  email?: string | null
  status: 'active' | 'disabled'
  roles: string[]
  created_at?: string
}

export function listUsers() {
  return get<ManagedUser[]>('/auth/users')
}

export function createUser(body: {
  username: string
  password: string
  displayName?: string
  email?: string
  roles?: string[]
}) {
  return post<ManagedUser>('/auth/users', body)
}

export function patchUser(id: string, body: { status: 'active' | 'disabled' }) {
  return patch<ManagedUser>(`/auth/users/${id}`, body)
}

export function resetUserPassword(id: string, password: string) {
  return post<{ id: string; reset: boolean }>(`/auth/users/${id}/reset-password`, { password })
}

export function setUserRoles(id: string, roles: string[]) {
  return put<{ id: string; roles: string[] }>(`/auth/users/${id}/roles`, { roles })
}

export function deleteUser(id: string) {
  return del<{ id: string; deleted: boolean }>(`/auth/users/${id}`)
}
