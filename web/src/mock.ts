// Mock backend, built from the sample data in design/screens/*.dc.html.
// Used when VITE_MOCK=1, ?mock=1, or the API is unreachable (see api.ts).
import { ApiError } from './errors'
import { shortDate } from './format'
import {
  STEP_ORDER,
  STEP_TEXT,
  disclosureFor,
  type ActivityResponse,
  type AskResponse,
  type Event,
  type EventDetail,
  type Fact,
  type FactGroups,
  type FactKind,
  type FeedResponse,
  type OpsSummary,
  type Pick,
  type RsvpRun,
  type RunNeed,
  type SiteConnection,
  type StepKey,
  type StepState,
  type User,
  type WeekResponse,
} from './types'

// ---------------------------------------------------------------- time helpers

const PT_OFFSET = '-08:00' // November is PST

function ptToday(): { y: number; m: number; d: number; weekday: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Los_Angeles',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    weekday: 'short',
  }).formatToParts(new Date())
  const get = (t: string) => parts.find((p) => p.type === t)!.value
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  return { y: +get('year'), m: +get('month'), d: +get('day'), weekday: days.indexOf(get('weekday')) }
}

/** The next given weekday (0 = Sunday) after today, at a Pacific time, as ISO. */
function next(weekday: number, hour: number, minute = 0): string {
  const t = ptToday()
  const ahead = ((weekday - t.weekday + 7) % 7) || 7
  const date = new Date(Date.UTC(t.y, t.m - 1, t.d + ahead))
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T${pad(hour)}:${pad(minute)}:00${PT_OFFSET}`
}

const nowIso = () => new Date().toISOString()
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

// ---------------------------------------------------------------- data

const EVENTS: Event[] = [
  {
    id: 'fake-agent-builders',
    title: 'Agent builders night',
    starts_at: next(2, 18, 30),
    ends_at: next(2, 21, 30),
    venue: 'SoMa',
    neighborhood: 'SoMa',
    url: 'https://lu.ma/agent-builders-night',
    source: 'seed',
    host: 'Agent Builders SF',
    description: 'Hands-on night for people building AI agents.',
    free: true,
    spots_left: 20,
    has_questions: false,
    tags: ['AI agents', 'hackathon'],
    site: 'luma',
    going_count: 80,
    friends_going: ['Dev', 'Priya', 'Sam', 'Ana', 'Leo', 'Kim'],
    price_text: 'Free',
  },
  {
    id: 'fake-bouldering',
    title: 'Bouldering social',
    starts_at: next(4, 19),
    ends_at: next(4, 21),
    venue: 'Dogpatch Boulders',
    neighborhood: 'Dogpatch',
    url: 'https://lu.ma/bouldering-social',
    source: 'seed',
    host: 'SF Climbers',
    description: 'Climbing meetup and social.',
    free: true,
    spots_left: 12,
    has_questions: false,
    tags: ['climbing', 'social'],
    site: 'luma',
    going_count: 34,
    friends_going: [],
    price_text: 'Free',
  },
  {
    id: 'fake-evals-workshop',
    title: 'Evals for agents, a workshop',
    starts_at: next(6, 10),
    ends_at: next(6, 13),
    venue: 'Mission',
    neighborhood: 'Mission',
    url: 'https://lu.ma/evals-for-agents',
    source: 'seed',
    host: 'Agent Builders SF',
    description: 'Hands-on workshop on evaluating AI agents.',
    free: true,
    spots_left: 8,
    has_questions: false,
    tags: ['AI agents', 'workshop'],
    site: 'luma',
    going_count: 41,
    friends_going: ['Priya'],
    price_text: 'Free',
  },
  {
    id: 'fake-ceramics',
    title: 'Ceramics for beginners',
    starts_at: next(0, 14),
    ends_at: next(0, 16),
    venue: 'Inner Sunset',
    neighborhood: 'Inner Sunset',
    url: 'https://lu.ma/ceramics-beginners',
    source: 'seed',
    host: 'Clay Club',
    description: 'A weekend afternoon at the wheel.',
    free: true,
    spots_left: 6,
    has_questions: false,
    tags: ['art', 'weekend'],
    site: 'luma',
    going_count: 18,
    friends_going: [],
    price_text: 'Free',
  },
]

interface Rank {
  base: number
  keywords: string[]
  boost: number
  matched: { why: string; tag: Pick['tag'] }
  plain: { why: string; tag: Pick['tag'] }
  longWhy: string
}

const RANK: Record<string, Rank> = {
  'fake-agent-builders': {
    base: 0.6,
    keywords: ['ai', 'agent', 'agents', 'hackathon', 'builders', 'llm'],
    boost: 0.3,
    matched: { tag: 'Strong match', why: 'You went to 3 agent hackathons this autumn, and liked the hands-on ones.' },
    plain: { tag: 'Strong match', why: 'You went to 3 agent hackathons this autumn, and liked the hands-on ones.' },
    longWhy:
      'You went to 3 agent hackathons this autumn and skipped the talk-only ones. This one is hands-on, and you said AI agents lately.',
  },
  'fake-bouldering': {
    base: 0.35,
    keywords: ['climbing', 'climb', 'bouldering', 'boulder'],
    boost: 0.45,
    matched: { tag: 'Strong match', why: 'You said climbing lately. Same gym as the one you went to in October.' },
    plain: { tag: 'Something new', why: 'Something new: a Thursday evening social in Dogpatch.' },
    longWhy: 'You said climbing lately, and it’s at the same gym as the one you went to in October.',
  },
  'fake-evals-workshop': {
    base: 0.62,
    keywords: ['ai', 'agent', 'agents', 'evals', 'workshop'],
    boost: 0.15,
    matched: { tag: 'Strong match', why: 'Hosted by someone whose last two events you went to.' },
    plain: { tag: 'Strong match', why: 'Hosted by someone whose last two events you went to.' },
    longWhy: 'Hosted by Agent Builders SF, whose last two events you went to. It’s a hands-on morning, not a talk.',
  },
  'fake-ceramics': {
    base: 0.4,
    keywords: ['ceramics', 'pottery', 'clay', 'art'],
    boost: 0.4,
    matched: { tag: 'Strong match', why: 'You said ceramics lately. A small class on a weekend afternoon.' },
    plain: { tag: 'Something new', why: 'Something new: a weekend afternoon, which you keep free.' },
    longWhy: 'Something new for you, on a weekend afternoon, which you keep free.',
  },
}

let factSeq = 0
const fact = (kind: FactKind, text: string, from_text: string, event_id?: string): Fact => ({
  id: `fact-${++factSeq}`,
  kind,
  text,
  from_text,
  event_id: event_id ?? null,
  created_at: nowIso(),
})

const freshFacts = (): FactGroups => ({
  said: [fact('said', 'Weekend afternoons stay free', 'You told the agent, Oct 2')],
  went: [
    fact('went', 'Likes hands-on hackathons over talks', '3 hackathons went, 2 talks skipped'),
    fact('went', 'Follows hosts from Agent Builders SF', 'Went to their last two events'),
  ],
  skipped: [fact('skipped', 'Not into sales and pitch nights', 'Hid 4 of them')],
})

const state = {
  user: {
    id: 'demo',
    name: 'Maya R.',
    verified: false,
    persona_inquiry_id: null,
    into: [] as string[],
    email: 'maya@example.com',
    usual_times: ['Weekday evenings'],
    spend_limit_usd: 2,
    ask_before: 'paid',
    verified_at: null,
  } as User,
  verifyStartedAt: 0,
  facts: freshFacts(),
  runs: new Map<string, MockRun>(),
  runSeq: 0,
  sites: [
    { site: 'luma', name: 'Luma', connected: true, event_count: 14 },
    { site: 'partiful', name: 'Partiful', connected: false, event_count: 0 },
    { site: 'meetup', name: 'Meetup', connected: false, event_count: 0 },
    { site: 'eventbrite', name: 'Eventbrite', connected: false, event_count: 0 },
  ] as SiteConnection[],
  calendar: { connected: false, source: 'Google Calendar' },
}

// ---------------------------------------------------------------- feed

function chipsFrom(text: string): string[] {
  const cleaned = text
    .replace(/[.!]/g, ' ')
    .replace(/\b(i'?m|i am|really|into|lately|right now|these days|recently|mostly)\b/gi, ' ')
  return cleaned
    .split(/,|;|\band\b|&|\+|\//i)
    .map((s) => s.replace(/\s+/g, ' ').trim())
    .filter((s) => s.length > 1 && !/^not\b/i.test(s))
    .map((s) => (/^ai\b/i.test(s) ? `AI${s.slice(2)}` : s[0]!.toUpperCase() + s.slice(1)))
}

function matches(eventId: string, chips: string[]): boolean {
  const words = chips.join(' ').toLowerCase().split(/\s+/)
  return RANK[eventId]!.keywords.some((k) => words.includes(k))
}

function feed(): FeedResponse {
  const chips = state.user.into
  const picks: Pick[] = EVENTS.map((event) => {
    const r = RANK[event.id]!
    const hit = chips.length > 0 && matches(event.id, chips)
    const v = hit ? r.matched : r.plain
    return { event, score: r.base + (hit ? r.boost : 0), why: v.why, why_fact_id: null, tag: v.tag, arm: 'sie' as const }
  })
  picks.sort((a, b) => b.score - a.score)
  return { picks, into: [...chips], arm: 'sie' }
}

// ---------------------------------------------------------------- RSVP runs

const STEP_MS = 1500
const QUEUE_MS = 700

interface MockRun {
  run: RsvpRun
  idx: number
  phaseAt: number
  needAt: StepKey | null
  answered: boolean
}

function setStep(m: MockRun, i: number, s: StepState) {
  m.run.steps[i] = { ...m.run.steps[i]!, state: s }
}

const CODE_NEED = (): RunNeed => ({
  kind: 'code',
  title: 'Enter the code Luma emailed you',
  detail: 'Your Luma login expired, so Luma checked it’s you. The agent types the code in; it never sees your inbox.',
  step_key: 'opened',
  expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
  input_label: 'Six-digit code',
})

function tick(m: MockRun) {
  const now = Date.now()
  const r = m.run
  if (r.status === 'queued') {
    if (now - m.phaseAt < QUEUE_MS) return
    r.status = 'running'
    m.idx = 0
    m.phaseAt += QUEUE_MS
    setStep(m, 0, 'current')
  }
  while (r.status === 'running' && now - m.phaseAt >= STEP_MS) {
    const key = STEP_ORDER[m.idx]!
    if (key === 'opened') r.has_frame = true
    if (m.needAt === key && !m.answered) {
      r.status = 'needs_you'
      r.need = CODE_NEED()
      r.stop_reason = 'Luma asked for a sign-in code.'
      return
    }
    setStep(m, m.idx, 'done')
    m.idx += 1
    m.phaseAt += STEP_MS
    if (m.idx >= STEP_ORDER.length) {
      r.status = 'done'
      r.outcome = 'going'
      r.ended_at = nowIso()
      r.cost_usd = 0.18
      const event = EVENTS.find((e) => e.id === r.event_id)
      if (event) {
        state.facts.went.unshift(
          fact('went', `Going to ${event.title}`, `The agent RSVPed for you, ${shortDate(new Date())}`, event.id),
        )
      }
      return
    }
    setStep(m, m.idx, 'current')
  }
}

function newRun(eventId: string): RsvpRun {
  const id = `run-${++state.runSeq}`
  const steps = STEP_ORDER.map((key) => {
    const [title, detail] = STEP_TEXT[key]
    return {
      run_id: id,
      key,
      title,
      detail: key === 'told_host' ? `“${disclosureFor(state.user.name)}”` : detail,
      state: 'waiting' as StepState,
    }
  })
  const run: RsvpRun = {
    id,
    user_id: state.user.id,
    event_id: eventId,
    status: 'queued',
    stop_reason: null,
    started_at: nowIso(),
    ended_at: null,
    steps,
    has_frame: false,
    need: null,
    outcome: null,
    cost_usd: 0,
    model: 'claude-opus-5-5',
    site: 'luma',
    arm: 'sie',
  }
  // In mock mode, Ceramics shows the "Needs you" path (Luma asks for a code).
  state.runs.set(id, { run, idx: -1, phaseAt: Date.now(), needAt: eventId === 'fake-ceramics' ? 'opened' : null, answered: false })
  return run
}

function getRun(id: string): MockRun {
  const m = state.runs.get(id)
  if (!m) throw new ApiError(404, 'Run not found')
  tick(m)
  return m
}

const clone = <T,>(v: T): T => structuredClone(v)

// ---------------------------------------------------------------- router

export async function mockRequest(method: string, path: string, body?: unknown): Promise<unknown> {
  await wait(path === '/into' || path === '/taste' || path === '/ask' ? 450 : 120)
  const b = (body ?? {}) as Record<string, unknown>
  const seg = path.split('/').filter(Boolean).map(decodeURIComponent)
  const route = `${method} /${seg[0] ?? ''}`

  switch (route) {
    case 'GET /health':
      return { ok: true }

    case 'GET /me':
      if (state.user.id === '') throw new ApiError(401, 'Signed out')
      return clone(state.user)
    case 'PATCH /me':
      Object.assign(state.user, b)
      return clone(state.user)
    case 'DELETE /me':
      state.facts = { said: [], went: [], skipped: [] }
      state.user = { ...state.user, verified: false, verified_at: null, into: [] }
      return { ok: true }

    case 'POST /verify':
      if (seg[1] === 'start') {
        state.verifyStartedAt = Date.now()
        return { inquiry_id: 'inq_mock', session_token: null, url: null }
      }
      return { ok: true }
    case 'GET /verify':
      if (!state.user.verified && state.verifyStartedAt && Date.now() - state.verifyStartedAt > 2500) {
        state.user.verified = true
        state.user.verified_at = nowIso()
      }
      return { verified: state.user.verified }

    case 'GET /feed':
      return feed()
    case 'POST /into': {
      const text = String(b.text ?? '').trim()
      if (!text) throw new ApiError(422, 'Say what you’re into.')
      for (const chip of chipsFrom(text)) {
        if (!state.user.into.some((c) => c.toLowerCase() === chip.toLowerCase())) state.user.into.push(chip)
      }
      const said = /^into\b/i.test(text) ? text : `Into ${text}`
      state.facts.said.unshift(fact('said', said.replace(/[.\s]+$/, ''), `You told the agent, ${shortDate(new Date())}`))
      return feed()
    }
    case 'DELETE /into':
      state.user.into = state.user.into.filter((c) => c !== seg[1])
      return feed()
    case 'POST /taste': {
      const chips = [...((b.chips as string[]) ?? []), ...chipsFrom(String(b.text ?? ''))]
      for (const chip of chips) if (!state.user.into.includes(chip)) state.user.into.push(chip)
      if (b.usual_times) state.user.usual_times = b.usual_times as string[]
      return feed()
    }

    case 'GET /events': {
      const event = EVENTS.find((e) => e.id === seg[1])
      if (!event) throw new ApiError(404, 'Event not found')
      if (seg[2] === 'detail') return detail(event)
      return clone(event)
    }

    case 'POST /rsvp': {
      if (seg[1]) {
        const m = getRun(seg[1])
        if (seg[2] === 'answer') {
          m.answered = true
          m.run.status = 'running'
          m.run.need = null
          m.run.stop_reason = null
          m.phaseAt = Date.now()
        } else if (seg[2] === 'cancel') {
          m.run.status = 'failed'
          m.run.stop_reason = 'You stopped this RSVP.'
          m.run.need = null
          m.run.ended_at = nowIso()
          if (m.idx >= 0 && m.idx < STEP_ORDER.length) setStep(m, m.idx, 'failed')
        }
        return clone(m.run)
      }
      if (!state.user.verified) throw new ApiError(403, 'Verify once before the agent RSVPs for you.')
      const eventId = String(b.event_id ?? '')
      if (!EVENTS.some((e) => e.id === eventId)) throw new ApiError(404, 'Event not found')
      return clone(newRun(eventId))
    }
    case 'GET /rsvp':
      return clone(getRun(seg[1] ?? '').run)

    case 'GET /memory':
      return clone(state.facts)
    case 'DELETE /memory': {
      if (seg[1]) {
        for (const k of ['said', 'went', 'skipped'] as const) state.facts[k] = state.facts[k].filter((f) => f.id !== seg[1])
      } else {
        state.facts = { said: [], went: [], skipped: [] }
      }
      return clone(state.facts)
    }

    case 'POST /auth':
      if (seg[1] === 'start') return { sent: true, dev_code: '123456' }
      if (seg[1] === 'verify') return clone(state.user)
      return { ok: true }

    case 'GET /sites':
      return clone(state.sites)
    case 'POST /sites':
    case 'DELETE /sites': {
      const s = state.sites.find((x) => x.site === seg[1])
      if (!s) throw new ApiError(404, 'Unknown site')
      s.connected = method === 'POST'
      s.event_count = s.connected ? 6 : 0
      return clone(s)
    }
    case 'GET /calendar':
      return clone(state.calendar)
    case 'POST /calendar':
      state.calendar.connected = true
      return clone(state.calendar)

    case 'GET /week':
      return week()
    case 'POST /ask':
      return askResult(String(b.text ?? ''))
    case 'GET /activity':
      return activity()
    case 'GET /ops':
      return ops()
  }
  throw new ApiError(404, `Not in mock mode: ${method} ${path}`)
}

// ---------------------------------------------------------------- frame

/** A drawn stand-in for the hosted Chrome screenshot. */
export async function mockFrame(runId: string): Promise<Blob | null> {
  const m = state.runs.get(runId)
  if (!m) return null
  tick(m)
  if (!m.run.has_frame && m.idx < 1) return null
  const event = EVENTS.find((e) => e.id === m.run.event_id)
  const step = m.run.steps.find((s) => s.state === 'current') ?? m.run.steps[m.run.steps.length - 1]!
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const going = m.run.status === 'done'
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400">
<rect width="640" height="400" fill="#ffffff"/>
<rect width="640" height="36" fill="#ecebe6"/>
<circle cx="18" cy="18" r="6" fill="#e5281b"/><circle cx="38" cy="18" r="6" fill="#f2b765"/><circle cx="58" cy="18" r="6" fill="#6fd49a"/>
<rect x="84" y="9" width="470" height="18" rx="9" fill="#ffffff"/>
<text x="96" y="22" font-family="Helvetica, Arial" font-size="12" fill="#6b6e76">${esc(event?.url ?? 'lu.ma')}</text>
<text x="32" y="96" font-family="Helvetica, Arial" font-size="28" font-weight="700" fill="#111214">${esc(event?.title ?? '')}</text>
<text x="32" y="126" font-family="Helvetica, Arial" font-size="15" fill="#6b6e76">Hosted by ${esc(event?.host ?? '')}</text>
<rect x="32" y="160" width="576" height="88" rx="12" fill="#f5f4f0" stroke="#e6e4de"/>
<text x="48" y="190" font-family="Helvetica, Arial" font-size="13" fill="#6b6e76">Note to host</text>
<text x="48" y="216" font-family="Helvetica, Arial" font-size="14" fill="#111214">${esc(disclosureFor(state.user.name).slice(0, 72))}</text>
<rect x="32" y="276" width="200" height="48" rx="24" fill="${going ? '#1f7a46' : '#111214'}"/>
<text x="132" y="306" text-anchor="middle" font-family="Helvetica, Arial" font-size="16" font-weight="700" fill="#ffffff">${going ? 'You’re going' : 'Register'}</text>
<text x="32" y="372" font-family="Helvetica, Arial" font-size="13" fill="#6b6e76">Agent: ${esc(step.title)}</text>
</svg>`
  return new Blob([svg], { type: 'image/svg+xml' })
}

