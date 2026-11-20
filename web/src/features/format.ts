// Formatting for the Part 9 screens. Events are in San Francisco, so times show in Pacific time.
import type { Event, Site } from './types'

const TZ = 'America/Los_Angeles'
const fmt = (opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('en-US', { timeZone: TZ, ...opts })
const clock = fmt({ hour: 'numeric', minute: '2-digit', hour12: true })
const weekdayShort = fmt({ weekday: 'short' })
const weekdayLong = fmt({ weekday: 'long' })

/** "6:30 pm" */
export function time(iso: string): string {
  return clock.format(new Date(iso)).replace(/\s?AM$/, ' am').replace(/\s?PM$/, ' pm')
}

/** "Wed 6:00 pm" */
export function whenShort(iso: string): string {
  return `${weekdayShort.format(new Date(iso))} ${time(iso)}`
}

/** "Tuesday" */
export function weekday(iso: string): string {
  return weekdayLong.format(new Date(iso))
}

/** "$0.42" */
export function usd(n: number): string {
  return `$${n.toFixed(2)}`
}

/** "94%" from 0..100 */
export function pct(n: number): string {
  return `${Math.round(n)}%`
}

export const SITE_NAMES: Record<Site, string> = {
  luma: 'Luma',
  partiful: 'Partiful',
  meetup: 'Meetup',
  eventbrite: 'Eventbrite',
}

export function place(e: Event): string {
  return e.neighborhood || e.venue
}

/** "6:30 pm · SoMa · Luma" (Week rows) */
export function weekMeta(e: Event): string {
  return [time(e.starts_at), place(e), e.site ? SITE_NAMES[e.site] : ''].filter(Boolean).join(' · ')
}

/** "Wed 6:00 pm · Mission · 80 going" (Ask results) */
export function askMeta(e: Event): string {
  const going = e.going_count != null ? `${e.going_count} going` : ''
  return [whenShort(e.starts_at), place(e), going].filter(Boolean).join(' · ')
}
