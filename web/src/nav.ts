// App navigation. Routes live in the URL hash (#/discover, #/event/abc), so the browser's
// back button and reloads work. The team dashboard is the one real path: /ops.
import { useSyncExternalStore } from 'react'

export type Route =
  | { name: 'welcome' }
  | { name: 'verify' }
  | { name: 'connect' }
  | { name: 'taste' }
  | { name: 'week' }
  | { name: 'discover' }
  | { name: 'ask' }
  | { name: 'event'; id: string }
  | { name: 'rsvp'; runId: string }
  | { name: 'needs-you'; runId: string }
  | { name: 'going'; runId: string }
  | { name: 'activity' }
  | { name: 'memory' }
  | { name: 'profile' }
  | { name: 'sites' }
  | { name: 'ops' }

const SIMPLE = new Set([
  'welcome', 'verify', 'connect', 'taste', 'week', 'discover', 'ask', 'activity', 'memory', 'profile', 'sites',
])

export const DEFAULT_ROUTE: Route = { name: 'discover' }

/** "#/event/abc" for a route. Ops has no hash; use hrefFor for links. */
export function routeToHash(r: Route): string {
  switch (r.name) {
    case 'event':
      return `#/event/${encodeURIComponent(r.id)}`
    case 'rsvp':
    case 'needs-you':
    case 'going':
      return `#/${r.name}/${encodeURIComponent(r.runId)}`
    default:
      return `#/${r.name}`
  }
}

/** An href for <a> tags. */
export function hrefFor(r: Route): string {
  return r.name === 'ops' ? '/ops' : `/${routeToHash(r)}`
}

export function parseLocation(pathname: string, hash: string): Route {
  if (pathname.replace(/\/+$/, '') === '/ops') return { name: 'ops' }
  const [name = '', raw] = hash.replace(/^#\/?/, '').split('/')
  const id = raw ? decodeURIComponent(raw) : ''
  if (SIMPLE.has(name)) return { name } as Route
  if (name === 'event' && id) return { name, id }
  if ((name === 'rsvp' || name === 'needs-you' || name === 'going') && id) return { name, runId: id }
  return DEFAULT_ROUTE
}

// ---------------------------------------------------------------- store

let current: Route = parseLocation(window.location.pathname, window.location.hash)
let depth = 0 // in-app history entries we pushed, so back() never leaves the app
const listeners = new Set<() => void>()

function sync() {
  const next = parseLocation(window.location.pathname, window.location.hash)
  if (JSON.stringify(next) !== JSON.stringify(current)) {
    current = next
    listeners.forEach((l) => l())
  }
}

window.addEventListener('hashchange', sync)
window.addEventListener('popstate', () => {
  depth = Math.max(0, depth - 1)
  sync()
})

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

const getSnapshot = () => current

export function navigate(r: Route, opts: { replace?: boolean } = {}) {
  const url = r.name === 'ops' ? '/ops' : `/${window.location.search}${routeToHash(r)}`
  if (opts.replace) {
    window.history.replaceState(null, '', url)
  } else {
    window.history.pushState(null, '', url)
    depth += 1
  }
  sync()
  window.scrollTo(0, 0)
}

export function back() {
  if (depth > 0) window.history.back()
  else navigate(DEFAULT_ROUTE, { replace: true })
}

export function useNav(): { route: Route; navigate: (r: Route) => void; back: () => void } {
  const route = useSyncExternalStore(subscribe, getSnapshot)
  return { route, navigate, back }
}
