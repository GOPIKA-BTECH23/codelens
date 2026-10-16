import { ApiError } from '../api/client'

/** A message safe to show a user, for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : 'Something went wrong'
}
