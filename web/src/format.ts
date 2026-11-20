import type { Event, Site } from './types'

// Events are in San Francisco, so times are shown in Pacific time wherever the viewer is.
const TZ = 'America/Los_Angeles'

const fmt = (opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('en-US', { timeZone: TZ, ...opts })
const weekdayShort = fmt({ weekday: 'short' })
const weekdayLong = fmt({ weekday: 'long' })
const monthDay = fmt({ month: 'short', day: 'numeric' })
const clock = fmt({ hour: 'numeric', minute: '2-digit', hour12: true })

/** "6:30 pm" */
export function time(iso: string): string {
  return clock.format(new Date(iso)).replace(/\s?AM$/, ' am').replace(/\s?PM$/, ' pm')
}

/** "Tue 6:30 pm" */
export function whenShort(iso: string): string {
  return `${weekdayShort.format(new Date(iso))} ${time(iso)}`
}

/** "Tuesday 6:30 pm" */
export function weekdayTime(iso: string): string {
  return `${weekdayLong.format(new Date(iso))} ${time(iso)}`
}

/** "Tuesday, Nov 24 · 6:30 to 9:30 pm" */
export function whenLong(start: string, end?: string | null): string {
  const day = `${weekdayLong.format(new Date(start))}, ${monthDay.format(new Date(start))}`
  if (!end) return `${day} · ${time(start)}`
  const a = time(start)
  const b = time(end)
  const samePeriod = a.slice(-2) === b.slice(-2)
  return `${day} · ${samePeriod ? a.slice(0, -3) : a} to ${b}`
}

/** "Nov 20" */
export function shortDate(iso: string | Date): string {
  return monthDay.format(typeof iso === 'string' ? new Date(iso) : iso)
}

export function place(e: Event): string {
  return e.neighborhood || e.venue
}

export function placeLong(e: Event): string {
  const parts = [e.venue, e.neighborhood].filter(Boolean)
  return [...new Set(parts)].join(', ')
}

const SITE_NAMES: Record<Site, string> = {
  luma: 'Luma',
  partiful: 'Partiful',
  meetup: 'Meetup',
  eventbrite: 'Eventbrite',
}

export function siteName(e: Event): string {
  if (e.site) return SITE_NAMES[e.site]
  try {
    const host = new URL(e.url).hostname.replace(/^www\./, '')
    if (host === 'lu.ma' || host.endsWith('luma.com')) return 'Luma'
    return host
  } catch {
    return 'Luma'
  }
}

/** "Free · 80 going · hosted on Luma" */
export function priceLine(e: Event): string {
  const parts = [e.price_text || (e.free ? 'Free' : 'Paid')]
  if (e.going_count) parts.push(`${e.going_count} going`)
  if (e.spots_left != null) parts.push(e.spots_left === 0 ? 'Full' : `${e.spots_left} spots left`)
  parts.push(`hosted on ${siteName(e)}`)
  return parts.join(' · ')
}

export function initials(text: string): string {
  const words = text.replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(Boolean)
  return words
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join('')
}

function hash(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  // Final mix, so ids that differ only near the end still land on far-apart hues.
  h ^= h >>> 16
  h = Math.imul(h, 0x85ebca6b)
  h ^= h >>> 13
  h = Math.imul(h, 0xc2b2ae35)
  h ^= h >>> 16
  return h >>> 0
}

// Twelve hues 30 degrees apart, ordered so neighbours differ, starting with the canvas's four.
const HUES = [150, 30, 330, 100, 200, 260, 10, 60, 180, 290, 120, 230]
// Salt chosen so the canvas sample events (mock mode) land on different hues.
const SALT = '3:'

/** The two-colour tile gradient from the canvas, with the hue taken from the event id. */
export function tileGradient(id: string, colors?: [string, string]): string {
  if (colors) return `linear-gradient(135deg, ${colors[0]}, ${colors[1]})`
  const h = HUES[hash(SALT + id) % HUES.length]!
  return `linear-gradient(135deg, hsl(${h},72%,62%), hsl(${(h + 40) % 360},66%,44%))`
}

export function friendsLine(names: string[] = []): string | null {
  if (names.length === 0) return null
  if (names.length === 1) return `${names[0]} is going`
  if (names.length === 2) return `${names[0]} and ${names[1]} are going`
  return `${names[0]}, ${names[1]} and ${names.length - 2} others you know are going`
}
