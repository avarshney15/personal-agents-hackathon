// The contract shapes the Part 9 screens use. They live in the shared web/src/types.ts
// (which mirrors backend/app/contracts.py); this file only re-exports them so the
// screens have one place to import from.
export type {
  ActivityItem,
  ActivityResponse,
  Arm,
  ArmStat,
  AskResponse,
  CalendarItem,
  Clash,
  Event,
  FeedResponse,
  OpsRun,
  OpsSummary,
  Pick,
  PickTag,
  Rehearsal,
  Site,
  SiteConnection,
  SpendStatus,
  TasteRequest,
  User,
  WeekDay,
  WeekItem,
  WeekResponse,
  WeekStatus,
} from '../types'
export type { CalendarStatus } from '../api'

export type Tone = 'ok' | 'wait' | 'needs'
export type AuthStart = { sent: boolean; dev_code?: string | null }