// ---------------------------------------------------------------- canvas samples for the other screens

function detail(event: Event): EventDetail {
  const r = RANK[event.id]!
  const hit = state.user.into.length > 0 && matches(event.id, state.user.into)
  const remembered = [...state.facts.said, ...state.facts.went].slice(0, 3)
  return {
    event: clone(event),
    tag: (hit ? r.matched : r.plain).tag,
    why: r.longWhy,
    remembered,
    clash:
      event.id === 'fake-agent-builders'
        ? {
            event_id: event.id,
            item: { id: 'cal-1', title: 'Call with Sam', starts_at: next(2, 18), ends_at: next(2, 19) },
            minutes_late: 30,
            text: '6:00 to 7:00 in your calendar. You’d arrive about 30 minutes late.',
          }
        : null,
    week_status: null,
  }
}

function week(): WeekResponse {
  const items = [...state.runs.values()]
    .filter((m) => m.run.status === 'done')
    .map((m) => ({ event: EVENTS.find((e) => e.id === m.run.event_id)!, status: 'going' as const, run_id: m.run.id }))
  return {
    days: items.map((it) => ({
      date: it.event.starts_at.slice(0, 10),
      label: shortDate(it.event.starts_at),
      weekday: new Intl.DateTimeFormat('en-US', { weekday: 'long', timeZone: 'America/Los_Angeles' }).format(
        new Date(it.event.starts_at),
      ),
      items: [it],
    })),
    clashes: [],
  }
}

