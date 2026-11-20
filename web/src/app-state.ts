// App-wide state shared by the screens: the current user, starting an RSVP, and small caches.
import { createContext, useContext } from 'react'
import type { Event, FeedResponse, User } from './types'

export interface AppState {
  me: User
  setMe: (u: User) => void
  reloadMe: () => Promise<void>
  /** Starts an RSVP, or sends the user to Verify first. */
  rsvp: (event: Event) => Promise<void>
  /** Tell the user when a run they left finishes. */
  watchRun: (runId: string, title: string) => void
}

export const AppContext = createContext<AppState | null>(null)

export function useApp(): AppState {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp outside AppContext')
  return ctx
}

// Events seen in the feed, so sheets and RSVP headers show at once.
const events = new Map<string, Event>()
export const rememberEvents = (list: Event[]) => list.forEach((e) => events.set(e.id, e))
export const knownEvent = (id: string) => events.get(id)

// The last feed, so returning to For you doesn't flash empty.
let feed: FeedResponse | null = null
export const lastFeed = () => feed
export const saveFeed = (f: FeedResponse) => {
  feed = f
  rememberEvents(f.picks.map((p) => p.event))
}

// Per-browser flags. Storage can be unavailable (private mode), so every access is guarded.
export function flag(name: string): boolean {
  try {
    return window.localStorage.getItem(`ea.${name}`) === '1'
  } catch {
    return false
  }
}
export function setFlag(name: string, on = true) {
  try {
    if (on) window.localStorage.setItem(`ea.${name}`, '1')
    else window.localStorage.removeItem(`ea.${name}`)
  } catch {
    // ignore
  }
}

export function sessionValue(name: string, value?: string | null): string | null {
  try {
    if (value === undefined) return window.sessionStorage.getItem(`ea.${name}`)
    if (value === null) window.sessionStorage.removeItem(`ea.${name}`)
    else window.sessionStorage.setItem(`ea.${name}`, value)
  } catch {
    // ignore
  }
  return null
}
