// Mirrors the Pydantic models in backend/app/contracts.py. Datetimes arrive as ISO strings.

export type FactKind = 'said' | 'went' | 'skipped' | 'hidden'
export type StepKey = 'checked' | 'opened' | 'told_host' | 'register' | 'read_back'
export type StepState = 'done' | 'current' | 'waiting' | 'failed'
export type RunStatus = 'queued' | 'running' | 'done' | 'needs_you' | 'failed'
export type Arm = 'tfidf' | 'sie'
export type PickTag = 'Strong match' | 'Match' | 'Something new'
export type Site = 'luma' | 'partiful' | 'meetup' | 'eventbrite'
export type WeekStatus = 'going' | 'waitlist' | 'invited' | 'pending'
export type NeedKind = 'code' | 'payment' | 'choice' | 'login' | 'human_check' | 'spend_limit'
export type Outcome = 'going' | 'waitlist'
export type AskBefore = 'paid' | 'always' | 'never'

export const DEMO_USER_ID = 'demo'

export const DISCLOSURE_TEMPLATE =
  'Registered by Event Agent for {name}, a verified person. Reply to reach them directly.'

export const STEP_ORDER: StepKey[] = ['checked', 'opened', 'told_host', 'register', 'read_back']

export const STEP_TEXT: Record<StepKey, [title: string, detail: string]> = {
  checked: ['Checked the event', 'Free, spots left, no host questions.'],
  opened: ['Opened Luma on your cloud desktop', 'Signed in as you, from when you connected.'],
  told_host: ['Told the host who’s registering', '“Registered by Event Agent for a verified person.”'],
  register: ['Register', 'One tap on Luma’s own button.'],
  read_back: ['Check it worked', 'Read your status back from the page.'],
}

// ---------------------------------------------------------------- shapes

export interface Event {
  id: string
  title: string
  starts_at: string
  ends_at?: string | null
  venue: string
  neighborhood: string
  url: string
  source: 'luma' | 'seed'
  host: string
  description: string
  free: boolean
  spots_left?: number | null
  has_questions: boolean
  tags: string[]
  site?: Site
  going_count?: number | null
  friends_going?: string[]
  price_text?: string
}

export interface User {
  id: string
  name: string
  verified: boolean
  persona_inquiry_id?: string | null
  into: string[]
  email?: string | null
  usual_times?: string[]
  spend_limit_usd?: number
  ask_before?: AskBefore
  verified_at?: string | null
}

export interface Fact {
  id: string
  kind: FactKind
  text: string
  from_text: string
  event_id?: string | null
  created_at: string
}

export interface FactGroups {
  said: Fact[]
  went: Fact[]
  skipped: Fact[]
}

export interface Intent {
  taste: string
  tags: string[]
}

export interface Pick {
  event: Event
  score: number
  why: string
  why_fact_id?: string | null
  tag: PickTag
  arm: Arm
}

export interface FeedResponse {
  picks: Pick[]
  into: string[]
  arm: Arm
}

export interface RsvpStep {
  run_id: string
  key: StepKey
  title: string
  detail: string
  state: StepState
}

export interface RunNeed {
  kind: NeedKind
  title: string
  detail: string
  step_key: StepKey
  expires_at?: string | null
  input_label?: string | null
}

export interface RsvpRun {
  id: string
  user_id: string
  event_id: string
  status: RunStatus
  stop_reason?: string | null
  started_at: string
  ended_at?: string | null
  steps: RsvpStep[]
  has_frame: boolean
  need?: RunNeed | null
  outcome?: Outcome | null
  cost_usd?: number
  model?: string | null
  site?: Site
  arm?: Arm | null
}

export interface RsvpResult {
  status: 'done' | 'needs_you' | 'failed'
  stop_reason?: string | null
  outcome?: Outcome | null
  cost_usd?: number
  model?: string | null
  need?: RunNeed | null
}

// Shapes for the newer canvas screens (not called by this app yet).

export interface SiteConnection {
  site: Site
  name: string
  connected: boolean
  event_count: number
}

export interface CalendarItem {
  id: string
  title: string
  starts_at: string
  ends_at: string
}

export interface Clash {
  event_id: string
  item: CalendarItem
  minutes_late: number
  text: string
}

export interface WeekItem {
  event: Event
  status: WeekStatus
  run_id?: string | null
}

export interface WeekDay {
  date: string
  label: string
  weekday: string
  items: WeekItem[]
}

export interface WeekResponse {
  days: WeekDay[]
  clashes: Clash[]
}

export interface TasteRequest {
  text: string
  chips: string[]
  usual_times: string[]
}

export interface AskResponse {
  query: string
  summary: string
  filters: string
  picks: Pick[]
}

export interface EventDetail {
  event: Event
  tag?: PickTag | null
  why: string
  remembered: Fact[]
  clash?: Clash | null
  week_status?: WeekStatus | null
}

export interface ActivityItem {
  id: string
  what: string
  where: string
  at: string
  result: string
  tone: 'ok' | 'wait' | 'needs'
  run_id?: string | null
}

export interface SpendStatus {
  spent_usd: number
  limit_usd: number
}

export interface ActivityResponse {
  items: ActivityItem[]
  spend: SpendStatus
}

export interface ArmStat {
  arm: Arm
  label: string
  rsvp_rate: number
  how: string
}

export interface Rehearsal {
  name: string
  value: string
  ok: boolean
}

export interface OpsRun {
  run: string
  site: string
  steps: number
  model: string
  cost_usd: number
  outcome: string
  ok: boolean
}

export interface OpsSummary {
  runs_today: number
  confirmed_pct: number
  handed_to_person: number
  cost_per_rsvp_usd: number
  arms: ArmStat[]
  rehearsals: Rehearsal[]
  recent: OpsRun[]
  sample: boolean
}

export interface VerifySession {
  inquiry_id: string
  session_token?: string | null
  url?: string | null
}

// Small response bodies from API.md.

export interface VerifyStatus {
  verified: boolean
}

export interface Ok {
  ok: boolean
}

// ---------------------------------------------------------------- helpers

export const isFinished = (status: RunStatus) =>
  status === 'done' || status === 'needs_you' || status === 'failed'

export const disclosureFor = (name: string) => DISCLOSURE_TEMPLATE.replace('{name}', name)
