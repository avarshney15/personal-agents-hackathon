// Your week (design/screens/Week.dc.html). The shell draws the tab bar.
import { useNav } from '../nav'
import { RoundButton, Tile } from '../ui'
import { getWeek } from './data'
import { weekMeta, weekday } from './format'
import type { Clash, WeekDay, WeekItem } from './types'
import { ErrorLine, Loading, StatusPill, WEEK_STATUS, useLoad } from './ui'
import './features.css'

function clashDay(clash: Clash, days: WeekDay[]): string {
  const day = days.find((d) => d.items.some((i) => i.event.id === clash.event_id))
  return day?.weekday || weekday(clash.item.starts_at)
}

function EventRow({ item, onOpen }: { item: WeekItem; onOpen: () => void }) {
  const status = WEEK_STATUS[item.status] ?? WEEK_STATUS.pending
  const e = item.event
  return (
    <li>
      <button type="button" className="ft-event" onClick={onOpen} aria-label={`${e.title}, ${weekMeta(e)}, ${status.label}`}>
        <span className="ft-event-art">
          <Tile id={e.id} title={e.title} size={64} />
          <StatusPill tone={status.tone}>{status.label}</StatusPill>
        </span>
        <span className="ft-event-text">
          <span className="ft-event-title">{e.title}</span>
          <span className="ft-event-meta">{weekMeta(e)}</span>
        </span>
      </button>
    </li>
  )
}

export function WeekScreen() {
  const { navigate } = useNav()
  const { load, reload } = useLoad(getWeek)

  return (
    <main className="ft-screen ft-screen--list" aria-labelledby="ft-week-title" aria-busy={load.state === 'loading'}>
      <div className="ft-titlebar">
        <h1 id="ft-week-title" className="ft-title">
          Your week
        </h1>
        <RoundButton label="Ask" icon="search" onClick={() => navigate({ name: 'ask' })} />
      </div>

      {load.state === 'loading' && <Loading label="Loading your week" />}
      {load.state === 'error' && <ErrorLine message={load.message} onRetry={reload} />}

      {load.state === 'ready' &&
        load.data.clashes.map((c) => (
          <section key={`${c.event_id}-${c.item.id}`} className="ft-clash" aria-label="Clash">
            <h2 className="ft-clash-title">{clashDay(c, load.data.days)} clashes with your calendar</h2>
            <p className="ft-clash-text">{c.text}</p>
          </section>
        ))}

      {load.state === 'ready' && load.data.days.length === 0 && (
        <div className="ft-empty">
          <span className="ft-row-title">Nothing this week yet</span>
          <span className="ft-row-sub">Events you RSVP to, get invited to or join a waitlist for show up here.</span>
          <button type="button" className="ft-link" onClick={() => navigate({ name: 'discover' })}>
            See picks for you
          </button>
        </div>
      )}

      {load.state === 'ready' &&
        load.data.days.map((d) => (
          <section key={d.date} className="ft-day" aria-labelledby={`ft-day-${d.date}`}>
            <h2 id={`ft-day-${d.date}`} className="ft-day-head">
              <span>{d.label}</span>
              <span>{d.weekday}</span>
            </h2>
            <ul className="ft-list">
              {d.items.map((item) => (
                <EventRow key={item.event.id} item={item} onOpen={() => navigate({ name: 'event', id: item.event.id })} />
              ))}
            </ul>
          </section>
        ))}
    </main>
  )
}
