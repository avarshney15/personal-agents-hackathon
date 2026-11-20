// Profile (design/screens/Profile.dc.html): identity, memory, limits, sites, delete.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { deleteMe, getCalendar, getMemory, getSites, patchMe, type MePatch } from '../api'
import { setFlag, useApp } from '../app-state'
import { errorText } from '../errors'
import { initials, shortDate } from '../format'
import { useNav, type Route } from '../nav'
import type { AskBefore } from '../types'
import { Icon, NavLink, Pill } from '../ui'

const ASK_TEXT: Record<AskBefore, string> = {
  paid: 'Only for paid events',
  always: 'Always',
  never: 'Never',
}
const LIMITS = [1, 2, 5, 10]
const money = (n: number) => `$${n.toFixed(2)}`

export function ProfileScreen() {
  const { me, setMe } = useApp()
  const { navigate } = useNav()
  const [factCount, setFactCount] = useState<number | null>(null)
  const [sitesText, setSitesText] = useState('Luma')
  const [open, setOpen] = useState<'limit' | 'ask' | null>(null)
  const [error, setError] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [busy, setBusy] = useState(false)
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    getMemory()
      .then((g) => setFactCount(g.said.length + g.went.length + g.skipped.length))
      .catch(() => {})
    Promise.allSettled([getSites(), getCalendar()]).then(([s, c]) => {
      const names = s.status === 'fulfilled' ? s.value.filter((x) => x.connected).map((x) => x.name) : ['Luma']
      if (c.status === 'fulfilled' && c.value.connected) names.push(c.value.source)
      setSitesText(names.length ? names.join(', ') : 'None connected yet')
    })
  }, [])

  useEffect(() => {
    if (confirmDelete) confirmRef.current?.focus()
  }, [confirmDelete])

  async function save(patch: MePatch) {
    setError('')
    try {
      setMe(await patchMe(patch))
    } catch (err) {
      setError(errorText(err))
    }
  }

  async function remove() {
    setBusy(true)
    setError('')
    try {
      await deleteMe()
      setFlag('onboarded', false)
      setFlag('skipVerify', false)
      navigate({ name: 'welcome' })
    } catch (err) {
      setError(errorText(err))
      setBusy(false)
    }
  }

  const limit = me.spend_limit_usd ?? 2
  const askBefore = me.ask_before ?? 'paid'

  return (
    <main className="screen profile" aria-labelledby="profile-title">
      <div className="profile-head">
        <span className="avatar" aria-hidden="true">
          {initials(me.name)}
        </span>
        <div className="stack-4">
          <h1 id="profile-title" className="type-sheet-title">
            {me.name}
          </h1>
          {me.verified ? <Pill tone="going">Verified person</Pill> : <Pill tone="needs">Not verified</Pill>}
        </div>
      </div>

      <ul className="card card-line list-card rows">
        <LinkRow
          to={{ name: 'verify' }}
          label="Identity"
          value={
            me.verified
              ? `Verified with Persona${me.verified_at ? ` · ${shortDate(me.verified_at)}` : ''}`
              : 'Not verified yet · verify once'
          }
        />
        <LinkRow
          to={{ name: 'memory' }}
          label="What the agent remembers"
          value={factCount == null ? 'Forget any of it' : `${factCount} ${factCount === 1 ? 'thing' : 'things'} · forget any`}
        />
      </ul>

      <ul className="card card-line list-card rows">
        <LinkRow to={{ name: 'activity' }} label="What the agent did" value="Every action and its steps" />
        <EditRow label="Spending limit" value={`${money(limit)} a day`} open={open === 'limit'} onToggle={() => setOpen(open === 'limit' ? null : 'limit')}>
          <Choices
            legend="Daily spending limit"
            options={LIMITS.map((n) => ({ value: String(n), label: money(n) }))}
            value={String(limit)}
            onChange={(v) => save({ spend_limit_usd: Number(v) })}
          />
          <p className="type-fine muted">The agent stops and asks before going over.</p>
        </EditRow>
        <EditRow label="Ask before RSVPing" value={ASK_TEXT[askBefore]} open={open === 'ask'} onToggle={() => setOpen(open === 'ask' ? null : 'ask')}>
          <Choices
            legend="Ask before RSVPing"
            options={(Object.keys(ASK_TEXT) as AskBefore[]).map((k) => ({ value: k, label: ASK_TEXT[k] }))}
            value={askBefore}
            onChange={(v) => save({ ask_before: v as AskBefore })}
          />
        </EditRow>
      </ul>

      <ul className="card card-line list-card rows">
        <LinkRow to={{ name: 'sites' }} label="Sites and calendar" value={sitesText} />
      </ul>

      {error && (
        <p className="type-fine danger" role="alert">
          {error}
        </p>
      )}

      {confirmDelete ? (
        <div className="card card-shadow stack-10" role="group" aria-labelledby="delete-title">
          <span id="delete-title" className="row-title">
            Delete your account?
          </span>
          <span className="type-meta muted">
            The agent forgets everything about you and stops acting for you. This can’t be undone.
          </span>
          <button ref={confirmRef} type="button" className="btn btn-danger" onClick={remove} disabled={busy}>
            {busy ? 'Deleting…' : 'Delete my account'}
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => setConfirmDelete(false)} disabled={busy}>
            Keep my account
          </button>
        </div>
      ) : (
        <button type="button" className="delete-row" onClick={() => setConfirmDelete(true)}>
          Delete my account
        </button>
      )}
    </main>
  )
}

function LinkRow({ to, label, value }: { to: Route; label: string; value: string }) {
  return (
    <li>
      <NavLink to={to} className="row">
        <span className="stack-2 grow-x">
          <span className="row-label">{label}</span>
          <span className="type-fine muted">{value}</span>
        </span>
        <Icon name="chevron" size={16} className="muted" />
      </NavLink>
    </li>
  )
}

function EditRow({
  label,
  value,
  open,
  onToggle,
  children,
}: {
  label: string
  value: string
  open: boolean
  onToggle: () => void
  children: ReactNode
}) {
  const id = `edit-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <li>
      <button type="button" className="row" aria-expanded={open} aria-controls={id} onClick={onToggle}>
        <span className="stack-2 grow-x">
          <span className="row-label">{label}</span>
          <span className="type-fine muted">{value}</span>
        </span>
        <Icon name="chevron" size={16} className={`muted chevron${open ? ' chevron-open' : ''}`} />
      </button>
      {open && (
        <div id={id} className="row-editor">
          {children}
        </div>
      )}
    </li>
  )
}

function Choices({
  legend,
  options,
  value,
  onChange,
}: {
  legend: string
  options: { value: string; label: string }[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <fieldset className="choices">
      <legend className="sr-only">{legend}</legend>
      {options.map((o) => (
        <label key={o.value} className={`chip ${o.value === value ? 'chip-on' : 'chip-quiet'}`}>
          <input
            type="radio"
            className="sr-only"
            name={legend}
            value={o.value}
            checked={o.value === value}
            onChange={() => onChange(o.value)}
          />
          {o.label}
        </label>
      ))}
    </fieldset>
  )
}
