// The app shell: phone frame, routes, tab bar, the current user, and RSVP hand-offs.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { getEvent, getMe, getRsvp, startRsvp } from './api'
import { AppContext, flag, knownEvent, rememberEvents, sessionValue, setFlag, type AppState } from './app-state'
import { ApiError, errorText } from './errors'
import * as features from './features'
import { navigate, useNav, type Route } from './nav'
import { DiscoverScreen } from './screens/Discover'
import { EventSheet } from './screens/EventSheet'
import { GoingScreen } from './screens/Going'
import { MemoryScreen } from './screens/Memory'
import { NeedsYouScreen } from './screens/NeedsYou'
import { ProfileScreen } from './screens/Profile'
import { RsvpScreen } from './screens/Rsvp'
import { VerifyScreen } from './screens/Verify'
import type { Event, User } from './types'
import { Logo, RoundButton, TabBar, useInterval } from './ui'

const TAB_ROUTES = new Set<Route['name']>(['week', 'discover', 'sites', 'profile'])

// Back from Persona's hosted flow with ?verified=1: note it, then drop it from the URL.
const returnedFromPersona = new URLSearchParams(window.location.search).get('verified') === '1'
const openedWithoutRoute =
  !returnedFromPersona && window.location.hash.replace(/^#\/?/, '') === '' && window.location.pathname !== '/ops'
if (returnedFromPersona) {
  const params = new URLSearchParams(window.location.search)
  params.delete('verified')
  const q = params.toString()
  window.history.replaceState(null, '', `${window.location.pathname}${q ? `?${q}` : ''}`)
  navigate({ name: 'verify' }, { replace: true })
}

interface Watched {
  runId: string
  title: string
}
interface Banner {
  runId: string
  text: string
  route: Route
}

export default function App() {
  const { route } = useNav()
  const [me, setMeState] = useState<User | null>(null)
  const [meError, setMeError] = useState('')
  const meRef = useRef<User | null>(null)
  const [watched, setWatched] = useState<Watched | null>(null)
  const [banner, setBanner] = useState<Banner | null>(null)
  const [onboarding] = useState(() => !flag('onboarded'))

  const setMe = useCallback((u: User) => {
    meRef.current = u
    setMeState(u)
  }, [])

  const reloadMe = useCallback(async () => {
    setMeError('')
    try {
      const u = await getMe()
      setMe(u)
    } catch (err) {
      setMeError(errorText(err))
    }
  }, [setMe])

  useEffect(() => {
    void reloadMe().then(() => {
      const u = meRef.current
      if (u && openedWithoutRoute && !u.verified && !flag('onboarded')) navigate({ name: 'welcome' }, { replace: true })
    })
  }, [reloadMe])

  const rsvp = useCallback(
    async (event: Event) => {
      rememberEvents([event])
      const toVerify = () => {
        sessionValue('pendingRsvp', event.id)
        navigate({ name: 'verify' })
      }
      if (!meRef.current?.verified) return toVerify()
      try {
        const run = await startRsvp(event.id)
        navigate({ name: 'rsvp', runId: run.id }, { replace: true })
      } catch (err) {
        if (err instanceof ApiError && err.status === 403) {
          if (meRef.current) setMe({ ...meRef.current, verified: false })
          return toVerify()
        }
        throw err
      }
    },
    [setMe],
  )

  const watchRun = useCallback((runId: string, title: string) => setWatched({ runId, title }), [])

  // "We'll tell you when you're in": poll a run the user closed, then show a banner.
  const watchingHere = watched && 'runId' in route && route.runId === watched.runId
  useInterval(
    async () => {
      if (!watched) return
      try {
        const run = await getRsvp(watched.runId)
        if (run.status === 'done') {
          setBanner({ runId: run.id, text: `You’re going to ${watched.title}.`, route: { name: 'going', runId: run.id } })
          setWatched(null)
        } else if (run.status === 'needs_you') {
          setBanner({ runId: run.id, text: `The agent needs you for ${watched.title}.`, route: { name: 'needs-you', runId: run.id } })
          setWatched(null)
        } else if (run.status === 'failed') {
          setBanner({ runId: run.id, text: `Your RSVP to ${watched.title} didn’t go through.`, route: { name: 'rsvp', runId: run.id } })
          setWatched(null)
        }
      } catch {
        // try again next tick
      }
    },
    watched && !watchingHere ? 2000 : null,
  )

  const ctx: AppState | null = useMemo(
    () => (me ? { me, setMe, reloadMe, rsvp, watchRun } : null),
    [me, setMe, reloadMe, rsvp, watchRun],
  )

  // Verify finished or skipped: carry on with a pending RSVP, the first-run flow, or go back.
  const afterVerify = useCallback(
    async (verified: boolean) => {
      setFlag('onboarded')
      if (!verified) setFlag('skipVerify')
      const pending = sessionValue('pendingRsvp')
      sessionValue('pendingRsvp', null)
      if (verified && pending) {
        try {
          const event = knownEvent(pending) ?? (await getEvent(pending))
          await rsvp(event)
          return
        } catch {
          // fall through to For you
        }
      }
      if (onboarding) navigate({ name: 'connect' }, { replace: true })
      else navigate({ name: 'discover' }, { replace: true })
    },
    [onboarding, rsvp],
  )

  if (route.name === 'ops') {
    return <features.OpsScreen />
  }

  if (!ctx) {
    return (
      <div className="frame">
        <main className="screen loading" aria-busy={!meError}>
          <Logo size={44} />
          {meError ? (
            <>
              <p className="type-meta" role="alert">
                {meError}
              </p>
              <button type="button" className="btn btn-quiet" onClick={() => void reloadMe()}>
                Try again
              </button>
            </>
          ) : (
            <p className="sr-only">Loading Event Agent</p>
          )}
        </main>
      </div>
    )
  }

  let screen: ReactNode
  let overlay: ReactNode = null
  let tab: Route['name'] | null = TAB_ROUTES.has(route.name) ? route.name : null

  switch (route.name) {
    case 'welcome':
      screen = <features.WelcomeScreen />
      break
    case 'verify':
      screen = (
        <VerifyScreen
          key={returnedFromPersona ? 'returning' : 'verify'}
          returning={returnedFromPersona}
          forRsvp={!!sessionValue('pendingRsvp')}
          onDone={() => void afterVerify(true)}
          onSkip={() => void afterVerify(false)}
        />
      )
      break
    case 'connect':
      screen = <features.ConnectScreen />
      break
    case 'taste':
      screen = <features.TasteScreen />
      break
    case 'week':
      screen = <features.WeekScreen />
      break
    case 'sites':
      screen = <features.ConnectScreen />
      break
    case 'ask':
      screen = <features.AskScreen />
      break
    case 'activity':
      screen = <features.ActivityScreen />
      break
    case 'discover':
      screen = <DiscoverScreen />
      break
    case 'event':
      screen = <DiscoverScreen />
      tab = 'discover'
      overlay = <EventSheet key={route.id} id={route.id} />
      break
    case 'rsvp':
      screen = <RsvpScreen key={route.runId} runId={route.runId} />
      break
    case 'needs-you':
      screen = <NeedsYouScreen key={route.runId} runId={route.runId} />
      break
    case 'going':
      screen = <GoingScreen key={route.runId} runId={route.runId} />
      break
    case 'memory':
      screen = <MemoryScreen />
      break
    case 'profile':
      screen = <ProfileScreen />
      break
  }

  return (
    <AppContext.Provider value={ctx}>
      <div className="frame">
        <div className={`viewport${tab ? ' with-tabbar' : ''}`} inert={!!overlay}>
          {screen}
        </div>
        {tab && (
          <div className="tabbar-slot" inert={!!overlay}>
            <TabBar active={tab} />
          </div>
        )}
        {overlay}
        {banner && !('runId' in route && route.runId === banner.runId) && (
          <div className="banner card card-shadow" role="status">
            <Logo size={32} />
            <span className="type-meta grow-x">{banner.text}</span>
            <button
              type="button"
              className="chip chip-on"
              onClick={() => {
                navigate(banner.route)
                setBanner(null)
              }}
            >
              View
            </button>
            <RoundButton label="Dismiss" icon="close" onClick={() => setBanner(null)} />
          </div>
        )}
      </div>
    </AppContext.Provider>
  )
}
