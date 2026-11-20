// Data for the Part 9 screens: the shared client (web/src/api.ts, one function per route in
// backend/API.md) plus the canvas samples in ./samples.
//
// Mock mode (VITE_MOCK=1, ?mock=1, or no backend): the shared mock answers, and Week, Ask,
// Activity and Ops show the canvas samples, merged with anything done in the session.
// Live mode: when a route isn't there yet or the server errors (0, 404, 405, 5xx), the screen
// falls back to the canvas sample instead of breaking. Other errors (a wrong code) reach the screen.
import * as api from '../api'
import { ApiError } from '../errors'
import * as sample from './samples'
import type { ActivityResponse, AskResponse, Site, TasteRequest, WeekDay, WeekResponse } from './types'

const isMock = async () => (await api.apiMode()) === 'mock'

function missing(err: unknown): boolean {
  const status = err instanceof ApiError ? err.status : 0
  return status === 0 || status === 404 || status === 405 || status >= 500
}

async function orSample<T>(label: string, call: () => Promise<T>, fallback: () => T): Promise<T> {
  try {
    return await call()
  } catch (err) {
    if (!missing(err)) throw err
    console.warn(`[features] ${label} unavailable; showing canvas sample data`, err)
    return fallback()
  }
}

// ---------------------------------------------------------------- Welcome

export const authStart = (email: string) =>
  orSample('POST /api/auth/start', () => api.authStart(email), () => ({ sent: true, dev_code: null }))

export const authVerify = (email: string, code: string) =>
  orSample('POST /api/auth/verify', () => api.authVerify(email, code), () => sample.user(email))

// ---------------------------------------------------------------- Connect, Sites

export const getSites = () => orSample('GET /api/sites', api.getSites, sample.sites)
export const connectSite = (site: Site) =>
  orSample(`POST /api/sites/${site}/connect`, () => api.connectSite(site), () => sample.setSite(site, true))
export const disconnectSite = (site: Site) =>
  orSample(`DELETE /api/sites/${site}`, () => api.disconnectSite(site), () => sample.setSite(site, false))
export const getCalendar = () => orSample('GET /api/calendar', api.getCalendar, sample.calendar)
export const connectCalendar = () => orSample('POST /api/calendar/connect', api.connectCalendar, sample.connectCalendar)

// ---------------------------------------------------------------- Taste

export const getMe = () => api.getMe()

export async function saveTaste(req: TasteRequest) {
  const feed = await orSample('POST /api/taste', () => api.postTaste(req), () => sample.taste(req))
  // The taste route takes usual_times too; PATCH /api/me keeps them on the profile either way.
  api.patchMe({ usual_times: req.usual_times }).catch(() => undefined)
  return feed
}

// ---------------------------------------------------------------- Week

// The shared mock seeds two finished runs (mock-run-*) that the canvas samples already show.
const seeded = (runId?: string | null) => !!runId && runId.startsWith('mock-run-')

/** Canvas week plus any items the shared mock has (runs finished this session). */
function mergeWeek(base: WeekResponse, extra: WeekResponse): WeekResponse {
  const days: WeekDay[] = base.days.map((d) => ({ ...d, items: [...d.items] }))
  for (const day of extra.days) {
    for (const item of day.items) {
      if (seeded(item.run_id)) continue
      const existing = days.flatMap((d) => d.items).find((i) => i.event.id === item.event.id)
      if (existing) {
        existing.status = item.status
        existing.run_id = item.run_id
        continue
      }
      const target = days.find((d) => d.date === day.date)
      if (target) target.items.push(item)
      else days.push({ ...day, items: [item] })
    }
  }
  days.sort((a, b) => a.date.localeCompare(b.date))
  for (const d of days) d.items.sort((a, b) => a.event.starts_at.localeCompare(b.event.starts_at))
  return { days, clashes: [...base.clashes, ...extra.clashes] }
}

export async function getWeek(): Promise<WeekResponse> {
  if (await isMock()) {
    const shared = await api.getWeek().catch(() => ({ days: [], clashes: [] }) as WeekResponse)
    return mergeWeek(sample.week(), shared)
  }
  return orSample('GET /api/week', api.getWeek, sample.week)
}

// ---------------------------------------------------------------- Ask

export async function ask(text: string): Promise<AskResponse> {
  if (sample.isSampleQuery(text) && (await isMock())) return sample.ask(text)
  return orSample('POST /api/ask', () => api.ask(text), () => sample.ask(text))
}

// ---------------------------------------------------------------- Activity

export async function getActivity(): Promise<ActivityResponse> {
  if (await isMock()) {
    const base = sample.activity()
    const shared = await api.getActivity().catch(() => null)
    if (!shared) return base
    // Runs started this session go on top; the spend stays the canvas's.
    const newest = shared.items.filter((i) => !seeded(i.run_id)).sort((a, b) => b.at.localeCompare(a.at))
    return { items: [...newest, ...base.items], spend: base.spend }
  }
  return orSample('GET /api/activity', api.getActivity, sample.activity)
}

// ---------------------------------------------------------------- Ops

export async function getOps() {
  if (await isMock()) return sample.ops()
  return orSample('GET /api/ops', api.getOps, sample.ops)
}
