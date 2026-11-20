// Connect your sites (design/screens/Connect.dc.html). The same screen is the Sites tab.
import { useState } from 'react'
import { errorText } from '../errors'
import { useNav } from '../nav'
import { connectCalendar, connectSite, disconnectSite, getCalendar, getSites } from './data'
import type { CalendarStatus, Site, SiteConnection } from './types'
import { CalendarIcon, ErrorLine, Loading, SiteIcon, useLoad } from './ui'
import './features.css'

function siteState(s: SiteConnection): string {
  if (!s.connected) return 'Not connected'
  return `Connected · ${s.event_count} ${s.event_count === 1 ? 'event' : 'events'}`
}

function SiteRow({
  s,
  open,
  busy,
  onConnect,
  onManage,
  onDisconnect,
}: {
  s: SiteConnection
  open: boolean
  busy: boolean
  onConnect: () => void
  onManage: () => void
  onDisconnect: () => void
}) {
  const manageId = `ft-manage-${s.site}`
  return (
    <li className={`ft-site${open ? ' ft-site--open' : ''}`}>
      <div className="ft-row">
        <SiteIcon site={s.site} />
        <div className="ft-row-text">
          <span className="ft-row-title">{s.name}</span>
          <span className="ft-row-sub">{busy ? (s.connected ? 'Disconnecting' : 'Connecting') : siteState(s)}</span>
        </div>
        {s.connected ? (
          <button
            type="button"
            className="ft-btn-pill"
            aria-expanded={open}
            aria-controls={manageId}
            aria-label={`Manage ${s.name}`}
            onClick={onManage}
            disabled={busy}
          >
            Manage
          </button>
        ) : (
          <button
            type="button"
            className="ft-btn-pill ft-btn-pill--primary"
            aria-label={`Connect ${s.name}`}
            onClick={onConnect}
            disabled={busy}
          >
            Connect
          </button>
        )}
      </div>
      {s.connected && open && (
        <div id={manageId} className="ft-site-manage">
          <span>The agent reads your {s.name} events and RSVPs there.</span>
          <button type="button" className="ft-link" onClick={onDisconnect} disabled={busy}>
            Disconnect
          </button>
        </div>
      )}
    </li>
  )
}

export function ConnectScreen() {
  const { route, navigate } = useNav()
  const asTab = route.name === 'sites'
  const sites = useLoad(getSites)
  const cal = useLoad(getCalendar)
  const [busy, setBusy] = useState<Site | 'calendar' | null>(null)
  const [open, setOpen] = useState<Site | null>(null)
  const [error, setError] = useState('')

  async function change(site: Site, connect: boolean) {
    if (sites.load.state !== 'ready') return
    setBusy(site)
    setError('')
    try {
      const updated = connect ? await connectSite(site) : await disconnectSite(site)
      const list = sites.load.state === 'ready' ? sites.load.data : []
      sites.set(list.map((s) => (s.site === site ? updated : s)))
      if (!connect) setOpen(null)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(null)
    }
  }

  async function linkCalendar() {
    setBusy('calendar')
    setError('')
    try {
      cal.set(await connectCalendar())
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(null)
    }
  }

  const calendar: CalendarStatus | null = cal.load.state === 'ready' ? cal.load.data : null

  return (
    <main className="ft-screen" aria-labelledby="ft-connect-title">
      <div className="ft-head">
        <h1 id="ft-connect-title" className="ft-title">
          Connect your sites
        </h1>
        <p className="ft-sub">
          You log in once on your own cloud browser. The agent reads your events and RSVPs there. We never keep your
          password.
        </p>
      </div>

      <h2 id="ft-sites-label" className="ft-label ft-h-reset">
        Event sites
      </h2>
      {sites.load.state === 'loading' && <Loading label="Loading your sites" />}
      {sites.load.state === 'error' && <ErrorLine message={sites.load.message} onRetry={sites.reload} />}
      {sites.load.state === 'ready' && (
        <ul className="ft-card ft-card--list ft-list" aria-labelledby="ft-sites-label">
          {sites.load.data.map((s) => (
            <SiteRow
              key={s.site}
              s={s}
              open={open === s.site}
              busy={busy === s.site}
              onConnect={() => change(s.site, true)}
              onManage={() => setOpen(open === s.site ? null : s.site)}
              onDisconnect={() => change(s.site, false)}
            />
          ))}
        </ul>
      )}

      <h2 className="ft-label ft-h-reset">Your calendar</h2>
      <div className="ft-card ft-card--row">
        <span className="ft-cal-icon">
          <CalendarIcon />
        </span>
        <div className="ft-row-text">
          <span className="ft-row-title">Google Calendar</span>
          <span className="ft-row-sub">
            {busy === 'calendar'
              ? 'Connecting'
              : calendar?.connected
                ? `Connected, read only, so clashes include your meetings`
                : 'Read only, so clashes include your meetings'}
          </span>
        </div>
        {calendar?.connected ? (
          <span className="ft-btn-pill ft-btn-pill--static">Connected</span>
        ) : (
          <button
            type="button"
            className="ft-btn-pill ft-btn-pill--primary"
            aria-label="Connect Google Calendar"
            onClick={linkCalendar}
            disabled={busy === 'calendar' || cal.load.state === 'loading'}
          >
            Connect
          </button>
        )}
      </div>

      {error && <ErrorLine message={error} />}

      {!asTab && (
        <>
          <div className="ft-grow" />
          <button type="button" className="ft-primary" onClick={() => navigate({ name: 'taste' })}>
            Continue
          </button>
          <p className="ft-fine ft-center">You can add more later in Sites.</p>
        </>
      )}
    </main>
  )
}
