import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type {
  FindingOut,
  FindingUpdate,
  RepoSettingsUpdate,
  ReviewOut,
  PullRequestState,
} from './types'

// Query keys in one place. TanStack Query caches by key: two components asking for the same
// key share one request and one cache entry, and invalidating a key refetches everyone who
// uses it. Hierarchical keys let us invalidate a whole family (['pulls'] covers every page and
// filter of every repository's PR list).
export const keys = {
  repos: (page: number, q: string) => ['repos', { page, q }] as const,
  repo: (id: string) => ['repo', id] as const,
  pulls: (repoId: string, state: PullRequestState | undefined, page: number) =>
    ['pulls', repoId, { state, page }] as const,
  pull: (id: string) => ['pull', id] as const,
  review: (pullId: string) => ['review', pullId] as const,
  usage: (from: string, to: string) => ['usage', { from, to }] as const,
}

export const PAGE_SIZE = 20

export function useRepos(page: number, q: string) {
  return useQuery({
    queryKey: keys.repos(page, q),
    queryFn: () => api.repos({ limit: PAGE_SIZE, offset: page * PAGE_SIZE, q }),
    placeholderData: keepPreviousData, // keep showing the old page while the next one loads
  })
}

export function useRepo(id: string) {
  return useQuery({ queryKey: keys.repo(id), queryFn: () => api.repo(id) })
}

export function usePulls(repoId: string, state: PullRequestState | undefined, page: number) {
  return useQuery({
    queryKey: keys.pulls(repoId, state, page),
    queryFn: () => api.pulls(repoId, { state, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    placeholderData: keepPreviousData,
  })
}

export function usePull(id: string) {
  return useQuery({ queryKey: keys.pull(id), queryFn: () => api.pull(id) })
}

export function useReview(pullId: string) {
  return useQuery({
    queryKey: keys.review(pullId),
    queryFn: () => api.review(pullId),
    // A queued or running review changes by itself: poll until it finishes.
    refetchInterval: (query) => {
      const status = query.state.data?.run.status
      return status === 'queued' || status === 'running' ? 5000 : false
    },
  })
}

export function useUsage(from: string, to: string, enabled = true) {
  return useQuery({
    queryKey: keys.usage(from, to),
    queryFn: () => api.usage({ from, to }),
    enabled,
  })
}

export function useRequestIndex(repoId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.requestIndex(repoId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.repo(repoId) })
      void queryClient.invalidateQueries({ queryKey: ['repos'] })
    },
  })
}

export function useUpdateSettings(repoId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (change: RepoSettingsUpdate) => api.updateSettings(repoId, change),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.repo(repoId) }),
  })
}

// --- optimistic feedback on a finding ------------------------------------------------------

/** The fields a PATCH can change; what we snapshot for rollback. */
type Editable = Pick<FindingOut, 'user_feedback' | 'dismissed'>

/** Apply a change to ONE finding inside the cached review (a pure function, easy to test). */
export function patchFindingInReview(
  review: ReviewOut | null | undefined,
  findingId: string,
  fields: Partial<Editable>,
): ReviewOut | null | undefined {
  if (!review) return review
  return {
    ...review,
    findings: review.findings.map((f) => (f.id === findingId ? { ...f, ...fields } : f)),
  }
}

/** The change a user asked for, as the fields it will set on the finding. */
function optimisticFields(change: FindingUpdate): Partial<Editable> {
  const fields: Partial<Editable> = {}
  if ('user_feedback' in change) fields.user_feedback = change.user_feedback ?? null
  if (change.dismissed !== undefined && change.dismissed !== null) {
    fields.dismissed = change.dismissed
  }
  return fields
}

/**
 * Useful / not useful / dismiss with an OPTIMISTIC update:
 *   onMutate  - the click: stop in-flight refetches, remember the finding's old values, and
 *               write the new ones into the cache, so the UI changes instantly;
 *   onError   - the server said no: put back ONLY this finding's old values (not the whole
 *               old snapshot, so a second click on another finding is not undone) and tell
 *               the caller through `onFailed`;
 *   onSettled - success or failure, refetch so the cache ends up equal to the server.
 */
export function useUpdateFinding(pullId: string, onFailed?: (error: Error) => void) {
  const queryClient = useQueryClient()
  const key = keys.review(pullId)

  return useMutation({
    mutationFn: ({ id, change }: { id: string; change: FindingUpdate }) =>
      api.updateFinding(id, change),

    onMutate: async ({ id, change }) => {
      await queryClient.cancelQueries({ queryKey: key })
      const before = queryClient.getQueryData<ReviewOut | null>(key)
      const current = before?.findings.find((f) => f.id === id)
      const previous: Partial<Editable> = current
        ? { user_feedback: current.user_feedback, dismissed: current.dismissed }
        : {}
      queryClient.setQueryData<ReviewOut | null>(key, (old) =>
        patchFindingInReview(old, id, optimisticFields(change)),
      )
      return { previous }
    },

    onError: (error, { id }, context) => {
      if (context) {
        queryClient.setQueryData<ReviewOut | null>(key, (old) =>
          patchFindingInReview(old, id, context.previous),
        )
      }
      onFailed?.(error)
    },

    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: key })
      void queryClient.invalidateQueries({ queryKey: ['pulls'] }) // open-findings counts
    },
  })
}
