import { get, post } from './client'
import type { LoginResult, MeInfo } from './types'

export function login(username: string, password: string) {
  return post<LoginResult>('/auth/login', { username, password })
}

export function me() {
  return get<MeInfo>('/auth/me')
}
