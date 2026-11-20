// What the agent remembers (design/screens/Memory.dc.html).
import { useEffect, useRef, useState } from 'react'
import { forgetEverything, forgetFact, getMemory } from '../api'
import { errorText } from '../errors'
import type { Fact, FactGroups } from '../types'
import { BackButton } from '../ui'

const GROUPS: { key: keyof FactGroups; name: string }[] = [
  { key: 'said', name: 'You said' },
  { key: 'went', name: 'You went' },
  { key: 'skipped', name: 'You skipped' },
]

export function MemoryScreen() {
  const [groups, setGroups] = useState<FactGroups | null>(null)
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [announce, setAnnounce] = useState('')
  const confirmRef = useRef<HTMLButtonElement>(null)
  const titleRef = useRef<HTMLHeadingElement>(null)

  const load = () => {
    setError('')
    getMemory()
      .then(setGroups)
      .catch((err) => setError(errorText(err)))
  }
  useEffect(load, [])
  useEffect(() => {
    if (confirming) confirmRef.current?.focus()
  }, [confirming])

  async function forget(f: Fact) {
    try {
      setGroups(await forgetFact(f.id))
      setAnnounce(`Forgot: ${f.text}`)
      titleRef.current?.focus()
    } catch (err) {
      setError(errorText(err))
    }
  }

  async function forgetAll() {
    setBusy(true)
    try {
      setGroups(await forgetEverything())
      setConfirming(false)
      setAnnounce('The agent forgot everything about you.')
      titleRef.current?.focus()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const empty = groups && GROUPS.every((g) => groups[g.key].length === 0)

  return (
    <main className="screen memory" aria-labelledby="memory-title">
      <div className="top-left">
        <BackButton />
      </div>
      <div className="stack-8">
        <h1 id="memory-title" ref={titleRef} className="type-title" tabIndex={-1}>
          What the agent remembers
        </h1>
        <p className="type-sub muted">It uses these to pick events and explain why. Forget anything and it stops counting at once.</p>
      </div>

      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>

      {error && (
        <p className="type-fine danger" role="alert">
          {error}
        </p>
      )}

      {!groups && !error && <p className="type-meta muted">Loading…</p>}

      {groups &&
        GROUPS.map((g) => (
          <section key={g.key} className="stack-6" aria-labelledby={`group-${g.key}`}>
            <h2 id={`group-${g.key}`} className="group-label">
              {g.name}
            </h2>
            <ul className="card card-line list-card">
              {groups[g.key].length === 0 && (
                <li className="fact-row">
                  <span className="type-fine muted">Nothing here.</span>
                </li>
              )}
              {groups[g.key].map((f) => (
                <li key={f.id} className="fact-row">
                  <div className="stack-2 grow-x">
                    <span className="fact-text">{f.text}</span>
                    <span className="type-fine muted">{f.from_text}</span>
                  </div>
                  <button type="button" className="chip chip-quiet" aria-label={`Forget: ${f.text}`} onClick={() => forget(f)}>
                    Forget
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}

      <div className="grow" />

      {confirming ? (
        <div className="card card-shadow stack-10" role="group" aria-labelledby="confirm-title">
          <span id="confirm-title" className="row-title">
            Forget everything?
          </span>
          <span className="type-meta muted">
            The agent starts over, with no memory of what you said, went to or skipped. This can’t be undone.
          </span>
          <button ref={confirmRef} type="button" className="btn btn-danger" onClick={forgetAll} disabled={busy}>
            {busy ? 'Forgetting…' : 'Forget everything'}
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => setConfirming(false)} disabled={busy}>
            Keep it
          </button>
        </div>
      ) : (
        <button type="button" className="btn btn-plain-danger" onClick={() => setConfirming(true)} disabled={!!empty}>
          Forget everything
        </button>
      )}
    </main>
  )
}
