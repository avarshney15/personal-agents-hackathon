// Sign in with email (design/screens/Welcome.dc.html), then the six-digit code.
import { useContext, useEffect, useRef, useState, type FormEvent } from 'react'
import { apiMode } from '../api'
import { AppContext } from '../app-state'
import { useNav } from '../nav'
import { Icon } from '../ui'
import { authStart, authVerify } from './data'
import { failText } from './fail'
import { SAMPLE_EMAIL } from './samples'
import './features.css'

type Step = 'email' | 'code'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function WelcomeScreen() {
  const { navigate } = useNav()
  const app = useContext(AppContext)
  const [step, setStep] = useState<Step>('email')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [devCode, setDevCode] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [resent, setResent] = useState(false)
  const codeRef = useRef<HTMLInputElement>(null)

  // Mock mode shows the canvas's sample address, as drawn.
  useEffect(() => {
    let live = true
    apiMode().then((mode) => {
      if (live && mode === 'mock') setEmail((e) => e || SAMPLE_EMAIL)
    })
    return () => {
      live = false
    }
  }, [])

  useEffect(() => {
    if (step === 'code') codeRef.current?.focus()
  }, [step])

  async function sendCode(e?: FormEvent) {
    e?.preventDefault()
    const addr = email.trim()
    if (!EMAIL_RE.test(addr)) {
      setError('Enter your email address, like name@example.com.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const res = await authStart(addr)
      setDevCode(res.dev_code ?? null)
      if (res.dev_code) setCode(res.dev_code)
      if (step === 'code') setResent(true)
      setStep('code')
    } catch (err) {
      setError(failText(err, 'Couldn’t send the code. Try again.'))
    } finally {
      setBusy(false)
    }
  }

  async function verify(e?: FormEvent) {
    e?.preventDefault()
    const digits = code.replace(/\D/g, '')
    if (digits.length !== 6) {
      setError('Enter all six digits.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const user = await authVerify(email.trim(), digits)
      app?.setMe(user)
      navigate(user.verified ? { name: 'connect' } : { name: 'verify' })
    } catch (err) {
      setError(failText(err, 'Couldn’t sign you in. Try again.'))
      setBusy(false)
    }
  }

  if (step === 'code') {
    return (
      <main className="screen ft-screen ft-screen--welcome" aria-labelledby="ft-code-title">
        <span className="ft-logo" aria-hidden="true">
          <Icon name="check" size={30} stroke={2.4} />
        </span>
        <div className="ft-head ft-head--10">
          <h1 id="ft-code-title" className="ft-title">
            Enter the code we emailed you
          </h1>
          <p className="ft-sub">
            We sent a 6-digit code to <strong className="ft-strong">{email.trim()}</strong>. Type it here to sign in.
          </p>
        </div>

        <form className="ft-form" onSubmit={verify} noValidate>
          <label htmlFor="ft-code" className="ft-label">
            Six-digit code
          </label>
          <input
            ref={codeRef}
            id="ft-code"
            className="ft-field ft-field--code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={7}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/[^\d ]/g, ''))}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'ft-code-error' : devCode ? 'ft-code-dev' : undefined}
          />
          {devCode && !error && (
            <p id="ft-code-dev" className="ft-fine">
              Dev mode: the server sent {devCode} and it’s filled in.
            </p>
          )}
          {error && (
            <p id="ft-code-error" className="ft-form-error" role="alert">
              {error}
            </p>
          )}
          {resent && !error && (
            <p className="ft-fine" role="status">
              New code sent.
            </p>
          )}
        </form>

        <div className="ft-grow" />

        <div className="ft-form">
          <button type="button" className="ft-primary" onClick={() => verify()} disabled={busy}>
            {busy ? 'Checking' : 'Sign in'}
          </button>
          <div className="ft-form-row">
            <button
              type="button"
              className="ft-link"
              onClick={() => {
                setStep('email')
                setCode('')
                setError('')
                setResent(false)
              }}
            >
              Use a different email
            </button>
            <button type="button" className="ft-link" onClick={() => sendCode()} disabled={busy}>
              Send a new code
            </button>
          </div>
        </div>
      </main>
    )
  }

  return (
    <main className="screen ft-screen ft-screen--welcome" aria-labelledby="ft-welcome-title">
      <span className="ft-logo" aria-hidden="true">
        <Icon name="check" size={30} stroke={2.4} />
      </span>
      <div className="ft-head ft-head--10">
        <h1 id="ft-welcome-title" className="ft-title">
          Every event you’re going to, and an agent that gets you in
        </h1>
        <p className="ft-sub">
          Luma, Meetup, Eventbrite and Partiful in one list. Tap RSVP and the agent registers you on the site, tells the
          host it’s acting for you, and checks it worked.
        </p>
      </div>
      <ul className="ft-bullets">
        <li>Remembers what you liked, and says why it picked each event</li>
        <li>Works while you’re offline, on your own logged-in accounts</li>
        <li>Comes back only for a code, a payment or a choice</li>
      </ul>

      <div className="ft-grow" />

      <form className="ft-form" onSubmit={sendCode} noValidate>
        <label htmlFor="ft-email" className="ft-label">
          Email
        </label>
        <input
          id="ft-email"
          className="ft-field"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'ft-email-error' : 'ft-email-note'}
        />
        {error && (
          <p id="ft-email-error" className="ft-form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="ft-primary" disabled={busy}>
          {busy ? 'Sending' : 'Email me a code'}
        </button>
        <p id="ft-email-note" className="ft-fine ft-center">
          No password. We send a 6-digit code.
        </p>
      </form>
    </main>
  )
}
