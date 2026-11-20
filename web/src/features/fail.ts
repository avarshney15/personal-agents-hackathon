// How the Part 9 screens word a failed call. Live mode never falls back to sample data.
import { ApiError, errorText } from '../errors'

/** No answer, no route, or a server error. */
export function unavailable(err: unknown): boolean {
  const status = err instanceof ApiError ? err.status : 0
  return status === 0 || status === 404 || status === 405 || status >= 500
}

/** The screen's own line ("Couldn't load your week.") when the route is unavailable;
 *  the server's detail otherwise (a wrong code, a bad email). */
export function failText(err: unknown, fallback: string): string {
  if (unavailable(err)) return fallback
  const detail = err instanceof ApiError ? err.detail : ''
  return detail || errorText(err) || fallback
}
