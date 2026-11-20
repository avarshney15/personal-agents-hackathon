// Data for the Part 9 screens: the shared client (web/src/api.ts, one function per route in
// backend/API.md), plus the canvas samples in ./samples for mock mode only.
//
// Mock mode (api.apiMode() === 'mock': VITE_MOCK=1, ?mock=1, or the shell found no backend):
// the shared mock answers, and Week, Ask, Activity and Ops show the canvas samples, merged with
// anything done in the session.
// Live mode: never sample data. A failed call reaches the screen, which shows a short error in the
// canvas's voice with Try again (see ./fail); unreachable routes are also logged to the console.
// Ops is the one exception, and only because the server itself says `sample: true`.
import * as api from '../api'
import { unavailable } from './fail'
import * as sample from './samples'
import type { ActivityResponse, AskResponse, Site, TasteRequest, WeekDay, WeekResponse } from './types'

const isMock = async () => (await api.apiMode()) === 'mock'

async function logged<T>(label: string, call: () => Promise<T>): Promise<T> {
  try {
    return await call()
  } catch (err) {
    if (unavailable(err)) console.warn(`[features] ${label} failed`, err)
    throw err
  }
}

// ---------------------------------------------------------------- Welcome

export const authStart = (email: string) => logged('POST /api/auth/start', () => api.authStart(email))
export const authVerify = (email: string, code: string) =>
  logged('POST /api/auth/verify', () => api.authVerify(email, code))

// ---------------------------------------------------------------- Connect, Sites

export const getSites = () => logged('GET /api/sites', api.getSites)
export const connectSite = (site: Site) => logged(`POST /api/sites/${site}/connect`, () => api.connectSite(site))
export const disconnectSite = (site: Site) => logged(`DELETE /api/sites/${site}`, () => api.disconnectSite(site))
export const getCalendar = () => logged('GET /api/calendar', api.getCalendar)
export const connectCalendar = () => logged('POST /api/calendar/connect', api.connectCalendar)

// ---------------------------------------------------------------- Taste

export const getMe = () => api.getMe()

export async function saveTaste(req: TasteRequest) {
  const feed = await logged('POST /api/taste', () => api.postTaste(req))
  // The taste route takes usual_times too; PATCH /api/me keeps them on the profile either way.
  api.patchMe({ usual_times: req.usual_times }).catch(() => undefined)
  return feed
}

// ---------------------------------------------------------------- Week

// The shared mock seeds two finished runs (mock-run-*) that the canvas samples already show.
const seeded = (runId?: string | null) => !!runId && runId.startsWith('mock-run-')

/** Canvas week plus any items the shared mock has (runs finished this session). Mock mode only. */
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
  return logged('GET /api/week', api.getWeek)
}

// ---------------------------------------------------------------- Ask

export async function ask(text: string): Promise<AskResponse> {
  if (sample.isSampleQuery(text) && (await isMock())) return sample.ask(text)
  return logged('POST /api/ask', () => api.ask(text))
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
  return logged('GET /api/activity', api.getActivity)
}

// ---------------------------------------------------------------- Ops

/** Live mode shows whatever the server sends; its `sample` flag drives the "Sample data" label. */
export async function getOps() {
  if (await isMock()) return sample.ops()
  return logged('GET /api/ops', api.getOps)
}
