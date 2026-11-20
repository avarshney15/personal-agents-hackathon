// Verify once with Persona (design/screens/Verify.dc.html).
import { useEffect, useRef, useState } from 'react'
import { getMe, getVerifyStatus, startVerify } from '../api'
import { useApp } from '../app-state'
import { errorText } from '../errors'
import { shortDate } from '../format'
import { disclosureFor } from '../types'
import { useNav } from '../nav'
import { Logo, useInterval } from '../ui'

const STEPS = [
  { n: '1', title: 'Photo of your ID', body: 'A driver’s licence or passport.' },
  { n: '2', title: 'A quick selfie', body: 'So Persona knows the ID is yours.' },
  { n: '3', title: 'Done, once', body: 'Every RSVP after this carries it.' },
]

type Phase = 'idle' | 'starting' | 'waiting' | 'error'

export function VerifyScreen({
  returning,
  forRsvp,
  onDone,
  onSkip,
}: {
  /** Back from Persona with ?verified=1: check status straight away. */
  returning: boolean
  /** The user tapped RSVP before verifying. */
  forRsvp: boolean
  onDone: () => void
  onSkip: () => void
}) {
  const { me, setMe } = useApp()
  const { back } = useNav()
  // Opened from Profile by someone already verified: show their status instead of the flow.
  const [alreadyVerified] = useState(me.verified && !returning)
  const finished = useRef(false)
  const [phase, setPhase] = useState<Phase>(returning ? 'waiting' : 'idle')
  const [personaUrl, setPersonaUrl] = useState<string | null>(null)
  const [blocked, setBlocked] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (me.verified && !alreadyVerified && !finished.current) {
      finished.current = true
      onDone()
    }
  }, [me.verified, alreadyVerified, onDone])

  useInterval(
    async () => {
      try {
        const { verified } = await getVerifyStatus()
        if (!verified) return
        // Reload for verified_at and anything else the backend set; fall back to a local flip.
        try {
          setMe(await getMe())
        } catch {
          setMe({ ...me, verified: true })
        }
      } catch {
        // keep polling; a blip shouldn't stop it
      }
    },
    phase === 'waiting' ? 2000 : null,
  )

  async function begin() {
    setPhase('starting')
    setError('')
    try {
      const session = await startVerify()
      if (session.url) {
        setPersonaUrl(session.url)
        // Not "noopener" in the features string: that makes window.open return null, and we
        // need the handle to tell a blocked popup from an opened one.
        const tab = window.open(session.url, '_blank')
        if (tab) tab.opener = null
        setBlocked(!tab)
      }
      setPhase('waiting')
    } catch (err) {
      setError(errorText(err))
      setPhase('error')
    }
  }

  const waiting = phase === 'waiting'

  return (
    <main className="screen verify" aria-labelledby="verify-title">
      <div className="brand-row">
        <Logo />
        <span className="type-brand-name">Event Agent</span>
      </div>

      <div className="stack-8">
        <h1 id="verify-title" className="type-title">
          Hosts will know it’s you
        </h1>
        <p className="type-sub muted">
          The agent never pretends to be a person. When it RSVPs, it tells the host it’s an agent acting for you, and
          that you’re verified.
        </p>
      </div>

      <ol className="card card-shadow numbered">
        {STEPS.map((s) => (
          <li key={s.n}>
            <span className="num" aria-hidden="true">
              {s.n}
            </span>
            <div className="stack-2">
              <span className="row-title">{s.title}</span>
              <span className="type-meta muted">{s.body}</span>
            </div>
          </li>
        ))}
      </ol>

      <div className="card card-line host-sees">
        <span className="type-status muted">What the host sees</span>
        <span className="type-meta">{disclosureFor(me.name)}</span>
      </div>

      <div className="grow" />

      {alreadyVerified ? (
        <div className="stack-10">
          <p className="type-fine muted center" role="status">
            You’re verified{me.verified_at ? ` since ${shortDate(me.verified_at)}` : ''}. Every RSVP carries it.
          </p>
          <button type="button" className="btn btn-primary" onClick={back}>
            Done
          </button>
        </div>
      ) : (
      <div className="stack-10">
        {forRsvp && !waiting && (
          <p className="type-fine muted center">Verify once, and the agent goes straight on to your RSVP.</p>
        )}
        {waiting ? (
          <>
            <button type="button" className="btn btn-primary" disabled aria-describedby="verify-wait">
              {personaUrl ? 'Waiting for Persona…' : 'Checking with Persona…'}
            </button>
            {blocked && personaUrl && (
              <a className="btn btn-quiet" href={personaUrl} target="_blank" rel="noopener noreferrer">
                Open Persona
              </a>
            )}
            <p id="verify-wait" className="type-fine muted center" role="status">
              {personaUrl
                ? 'Finish in the Persona tab. This page moves on by itself when you pass.'
                : 'This page moves on by itself when you pass.'}
            </p>
          </>
        ) : (
          <>
            <button type="button" className="btn btn-primary" onClick={begin} disabled={phase === 'starting'}>
              {phase === 'starting' ? 'Opening Persona…' : 'Verify with Persona'}
            </button>
            <button type="button" className="btn btn-quiet" onClick={onSkip}>
              Not now
            </button>
            {error ? (
              <p className="type-fine danger center" role="alert">
                {error}
              </p>
            ) : (
              <p className="type-fine muted center">Takes about a minute. Persona keeps your ID; we keep only that you passed.</p>
            )}
          </>
        )}
      </div>
      )}
    </main>
  )
}
