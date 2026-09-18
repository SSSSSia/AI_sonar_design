import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface AuthUser {
  token: string
  username: string
  displayName: string
  roles: string[]
}

interface AuthState {
  token: string | null
  username: string | null
  displayName: string | null
  roles: string[]
  setAuth: (u: AuthUser) => void
  clear: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      username: null,
      displayName: null,
      roles: [],
      setAuth: (u) =>
        set({ token: u.token, username: u.username, displayName: u.displayName, roles: u.roles }),
      clear: () => set({ token: null, username: null, displayName: null, roles: [] }),
    }),
    { name: 'sonarcad-auth' },
  ),
)

export function hasAnyRole(roles: string[], allowed: string[]): boolean {
  return roles.some((r) => allowed.includes(r))
}
