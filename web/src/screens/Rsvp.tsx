// RSVP progress (design/screens/Rsvp.dc.html): the five steps while the agent works.
import { useEffect, useState } from 'react'
import { useApp } from '../app-state'
import { place, siteName, whenShort } from '../format'
import { navigate, useNav } from '../nav'
import { STEP_ORDER, STEP_TEXT, isFinished, type RsvpStep, type StepState } from '../types'
import { Icon, Pill, RoundButton } from '../ui'
import { WatchPanel, useRun } from './run'

const STATE_TEXT: Record<StepState, string> = {
  done: 'Done',
  current: 'Doing now',
  waiting: 'Waiting',
  failed: 'Stopped here',
}

export function RsvpScreen({ runId }: { runId: string }) {
  const { back } = useNav()
  const { watchRun } = useApp()
  const { run, event, error } = useRun(runId, 1000)
  const [watching, setWatching] = useState(false)

  useEffect(() => {
    if (run?.status === 'needs_you') navigate({ name: 'needs-you', runId }, { replace: true })
    if (run?.status === 'done') navigate({ name: 'going', runId }, { replace: true })
  }, [run?.status, runId, navigate])

  useEffect(() => {
    document.getElementById('rsvp-title')?.focus({ preventScroll: true })
  }, [])

  const close = () => {
    if (run && !isFinished(run.status)) watchRun(runId, event?.title ?? 'your event')
    back()
  }

  const steps: RsvpStep[] =
    run && run.steps.length > 0
      ? run.steps
      : STEP_ORDER.map((key) => ({ run_id: runId, key, title: STEP_TEXT[key][0], detail: STEP_TEXT[key][1], state: 'waiting' }))

  const failed = run?.status === 'failed'
  const meta = event ? [whenShort(event.starts_at), place(event), `on ${siteName(event)}`].filter(Boolean).join(' · ') : ''

  return (
    <div className="scrim scrim-solid">
      <main className="sheet" aria-labelledby="rsvp-title" style={{ height: 'calc(100% - 84px)' }}>
        <span className="handle" aria-hidden="true" />
        <div className="sheet-close">
          <RoundButton label="Close" icon="close" onClick={close} />
        </div>

        <div className="stack-6">
          {failed ? <Pill tone="needs">Didn’t go through</Pill> : <Pill tone="pending">Pending</Pill>}
          <h2 id="rsvp-title" className="type-sheet-title" tabIndex={-1}>
            {event?.title ?? 'Your RSVP'}
          </h2>
          {meta && <span className="type-meta muted">{meta}</span>}
        </div>

        <section className="card card-shadow plan" aria-labelledby="plan-title">
          <h3 id="plan-title" className="type-section">
            Here’s the plan
          </h3>
          <ol className="steps">
            {steps.map((s) => (
              <li key={s.key} className={`step step-${s.state}`} aria-current={s.state === 'current' ? 'step' : undefined}>
                <StepMark state={s.state} />
                <div className="stack-2">
                  <span className="step-title">
                    {s.title}
                    <span className="sr-only">. {STATE_TEXT[s.state]}.</span>
                  </span>
                  <span className="type-fine muted">{s.detail}</span>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <div className="verified-line">
          <Icon name="shield" />
          <span>Acting for a verified person</span>
        </div>

        {failed && (
          <div className="card card-line stack-8" role="alert">
            <span className="row-title">The agent stopped</span>
            <span className="type-meta">{run?.stop_reason || 'Something on the page didn’t work as expected.'}</span>
            {event && (
              <a className="link type-meta" href={event.url} target="_blank" rel="noopener noreferrer">
                Finish on {siteName(event)}
              </a>
            )}
          </div>
        )}

        {watching && <WatchPanel runId={runId} live={!!run && !isFinished(run.status)} />}

        <div className="grow" />

        {error && !run && (
          <p className="type-fine danger center" role="alert">
            {error}
          </p>
        )}
        <p className="sr-only" role="status" aria-live="polite">
          {steps.find((s) => s.state === 'current')?.title ?? ''}
        </p>
        {!failed && (
          <span className="type-fine muted center">
            You can close this. We’ll tell you when you’re in, or if the agent needs you.
          </span>
        )}
        <button type="button" className="btn btn-quiet" aria-expanded={watching} onClick={() => setWatching((w) => !w)}>
          {watching ? 'Hide the agent’s screen' : 'Watch the agent'}
        </button>
      </main>
    </div>
  )
}

function StepMark({ state }: { state: StepState }) {
  if (state === 'done')
    return (
      <span className="mark mark-done" aria-hidden="true">
        <Icon name="check" size={13} stroke={3} />
      </span>
    )
  if (state === 'failed')
    return (
      <span className="mark mark-failed" aria-hidden="true">
        <Icon name="close" size={12} stroke={3} />
      </span>
    )
  if (state === 'current')
    return (
      <span className="mark mark-current" aria-hidden="true">
        <span />
      </span>
    )
  return <span className="mark mark-waiting" aria-hidden="true" />
}
