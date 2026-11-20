// Shared by RSVP progress, Needs you and You're going: load a run and its event, and the
// "Watch the agent" panel.
import { useEffect, useRef, useState } from 'react'
import { getEvent, getRsvp, getRsvpFrame } from '../api'
import { knownEvent, rememberEvents } from '../app-state'
import { errorText } from '../errors'
import type { Event, RsvpRun } from '../types'
import { useInterval } from '../ui'

export function useRun(runId: string, pollMs: number) {
  const [run, setRun] = useState<RsvpRun | null>(null)
  const [event, setEvent] = useState<Event | null>(null)
  const [error, setError] = useState('')

  const refresh = async () => {
    try {
      const r = await getRsvp(runId)
      setRun(r)
      setError('')
      return r
    } catch (err) {
      setError(errorText(err))
      return null
    }
  }

  useEffect(() => {
    setRun(null)
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  useEffect(() => {
    if (!run) return
    const cached = knownEvent(run.event_id)
    if (cached) {
      setEvent(cached)
      return
    }
    getEvent(run.event_id)
      .then((e) => {
        rememberEvents([e])
        setEvent(e)
      })
      .catch(() => {})
  }, [run?.event_id]) // eslint-disable-line react-hooks/exhaustive-deps

  // needs_you can go back to running after an answer, so only done and failed stop the polling.
  useInterval(refresh, run && (run.status === 'done' || run.status === 'failed') ? null : pollMs)

  return { run, setRun, event, error, refresh }
}

/** The hosted Chrome screen, refreshed every second. Shows nothing until the first frame exists. */
export function WatchPanel({ runId, live }: { runId: string; live: boolean }) {
  const [src, setSrc] = useState<string | null>(null)
  const [missing, setMissing] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const urlRef = useRef<string | null>(null)

  const load = async () => {
    try {
      const blob = await getRsvpFrame(runId)
      if (!blob) {
        setMissing(true)
        return
      }
      const url = URL.createObjectURL(blob)
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      urlRef.current = url
      setSrc(url)
      setMissing(false)
    } catch {
      // keep the last frame
    }
  }

  useEffect(() => {
    void load()
    ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    return () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  useInterval(load, live ? 1000 : null)

  return (
    <div ref={ref} className="card card-line watch" aria-label="The agent’s screen">
      {src && !missing ? (
        <img src={src} alt="The agent’s screen in its hosted Chrome, updated every second" />
      ) : (
        <p className="type-fine muted">The agent’s screen shows here once it opens the event page.</p>
      )}
    </div>
  )
}
