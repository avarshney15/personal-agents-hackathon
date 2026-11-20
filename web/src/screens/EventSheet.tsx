// Event sheet (design/screens/Event.dc.html): meta, why it fits, clash, friends, RSVP.
import { useEffect, useState } from 'react'
import { getEvent, getEventDetail } from '../api'
import { knownEvent, lastFeed, rememberEvents, useApp } from '../app-state'
import { errorText } from '../errors'
import { friendsLine, placeLong, priceLine, siteName, whenLong } from '../format'
import { useNav } from '../nav'
import type { Event, EventDetail, PickTag } from '../types'
import { Icon, NavLink, Pill, Sheet, Tile, tagTone, type IconName } from '../ui'

export function EventSheet({ id }: { id: string }) {
  const { back } = useNav()
  const { me, rsvp } = useApp()
  const pick = lastFeed()?.picks.find((p) => p.event.id === id)
  const [event, setEvent] = useState<Event | null>(knownEvent(id) ?? null)
  const [detail, setDetail] = useState<EventDetail | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    getEventDetail(id)
      .then((d) => {
        if (!live) return
        rememberEvents([d.event])
        setDetail(d)
        setEvent(d.event)
      })
      .catch(async () => {
        // The detail route is newer; fall back to the plain event.
        if (knownEvent(id)) return
        try {
          const e = await getEvent(id)
          if (live) setEvent(e)
        } catch (err) {
          if (live) setError(errorText(err))
        }
      })
    return () => {
      live = false
    }
  }, [id])

  const tag: PickTag | null | undefined = detail?.tag ?? pick?.tag
  const why = detail?.why || pick?.why
  const friends = friendsLine(event?.friends_going)

  async function onRsvp() {
    if (!event) return
    setBusy(true)
    setError('')
    try {
      await rsvp(event)
    } catch (err) {
      setError(errorText(err))
      setBusy(false)
    }
  }

  const meta: { icon: IconName; text: string }[] = event
    ? [
        { icon: 'clock', text: whenLong(event.starts_at, event.ends_at) },
        ...(placeLong(event) ? [{ icon: 'pin' as const, text: placeLong(event) }] : []),
        { icon: 'ticket', text: priceLine(event) },
        ...(event.host ? [{ icon: 'person' as const, text: `Hosted by ${event.host}` }] : []),
      ]
    : []

  return (
    <Sheet labelledBy="event-title" onClose={back} height={780}>
      <div className="event-head">
        {event ? <Tile id={event.id} title={event.title} /> : <span className="tile sk" />}
        <div className="stack-4">
          <h2 id="event-title" className="type-sheet-title" tabIndex={-1}>
            {event?.title ?? 'Loading…'}
          </h2>
          {tag && <Pill tone={tagTone(tag)}>{tag}</Pill>}
        </div>
      </div>

      {meta.length > 0 && (
        <ul className="meta-list">
          {meta.map((m) => (
            <li key={m.icon} className="type-meta muted">
              <Icon name={m.icon} size={16} />
              <span>{m.text}</span>
            </li>
          ))}
        </ul>
      )}

      {why && (
        <section className="card card-line stack-8 why" aria-labelledby="why-title">
          <h3 id="why-title" className="type-section">
            Why it fits you
          </h3>
          <p className="type-meta">{why}</p>
          <NavLink to={{ name: 'memory' }} className="link type-meta">
            What the agent remembers
          </NavLink>
        </section>
      )}

      {detail?.clash && (
        <section className="card tone-wait-card stack-4" aria-label="Calendar clash">
          <span className="clash-title">Clashes with “{detail.clash.item.title}”</span>
          <span className="type-meta ink">{detail.clash.text}</span>
        </section>
      )}

      {friends && <span className="type-meta muted">{friends}</span>}

      <div className="grow" />

      {error && (
        <p className="type-fine danger center" role="alert">
          {error}
        </p>
      )}
      {!me.verified && <p className="type-fine muted center">You’ll verify once with Persona first. It takes about a minute.</p>}
      <button type="button" className="btn btn-primary" onClick={onRsvp} disabled={!event || busy}>
        {busy ? 'Starting…' : 'RSVP'}
      </button>
      {event && (
        <a className="btn btn-quiet" href={event.url} target="_blank" rel="noopener noreferrer">
          Open on {siteName(event)}
        </a>
      )}
    </Sheet>
  )
}
