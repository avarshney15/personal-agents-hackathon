// Canvas sample data the shared mock (web/src/mock.ts) lacks: the Week, Ask, Activity and Ops
// screens exactly as drawn in design/screens/*.dc.html (each screen's renderVals()).
// Dates are relative to today in Pacific time, so "Today" stays today.
// Agent builders night and Bouldering social reuse the shared mock's event ids, so the
// Event sheet opens for them in mock mode.
import type {
  ActivityResponse,
  AskResponse,
  CalendarStatus,
  Event,
  FeedResponse,
  OpsSummary,
  Pick,
  PickTag,
  Site,
  SiteConnection,
  TasteRequest,
  User,
  WeekResponse,
} from './types'

export const SAMPLE_EMAIL = 'maya@example.com'
export const SAMPLE_TASTE = 'Climbing and AI agents lately. Not big networking mixers.'
export const SAMPLE_CHIPS = ['AI agents', 'Climbing', 'Hackathons']
export const SAMPLE_TIMES = ['Weekday evenings']
export const SAMPLE_QUERY = 'Agent hackathons next week, evenings or weekend, not too big'

export const isSampleQuery = (text: string) =>
  text.trim().toLowerCase().replace(/[.?!]+$/, '') === SAMPLE_QUERY.toLowerCase()

// ---------------------------------------------------------------- dates (Pacific)

const TZ = 'America/Los_Angeles'
const OFFSET = '-08:00' // November is PST
const pad = (n: number) => String(n).padStart(2, '0')

function today(): { y: number; m: number; d: number; wd: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    weekday: 'short',
  }).formatToParts(new Date())
  const get = (t: string) => parts.find((p) => p.type === t)!.value
  return { y: +get('year'), m: +get('month'), d: +get('day'), wd: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(get('weekday')) }
}

