// Small shared pieces: icons, tiles, pills, buttons, the tab bar, the sheet and a polling hook.
import { useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from 'react'
import { hrefFor, useNav, type Route } from './nav'
import { initials, tileGradient } from './format'
import type { PickTag } from './types'

// ---------------------------------------------------------------- icons (24px grid, stroke)

const paths = {
  check: <path d="M5 12l5 5 9-10" />,
  back: <path d="M15 5l-7 7 7 7" />,
  chevron: <path d="M9 5l7 7-7 7" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  shield: (
    <>
      <path d="M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z" />
      <path d="M9 12l2 2 4-4" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  pin: (
    <>
      <path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z" />
      <circle cx="12" cy="10" r="2.3" />
    </>
  ),
  ticket: (
    <>
      <path d="M4 7.5h16v3a1.8 1.8 0 0 0 0 3.6v3H4v-3a1.8 1.8 0 0 0 0-3.6z" />
      <path d="M14 7.5v9.6" strokeDasharray="1.5 2" />
    </>
  ),
  person: (
    <>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4 4" />
    </>
  ),
  screen: (
    <>
      <rect x="3.5" y="5" width="17" height="12" rx="2" />
      <path d="M9 20h6" />
    </>
  ),
} as const

export type IconName = keyof typeof paths

export function Icon({ name, size = 20, stroke = 1.8, className }: { name: IconName; size?: number; stroke?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {paths[name]}
    </svg>
  )
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className="logo" style={{ width: size, height: size, borderRadius: size / 4 }} aria-hidden="true">
      <Icon name="check" size={size * 0.57} stroke={2.4} />
    </span>
  )
}

// ---------------------------------------------------------------- event bits

export function Tile({ id, title, size = 64 }: { id: string; title: string; size?: number }) {
  return (
    <span className="tile" style={{ background: tileGradient(id), width: size, height: size }} aria-hidden="true">
      {initials(title)}
    </span>
  )
}

export type Tone = 'going' | 'pending' | 'needs' | 'wait' | 'invited' | 'quiet'

export function Pill({ tone, children, small }: { tone: Tone; children: ReactNode; small?: boolean }) {
  return <span className={`pill tone-${tone}${small ? ' pill-small' : ''}`}>{children}</span>
}

export const tagTone = (tag: PickTag | null | undefined): Tone =>
  tag === 'Strong match' ? 'going' : tag === 'Match' ? 'invited' : 'quiet'

// ---------------------------------------------------------------- buttons

export function RoundButton({ label, icon, onClick }: { label: string; icon: IconName; onClick: () => void }) {
  return (
    <button type="button" className="round-btn" aria-label={label} onClick={onClick}>
      <Icon name={icon} />
    </button>
  )
}

export function BackButton({ label = 'Back' }: { label?: string }) {
  const { back } = useNav()
  return <RoundButton label={label} icon="back" onClick={back} />
}

// ---------------------------------------------------------------- tab bar

const TABS: { route: Route; label: string }[] = [
  { route: { name: 'week' }, label: 'Week' },
  { route: { name: 'discover' }, label: 'Discover' },
  { route: { name: 'sites' }, label: 'Sites' },
  { route: { name: 'profile' }, label: 'Profile' },
]

export function TabBar({ active }: { active: Route['name'] }) {
  const { navigate } = useNav()
  return (
    <nav className="tabbar" aria-label="Main">
      {TABS.map((t) => (
        <a
          key={t.label}
          href={hrefFor(t.route)}
          aria-current={t.route.name === active ? 'page' : undefined}
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey) return
            e.preventDefault()
            navigate(t.route)
          }}
        >
          {t.label}
        </a>
      ))}
    </nav>
  )
}

/** A link that navigates in-app. */
export function NavLink({ to, className, children, ...rest }: { to: Route; className?: string; children: ReactNode; 'aria-label'?: string }) {
  const { navigate } = useNav()
  return (
    <a
      href={hrefFor(to)}
      className={className}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || to.name === 'ops') return
        e.preventDefault()
        navigate(to)
      }}
      {...rest}
    >
      {children}
    </a>
  )
}

// ---------------------------------------------------------------- sheet

/** A bottom sheet inside the phone frame. Escape and the scrim close it; focus moves to its title. */
export function Sheet({
  labelledBy,
  onClose,
  height,
  solid,
  children,
}: {
  labelledBy: string
  onClose: () => void
  height: number
  solid?: boolean
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const title = document.getElementById(labelledBy)
    title?.focus({ preventScroll: true })
    return () => opener?.focus?.({ preventScroll: true })
  }, [labelledBy])
  return (
    <div
      className={`scrim${solid ? ' scrim-solid' : ''}`}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose()
      }}
    >
      <div
        ref={ref}
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        style={{ height: `calc(100% - ${844 - height}px)` }}
      >
        <span className="handle" aria-hidden="true" />
        <div className="sheet-close">
          <RoundButton label="Close" icon="close" onClick={onClose} />
        </div>
        {children}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- hooks

/** Calls fn every `ms` while ms is not null. The latest fn is always used. */
export function useInterval(fn: () => void, ms: number | null) {
  const saved = useRef(fn)
  useLayoutEffect(() => {
    saved.current = fn
  })
  useEffect(() => {
    if (ms === null) return
    const id = window.setInterval(() => saved.current(), ms)
    return () => window.clearInterval(id)
  }, [ms])
}

/** FLIP: animates children marked data-flip="key" from their old spot when the list reorders. */
export function useFlip(container: RefObject<HTMLElement | null>, order: string) {
  const prev = useRef(new Map<string, number>())
  useLayoutEffect(() => {
    const el = container.current
    if (!el) return
    const items = Array.from(el.querySelectorAll<HTMLElement>('[data-flip]'))
    const nextTops = new Map<string, number>()
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const animate = prev.current.size > 0 && !reduce
    for (const item of items) {
      const key = item.dataset.flip!
      const top = item.offsetTop
      nextTops.set(key, top)
      if (!animate) continue
      const before = prev.current.get(key)
      if (before === undefined) {
        item.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300, easing: 'ease-out' })
        continue
      }
      const dy = before - top
      if (Math.abs(dy) < 1) continue
      item.style.zIndex = dy > 0 ? '2' : '1'
      const a = item.animate(
        [
          { transform: `translateY(${dy}px)`, backgroundColor: 'var(--surface)' },
          { transform: 'translateY(0)', backgroundColor: 'var(--bg)' },
        ],
        { duration: 560, easing: 'cubic-bezier(.2,.75,.2,1)' },
      )
      a.onfinish = () => {
        item.style.zIndex = ''
      }
    }
    prev.current = nextTops
  }, [container, order])
}
