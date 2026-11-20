// Needs you (design/screens/NeedsYou.dc.html): the agent stopped and needs a code or the user.
import { useEffect, useState, type FormEvent } from 'react'
import { answerRsvp, cancelRsvp } from '../api'
import { errorText } from '../errors'
import { siteName } from '../format'
import { navigate } from '../nav'
import { STEP_ORDER } from '../types'
import { BackButton, Pill } from '../ui'
import { WatchPanel, useRun } from './run'

export function NeedsYouScreen({ runId }: { runId: string }) {
  const { run, event, error: loadError } = useRun(runId, 2000)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [watching, setWatching] = useState(false)

  useEffect(() => {
    if (!run) return
    if (run.status === 'done') navigate({ name: 'going', runId }, { replace: true })
    if (run.status === 'running' || run.status === 'queued' || run.status === 'failed')
      navigate({ name: 'rsvp', runId }, { replace: true })
  }, [run?.status, runId, navigate]) // eslint-disable-line react-hooks/exhaustive-deps

  const need = run?.need ?? null
  const stepIndex = need ? STEP_ORDER.indexOf(need.step_key) + 1 : 0
  const minutesLeft = need?.expires_at ? Math.max(0, Math.round((Date.parse(need.expires_at) - Date.now()) / 60000)) : null

  async function submit(e: FormEvent) {
    e.preventDefault()
    const value = code.replace(/\s+/g, '')
    if (!value) return
    setBusy(true)
    setError('')
    try {
      await answerRsvp(runId, value)
      navigate({ name: 'rsvp', runId }, { replace: true })
    } catch (err) {
      setError(errorText(err))
      setBusy(false)
    }
  }

  async function stop() {
    setBusy(true)
    try {
      await cancelRsvp(runId)
      navigate({ name: 'rsvp', runId }, { replace: true })
    } catch (err) {
      setError(errorText(err))
      setBusy(false)
    }
  }

  const site = event ? siteName(event) : 'the site'

  return (
    <main className="screen needs" aria-labelledby="needs-title">
      <div className="top-left">
        <BackButton />
      </div>
      <Pill tone="needs">Needs you</Pill>
      <div className="stack-8">
        <h1 id="needs-title" className="type-title">
          {need?.title || 'The agent needs you'}
        </h1>
        <p className="type-sub muted">
          {need?.detail || run?.stop_reason || (loadError ? loadError : `The agent stopped before finishing on ${site}.`)}
        </p>
      </div>

      <form id="answer-form" className="stack-8" onSubmit={submit}>
        {need?.input_label && (
          <>
            <label htmlFor="need-code" className="sr-only">
              {need.input_label}
            </label>
            <input
              id="need-code"
              className="field code-field"
              type="text"
              inputMode={need.kind === 'code' ? 'numeric' : 'text'}
              autoComplete="one-time-code"
              placeholder={need.kind === 'code' ? '000 000' : undefined}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              disabled={busy}
            />
          </>
        )}
      </form>

      <div className="card card-line stack-4">
        <span className="row-title-sm">{event?.title ?? 'Your RSVP'}</span>
        <span className="type-fine muted">
          {stepIndex > 0 ? `Paused at step ${stepIndex} of ${STEP_ORDER.length}` : 'Paused'}
          {minutesLeft != null && ` · we hold your place for ${minutesLeft} minute${minutesLeft === 1 ? '' : 's'}`}
        </span>
      </div>

      {watching && <WatchPanel runId={runId} live />}

      <div className="grow" />

      {error && (
        <p className="type-fine danger center" role="alert">
          {error}
        </p>
      )}
      {need?.input_label ? (
        <button type="submit" form="answer-form" className="btn btn-primary" disabled={busy || !code.trim()}>
          {busy ? 'Sending…' : 'Continue'}
        </button>
      ) : (
        event && (
          <a className="btn btn-primary" href={event.url} target="_blank" rel="noopener noreferrer">
            Finish on {site}
          </a>
        )
      )}
      <button type="button" className="btn btn-quiet" aria-expanded={watching} onClick={() => setWatching((w) => !w)}>
        {watching ? 'Hide the agent’s screen' : 'Use the browser instead'}
      </button>
      <button type="button" className="text-btn center" onClick={stop} disabled={busy}>
        Stop this RSVP
      </button>
    </main>
  )
}
