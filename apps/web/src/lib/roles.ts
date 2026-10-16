import type { MeResponse } from '../api/types'

/** True when the user is admin or owner somewhere: the analytics page is for them. */
export function isAnyAdmin(me: MeResponse | null): boolean {
  return !!me?.installations.some((i) => i.role === 'admin' || i.role === 'owner')
}
