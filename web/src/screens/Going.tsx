// You're going (design/screens/Going.dc.html).
import { useEffect } from 'react'
import { siteName, weekdayTime } from '../format'
import { useNav } from '../nav'
import { Icon, NavLink } from '../ui'
import { useRun } from './run'

export function GoingScreen({ runId }: { runId: string }) {
  const { navigate } = useNav()
  const { run, event } = useRun(runId, 1000)
  const waitlist = run?.outcome === 'waitlist'
  const site = event ? siteName(event) : 'Luma'

  useEffect(() => {
    document.getElementById('going-title')?.focus({ preventScroll: true })
  }, [])

  // The host is only told when step 3 really wrote the disclosure into a note on the form.
  // Most Luma forms have no note field; then step 3's detail says so, and so does this row.
  const toldHost = run?.steps.find((s) => s.key === 'told_host')
  const noteLeft = !!toldHost && toldHost.state === 'done' && toldHost.detail.includes('Registered by Event Agent')
  const noteRow = noteLeft
    ? { title: 'Host told who registered you', detail: 'An agent, for a verified person.' }
    : {
        title: 'No note to the host',
        detail:
          toldHost?.state === 'done' && toldHost.detail && !toldHost.detail.startsWith('“')
            ? toldHost.detail
            : `No note field on ${site}’s form, so the host sees a normal registration.`,
      }

  const checks = [
    waitlist
      ? { title: `On the waitlist on ${site}`, detail: `${site} shows you on the waitlist. You’ll hear if a spot opens.` }
      : { title: `Registered on ${site}`, detail: `${site} shows you as going. Ticket in your email.` },
    noteRow,
    { title: 'Added to your calendar', detail: 'It shows up in your week.' },
    { title: 'Remembered', detail: 'The agent counts this next time it picks for you.' },
  ]

  return (
    <main className="screen going" aria-labelledby="going-title">
      <span className={`big-check${waitlist ? ' big-check-wait' : ''}`} aria-hidden="true">
        <Icon name="check" size={48} stroke={2.4} />
      </span>
      <div className="stack-8 center">
        <h1 id="going-title" className="type-title" tabIndex={-1}>
          {waitlist ? 'You’re on the waitlist' : 'You’re going'}
        </h1>
        {event && (
          <p className="type-sub muted">
            {event.title} · {weekdayTime(event.starts_at)}
          </p>
        )}
      </div>

      <ul className="card card-line list-card" aria-label="What the agent did">
        {checks.map((c) => (
          <li key={c.title} className="check-row">
            <Icon name="check" size={20} stroke={2.2} className="going-ink" />
            <div className="stack-2">
              <span className="row-title-sm">{c.title}</span>
              <span className="type-fine muted">{c.detail}</span>
            </div>
          </li>
        ))}
      </ul>

      <div className="grow" />
      <button type="button" className="btn btn-primary" onClick={() => navigate({ name: 'week' })}>
        Back to my week
      </button>
      <NavLink to={{ name: 'activity' }} className="link type-meta center">
        See what the agent did
      </NavLink>
    </main>
  )
}