/** ISO date `ahead` days from today (Pacific). */
function dayIso(ahead: number): string {
  const t = today()
  const d = new Date(Date.UTC(t.y, t.m - 1, t.d + ahead))
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

/** Days from today to the next given weekday (0 = Sunday), never today. */
function aheadTo(weekday: number): number {
  return ((weekday - today().wd + 7) % 7) || 7
}

const at = (ahead: number, hour: number, minute = 0) => `${dayIso(ahead)}T${pad(hour)}:${pad(minute)}:00${OFFSET}`

const monthDay = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
const weekdayLong = new Intl.DateTimeFormat('en-US', { weekday: 'long', timeZone: 'UTC' })

function dayLabels(ahead: number): { date: string; label: string; weekday: string } {
  const date = dayIso(ahead)
  const d = new Date(`${date}T12:00:00Z`)
  return { date, label: ahead === 0 ? 'Today' : monthDay.format(d), weekday: weekdayLong.format(d) }
}

// ---------------------------------------------------------------- events

function ev(id: string, title: string, startsAt: string, neighborhood: string, site: Site, extra: Partial<Event> = {}): Event {
  return {
    id,
    title,
    starts_at: startsAt,
    ends_at: null,
    venue: neighborhood,
    neighborhood,
    url: `https://lu.ma/${id}`,
    source: 'seed',
    host: '',
    description: '',
    free: true,
    spots_left: null,
    has_questions: false,
    tags: [],
    site,
    going_count: null,
    friends_going: [],
    price_text: 'Free',
    ...extra,
  }
}

const TUE = () => aheadTo(2)
const WED = () => aheadTo(3)
const THU = () => aheadTo(4)
const SAT = () => aheadTo(6)

// ---------------------------------------------------------------- Connect, Welcome, Taste (live-mode fallbacks)

export const user = (email = SAMPLE_EMAIL): User => ({
  id: 'demo',
  name: 'Maya R.',
  verified: false,
  persona_inquiry_id: null,
  into: [...SAMPLE_CHIPS],
  email,
  usual_times: [...SAMPLE_TIMES],
  spend_limit_usd: 2,
  ask_before: 'paid',
  verified_at: null,
})

let sitesState: SiteConnection[] = [
  { site: 'luma', name: 'Luma', connected: true, event_count: 14 },
  { site: 'partiful', name: 'Partiful', connected: true, event_count: 3 },
  { site: 'meetup', name: 'Meetup', connected: false, event_count: 0 },
  { site: 'eventbrite', name: 'Eventbrite', connected: false, event_count: 0 },
]
let calendarState: CalendarStatus = { connected: false, source: 'Google Calendar' }

export const sites = (): SiteConnection[] => sitesState.map((s) => ({ ...s }))

export function setSite(site: Site, connected: boolean): SiteConnection {
  sitesState = sitesState.map((s) =>
    s.site === site ? { ...s, connected, event_count: connected ? s.event_count || 6 : 0 } : s,
  )
  return { ...sitesState.find((s) => s.site === site)! }
}

export const calendar = (): CalendarStatus => ({ ...calendarState })

export function connectCalendar(): CalendarStatus {
  calendarState = { connected: true, source: 'Google Calendar' }
  return { ...calendarState }
}

export const taste = (req: TasteRequest): FeedResponse => ({ picks: [], into: req.chips, arm: 'sie' })

// ---------------------------------------------------------------- Week

export function week(): WeekResponse {
  const builders = ev('fake-agent-builders', 'Agent builders night', at(TUE(), 18, 30), 'SoMa', 'luma', {
    ends_at: at(TUE(), 21, 30),
    going_count: 80,
  })
  return {
    days: [
      {
        ...dayLabels(0),
        items: [
          {
            event: ev('canvas-personal-agents-hack', 'Personal Agents Hack', at(0, 9, 30), 'SoMa', 'luma'),
            status: 'going',
            run_id: null,
          },
        ],
      },
      {
        ...dayLabels(TUE()),
        items: [
          { event: builders, status: 'pending', run_id: null },
          {
            event: ev('canvas-design-systems-dinner', 'Design systems dinner', at(TUE(), 20), 'Hayes Valley', 'partiful'),
            status: 'invited',
            run_id: null,
          },
        ],
      },
      {
        ...dayLabels(THU()),
        items: [
          {
            event: ev('fake-bouldering', 'Bouldering social', at(THU(), 19), 'Dogpatch', 'meetup'),
            status: 'waitlist',
            run_id: 'mock-run-bouldering',
          },
        ],
      },
    ],
    clashes: [
      {
        event_id: builders.id,
        item: { id: 'cal-sam', title: 'Call with Sam', starts_at: at(TUE(), 18), ends_at: at(TUE(), 19) },
        minutes_late: 30,
        text: 'Agent builders night starts at 6:30, and you have “Call with Sam” from 6:00 to 7:00.',
      },
    ],
  }
}

// ---------------------------------------------------------------- Ask

function pick(event: Event, tag: PickTag, why: string, score: number): Pick {
  return { event, tag, why, score, why_fact_id: null, arm: 'sie' }
}

export function ask(text = SAMPLE_QUERY): AskResponse {
  return {
    query: text || SAMPLE_QUERY,
    summary: 'Three fit. I left out two that are over 300 people, since you said not too big.',
    filters: 'Next week · evenings and weekend · under 300',
    picks: [
      pick(
        ev('canvas-browser-agents', 'Browser agents build night', at(WED(), 18), 'Mission', 'luma', { going_count: 80 }),
        'Strong match',
        'Hands-on, small, and the host ran the last one you went to.',
        0.91,
      ),
      pick(
        ev('canvas-weekend-sprint', 'Weekend agent sprint', at(SAT(), 10), 'SoMa', 'luma', { going_count: 150 }),
        'Strong match',
        'Teams of 2 to 4, like the hackathons you’ve gone to.',
        0.86,
      ),
      pick(
        ev('canvas-voice-agents', 'Voice agents hack evening', at(THU(), 17, 30), 'FiDi', 'luma', { going_count: 120 }),
        'Match',
        'Clashes with Bouldering social, where you’re on the waitlist.',
        0.64,
      ),
    ],
  }
}

// ---------------------------------------------------------------- Activity

export const activity = (): ActivityResponse => ({
  spend: { spent_usd: 0.42, limit_usd: 2 },
  items: [
    { id: 'a1', what: 'RSVP · Agent builders night', where: 'Luma · 2:14 pm · 5 steps', at: at(0, 14, 14), result: 'Going', tone: 'ok', run_id: 'mock-run-builders' },
    { id: 'a2', what: 'Asked you for a code', where: 'Luma · 2:12 pm', at: at(0, 14, 12), result: 'Answered', tone: 'needs', run_id: 'mock-run-builders' },
    { id: 'a3', what: 'Joined waitlist · Bouldering social', where: 'Meetup · 11:40 am · 4 steps', at: at(0, 11, 40), result: 'Waitlist', tone: 'wait', run_id: 'mock-run-bouldering' },
    { id: 'a4', what: 'Read your events', where: 'Luma, Partiful · 9:00 am', at: at(0, 9), result: '17 events', tone: 'ok', run_id: null },
    { id: 'a5', what: 'Read host updates', where: 'Luma · 9:01 am', at: at(0, 9, 1), result: '2 new', tone: 'ok', run_id: null },
  ],
})

// ---------------------------------------------------------------- Ops

export const ops = (): OpsSummary => ({
  runs_today: 128,
  confirmed_pct: 94,
  handed_to_person: 6,
  cost_per_rsvp_usd: 0.07,
  arms: [
    { arm: 'sie', label: 'New: memory and reranking', rsvp_rate: 0.18, how: 'Recall from memory, then rerank the top 40' },
    { arm: 'tfidf', label: 'Old: word matching', rsvp_rate: 0.11, how: 'Taste profile and word overlap' },
  ],
  rehearsals: [
    { name: 'Simulated Luma', value: '48 of 50', ok: true },
    { name: 'Simulated Meetup', value: '45 of 50', ok: true },
    { name: 'Full event, no waitlist', value: 'Stopped, as it should', ok: true },
    { name: 'Paid event', value: 'Asked first, as it should', ok: true },
  ],
  recent: [
    { run: 'RSVP · Agent builders night', site: 'Luma', steps: 5, model: 'Claude Haiku', cost_usd: 0.06, outcome: 'Going', ok: true },
    { run: 'RSVP · Design systems dinner', site: 'Partiful', steps: 2, model: 'Claude Haiku', cost_usd: 0.03, outcome: 'Needs code', ok: false },
    { run: 'Waitlist · Bouldering social', site: 'Meetup', steps: 4, model: 'Claude Haiku', cost_usd: 0.05, outcome: 'Waitlist', ok: true },
    { run: 'Tag 30 new events', site: 'Catalog', steps: 1, model: 'Arcee', cost_usd: 0.01, outcome: 'Done', ok: true },
  ],
  sample: true,
})
