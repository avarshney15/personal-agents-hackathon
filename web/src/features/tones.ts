// Status and tag colours as drawn on the canvas.
import type { Tone as PillTone } from '../ui'
import type { PickTag, Tone, WeekStatus } from './types'

export const WEEK_STATUS: Record<WeekStatus, { label: string; tone: PillTone }> = {
  going: { label: 'Going', tone: 'going' },
  waitlist: { label: 'Waitlist', tone: 'wait' },
  invited: { label: 'Invited', tone: 'invited' },
  pending: { label: 'Pending', tone: 'pending' },
}

/** Ask.dc.html: Strong match on going-bg, Match on surface-2. Something new borrows invited. */
export const TAG_TONE: Record<PickTag, PillTone> = {
  'Strong match': 'going',
  Match: 'quiet',
  'Something new': 'invited',
}

export const RESULT_TONE: Record<Tone, PillTone> = { ok: 'going', wait: 'wait', needs: 'needs' }
