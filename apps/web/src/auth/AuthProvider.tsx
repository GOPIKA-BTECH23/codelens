import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useMemo } from 'react'
import { ApiError, api } from '../api/client'
import type { MeResponse } from '../api/types'
import { AuthContext, type AuthState, ME_QUERY_KEY } from './context'

/** GET /me, where "not signed in" is an answer (null), not an error. */
async function fetchMe(): Promise<MeResponse | null> {
  try {
    return await api.me()
  } catch (error) {
    // The API client has already tried a refresh; a 401 now means the session is over.
    if (error instanceof ApiError && error.status === 401) return null
    throw error
  }
}

/**
 * Who is signed in, from GET /me (a TanStack Query, so it is cached and shared).
 *
 * The browser cannot know whether it is logged in: the session cookies are httpOnly, so
 * JavaScript cannot see them. Asking the API is the only source of truth.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const me = useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: fetchMe,
    retry: false,
    staleTime: 60_000,
  })

  const logoutMutation = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      // Drop every cached response that belonged to this user, then record "signed out".
      queryClient.clear()
      queryClient.setQueryData(ME_QUERY_KEY, null)
    },
  })
  const { mutateAsync: logoutAsync, isPending: loggingOut } = logoutMutation
  const { data, isPending, isError, error, refetch } = me

  const value = useMemo<AuthState>(() => {
    const base = {
      logout: async () => {
        await logoutAsync().catch(() => undefined) // cookies are cleared either way
      },
      loggingOut,
      retry: () => void refetch(),
    }
    if (isPending) return { ...base, status: 'loading', me: null, error: null }
    if (isError) return { ...base, status: 'error', me: null, error }
    if (data) return { ...base, status: 'authenticated', me: data, error: null }
    return { ...base, status: 'anonymous', me: null, error: null }
  }, [data, isPending, isError, error, refetch, logoutAsync, loggingOut])

  return <AuthContext value={value}>{children}</AuthContext>
}
