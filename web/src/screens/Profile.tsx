// Profile (design/screens/Profile.dc.html): identity, memory, limits, sites, delete.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  deleteMe,
  getCalendar,
  getMemory,
  getProfile,
  getSites,
  patchMe,
  putProfile,
  type AboutYou,
  type MePatch,
  type ProfileKey,
} from '../api'
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

const ABOUT_FIELDS: { key: ProfileKey; label: string; type?: string; placeholder: string }[] = [
  { key: 'title', label: 'Job title', placeholder: 'ML Engineer' },
  { key: 'company', label: 'Company', placeholder: 'Where you work' },
  { key: 'school', label: 'School', placeholder: 'Where you studied' },
  { key: 'linkedin', label: 'LinkedIn', type: 'url', placeholder: 'https://www.linkedin.com/in/…' },
  { key: 'github', label: 'GitHub', type: 'url', placeholder: 'https://github.com/…' },
  { key: 'website', label: 'Website', type: 'url', placeholder: 'https://…' },
  { key: 'location', label: 'Location', placeholder: 'San Francisco' },
  { key: 'phone', label: 'Phone', type: 'tel', placeholder: 'Only if you want it on forms' },
]

/** "About you": what the agent may put on registration forms. Each field is one memory in Cognee. */
function AboutYouRow({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const [saved, setSaved] = useState<AboutYou | null>(null)
  const [draft, setDraft] = useState<Partial<Record<ProfileKey, string>>>({})
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getProfile()
      .then((p) => {
        setSaved(p)
        setDraft(Object.fromEntries(Object.entries(p).map(([k, v]) => [k, v ?? ''])))
      })
      .catch(() => setSaved(null))
  }, [])

  const filled = saved ? ABOUT_FIELDS.filter((f) => saved[f.key]).map((f) => f.label) : []
  const bioFilled = Boolean(saved?.bio)
  const summary = filled.length || bioFilled
    ? `${[...filled, ...(bioFilled ? ['About me'] : [])].slice(0, 3).join(', ')}${filled.length + (bioFilled ? 1 : 0) > 3 ? '…' : ''} · fills registration forms`
    : 'Add your details · the agent fills registration forms with them'

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setStatus('')
    try {
      // Send only what changed; an emptied field is sent as "" so the agent forgets it.
      const changed = Object.fromEntries(
        Object.entries(draft).filter(([k, v]) => (v ?? '').trim() !== ((saved?.[k as ProfileKey] ?? '') as string)),
      )
      const next = Object.keys(changed).length ? await putProfile(changed) : saved
      if (next) {
        setSaved(next)
        setDraft(Object.fromEntries(Object.entries(next).map(([k, v]) => [k, v ?? ''])))
      }
      setStatus('Saved. The agent uses this on every registration form.')
    } catch (err) {
      setStatus(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <EditRow label="About you" value={summary} open={open} onToggle={onToggle}>
      <form className="stack-10" onSubmit={submit}>
        {ABOUT_FIELDS.map((f) => (
          <label key={f.key} className="stack-4">
            <span className="type-fine muted">{f.label}</span>
            <input
              className="field"
              type={f.type ?? 'text'}
              value={draft[f.key] ?? ''}
              placeholder={f.placeholder}
              autoComplete="off"
              onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
            />
          </label>
        ))}
        <label className="stack-4">
          <span className="type-fine muted">About me</span>
          <textarea
            className="field"
            rows={3}
            value={draft.bio ?? ''}
            placeholder="One or two lines on what you build. The agent uses it for open-ended questions."
            onChange={(e) => setDraft({ ...draft, bio: e.target.value })}
          />
        </label>
        <p className="type-fine muted">
          Kept in the agent's memory (Cognee) and shown under What the agent remembers. The agent never
          invents anything that isn't here.
        </p>
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        {status && (
          <p className="type-fine muted" role="status">
            {status}
          </p>
        )}
      </form>
    </EditRow>
  )
}

export function ProfileScreen() {
  const { me, setMe } = useApp()
  const { navigate } = useNav()
  const [factCount, setFactCount] = useState<number | null>(null)
  const [sitesText, setSitesText] = useState('Luma')
  const [open, setOpen] = useState<'about' | 'limit' | 'ask' | null>(null)
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
        <AboutYouRow open={open === 'about'} onToggle={() => setOpen(open === 'about' ? null : 'about')} />
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
