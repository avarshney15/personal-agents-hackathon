// Pieces only the Part 9 screens need. Shared ones (Icon, Logo, Tile, Pill, RoundButton,
// BackButton) come from web/src/ui.tsx.
import type { Tone as PillTone } from '../ui'
import type { Site } from './types'

/** A small status pill with the canvas's colours. */
export function StatusPill({ tone, children }: { tone: PillTone; children: string }) {
  return <span className={`ft-pill ft-pill--${tone}`}>{children}</span>
}

// ---------------------------------------------------------------- site icon (Connect)

// Connect.dc.html: 40px, radius 10, two letters on hsl(h,72%,62%) to hsl(h+40,66%,44%).
const SITE_ART: Record<Site, { ini: string; hue: number }> = {
  luma: { ini: 'Lu', hue: 20 },
  partiful: { ini: 'Pa', hue: 320 },
  meetup: { ini: 'Me', hue: 350 },
  eventbrite: { ini: 'Eb', hue: 40 },
}

export function SiteIcon({ site }: { site: Site }) {
  const art = SITE_ART[site] ?? { ini: site.slice(0, 2), hue: 200 }
  return (
    <span
      className="ft-site-icon"
      aria-hidden="true"
      style={{ background: `linear-gradient(135deg, hsl(${art.hue},72%,62%), hsl(${(art.hue + 40) % 360},66%,44%))` }}
    >
      {art.ini}
    </span>
  )
}

// ---------------------------------------------------------------- icons the shared set lacks

export const CalendarIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <rect x="3" y="5" width="18" height="16" rx="3" />
    <path d="M3 10h18M8 3v4M16 3v4" />
  </svg>
)

export const SendIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d="M12 19V5M5 12l7-7 7 7" />
  </svg>
)

// ---------------------------------------------------------------- loading

export function Loading({ label }: { label: string }) {
  return (
    <p className="ft-loading" role="status">
      {label}
    </p>
  )
}

export function ErrorLine({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="ft-error" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button type="button" className="ft-link" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}