function askResult(text: string): AskResponse {
  const words = text.toLowerCase()
  const picks = feed().picks.filter((p) => RANK[p.event.id]!.keywords.some((k) => words.includes(k)))
  return {
    query: text,
    summary: picks.length ? `${picks.length} fit.` : 'Nothing fits that yet.',
    filters: 'Next week',
    picks: picks.length ? picks : [],
  }
}

function activity(): ActivityResponse {
  const items = [...state.runs.values()].map((m) => {
    const event = EVENTS.find((e) => e.id === m.run.event_id)
    const tone: 'ok' | 'wait' | 'needs' = m.run.status === 'done' ? 'ok' : m.run.status === 'needs_you' ? 'needs' : 'wait'
    return {
      id: m.run.id,
      what: `RSVP · ${event?.title ?? ''}`,
      where: `Luma · ${STEP_ORDER.length} steps`,
      at: m.run.started_at,
      result: m.run.status === 'done' ? 'Going' : m.run.status === 'needs_you' ? 'Needs you' : 'Working',
      tone,
      run_id: m.run.id,
    }
  })
  const spent = [...state.runs.values()].reduce((s, m) => s + (m.run.cost_usd ?? 0), 0)
  return { items, spend: { spent_usd: spent, limit_usd: state.user.spend_limit_usd ?? 2 } }
}

function ops(): OpsSummary {
  return {
    runs_today: state.runs.size,
    confirmed_pct: 92,
    handed_to_person: 1,
    cost_per_rsvp_usd: 0.18,
    arms: [
      { arm: 'tfidf', label: 'Old: keyword match', rsvp_rate: 0.11, how: 'TF-IDF over titles and tags' },
      { arm: 'sie', label: 'New: memory and reranking', rsvp_rate: 0.19, how: 'Cognee memory plus Superlinked SIE' },
    ],
    rehearsals: [{ name: 'Simulated Luma', value: '48 of 50', ok: true }],
    recent: [],
    sample: true,
  }
}
