import { createContext, use } from 'react'
import type { MeResponse } from '../api/types'

export const ME_QUERY_KEY = ['me'] as const

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous' | 'error'

export interface AuthState {
  status: AuthStatus
  /** The signed-in user and their installations; null unless status is 'authenticated'. */
  me: MeResponse | null
  /** Set when the API could not be reached (not when the user is simply signed out). */
  error: Error | null
  logout: () => Promise<void>
  loggingOut: boolean
  retry: () => void
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const auth = use(AuthContext)
  if (!auth) throw new Error('useAuth must be used inside <AuthProvider>')
  return auth
}
