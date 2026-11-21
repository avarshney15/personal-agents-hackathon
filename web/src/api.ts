// One function per route in backend/API.md. Every path is under /api, which Vite proxies to
// http://localhost:8000 in dev.
//
// Mock mode: with VITE_MOCK=1, ?mock=1 in the URL, or when GET /api/health can't be reached,
// every call is answered by src/mock.ts instead (sample data from the design canvas).
import { ApiError } from './errors'
import type {
  ActivityResponse,
  AskBefore,
  AskResponse,
  EventDetail,
  Event,
  FactGroups,
  FeedResponse,
  Ok,
  OpsSummary,
  RsvpRun,
  Site,
  SiteConnection,
  TasteRequest,
  User,
  VerifySession,
  VerifyStatus,
  WeekResponse,
} from './types'

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE'
export type ApiMode = 'live' | 'mock'

let modePromise: Promise<ApiMode> | null = null

async function detectMode(): Promise<ApiMode> {
  if (import.meta.env.VITE_MOCK === '1') return 'mock'
  if (new URLSearchParams(window.location.search).get('mock') === '1') return 'mock'
  try {
    const res = await fetch('/api/health', { signal: AbortSignal.timeout(3000) })
    if (!res.ok) return 'mock'
    const body = (await res.json()) as Partial<Ok>
    return body.ok ? 'live' : 'mock'
  } catch {
    return 'mock'
  }
}

/** Resolves once: "live" when the backend answers /api/health, otherwise "mock". */
export function apiMode(): Promise<ApiMode> {
  if (!modePromise) {
    modePromise = detectMode().then((mode) => {
      document.documentElement.dataset.api = mode
      if (mode === 'mock') console.info('[Event Agent] Backend not reachable: using mock data (src/mock.ts).')
      return mode
    })
  }
  return modePromise
}

async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  if ((await apiMode()) === 'mock') {
    const { mockRequest } = await import('./mock')
    return (await mockRequest(method, path, body)) as T
  }
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, '')
  }
  if (!res.ok) throw new ApiError(res.status, await errorDetail(res))
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') return body.detail
  } catch {
    // not JSON
  }
  return ''
}

const enc = encodeURIComponent

// ---------------------------------------------------------------- core routes

export const health = () => request<Ok>('GET', '/health')
export const getMe = () => request<User>('GET', '/me')

export const startVerify = () => request<VerifySession>('POST', '/verify/start')
export const getVerifyStatus = () => request<VerifyStatus>('GET', '/verify/status')
/** Persona calls this, not the app. Here so every route has a function. */
export const postVerifyWebhook = (payload: unknown) => request<Ok>('POST', '/verify/webhook', payload)

export const getFeed = () => request<FeedResponse>('GET', '/feed')
export const addInto = (text: string) => request<FeedResponse>('POST', '/into', { text })
export const removeInto = (tag: string) => request<FeedResponse>('DELETE', `/into/${enc(tag)}`)

export const getEvent = (id: string) => request<Event>('GET', `/events/${enc(id)}`)

export const startRsvp = (eventId: string) => request<RsvpRun>('POST', '/rsvp', { event_id: eventId })
export const getRsvp = (runId: string) => request<RsvpRun>('GET', `/rsvp/${enc(runId)}`)

/** The latest PNG of the hosted Chrome, or null while there is none yet (404). */
export async function getRsvpFrame(runId: string): Promise<Blob | null> {
  if ((await apiMode()) === 'mock') {
    const { mockFrame } = await import('./mock')
    return mockFrame(runId)
  }
  const res = await fetch(`/api/rsvp/${enc(runId)}/frame`, { cache: 'no-store', credentials: 'same-origin' })
  if (res.status === 404) return null
  if (!res.ok) throw new ApiError(res.status)
  return res.blob()
}

export const getMemory = () => request<FactGroups>('GET', '/memory')
export const forgetFact = (factId: string) => request<FactGroups>('DELETE', `/memory/${enc(factId)}`)
export const forgetEverything = () => request<FactGroups>('DELETE', '/memory')

// ---------------------------------------------------------------- routes for the updated canvas

export const authStart = (email: string) =>
  request<{ sent: boolean; dev_code?: string | null }>('POST', '/auth/start', { email })
export const authVerify = (email: string, code: string) => request<User>('POST', '/auth/verify', { email, code })
export const authLogout = () => request<Ok>('POST', '/auth/logout')

export type MePatch = Partial<{
  spend_limit_usd: number
  ask_before: AskBefore
  usual_times: string[]
  name: string
}>
export const patchMe = (patch: MePatch) => request<User>('PATCH', '/me', patch)

/** "About you": the facts the RSVP agent uses to fill registration forms, stored in Cognee. */
export type ProfileKey = 'github' | 'linkedin' | 'website' | 'title' | 'company' | 'school' | 'location' | 'phone' | 'bio'
export type AboutYou = Record<ProfileKey, string | null>
export const getProfile = () => request<AboutYou>('GET', '/profile')
export const putProfile = (patch: Partial<Record<ProfileKey, string>>) => request<AboutYou>('PUT', '/profile', patch)
export const deleteMe = () => request<Ok>('DELETE', '/me')

export const getSites = () => request<SiteConnection[]>('GET', '/sites')
export const connectSite = (site: Site) => request<SiteConnection>('POST', `/sites/${enc(site)}/connect`)
export const disconnectSite = (site: Site) => request<SiteConnection>('DELETE', `/sites/${enc(site)}`)

export type CalendarStatus = { connected: boolean; source: string }
export const getCalendar = () => request<CalendarStatus>('GET', '/calendar')
export const connectCalendar = () => request<CalendarStatus>('POST', '/calendar/connect')

export const postTaste = (taste: TasteRequest) => request<FeedResponse>('POST', '/taste', taste)
export const getWeek = () => request<WeekResponse>('GET', '/week')
export const ask = (text: string) => request<AskResponse>('POST', '/ask', { text })
export const getEventDetail = (id: string) => request<EventDetail>('GET', `/events/${enc(id)}/detail`)

export const answerRsvp = (runId: string, value: string) =>
  request<RsvpRun>('POST', `/rsvp/${enc(runId)}/answer`, { value })
export const cancelRsvp = (runId: string) => request<RsvpRun>('POST', `/rsvp/${enc(runId)}/cancel`)

export const getActivity = () => request<ActivityResponse>('GET', '/activity')
export const getOps = () => request<OpsSummary>('GET', '/ops')
