// Ask (design/screens/Ask.dc.html): a query in your words, the agent's answer, results.
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { apiMode } from '../api'
import { errorText } from '../errors'
import { useNav } from '../nav'
import { BackButton } from '../ui'
import { ask } from './data'
import { askMeta } from './format'
import { SAMPLE_QUERY } from './samples'
import type { AskResponse } from './types'
import { ErrorLine, SendIcon, StatusPill, TAG_TONE } from './ui'
import './features.css'

// The last answer, so coming back from an Event sheet shows it again.
let last: AskResponse | null = null

export function AskScreen() {
  const { navigate } = useNav()
  const [answer, setAnswer] = useState<AskResponse | null>(last)
  const [pending, setPending] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [ready, setReady] = useState(last !== null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function run(text: string) {
    const q = text.trim()
    if (!q) return
    setPending(q)
    setError('')
    try {
      const res = await ask(q)
      last = res
      setAnswer(res)
      setDraft('')
    } catch (err) {
      setError(errorText(err))
    } finally {
      setPending(null)
    }
  }

  // Mock mode opens on the canvas's sample question.
  useEffect(() => {
    if (last) return
    let live = true
    apiMode().then((mode) => {
      if (!live) return
      setReady(true)
      if (mode === 'mock') run(SAMPLE_QUERY)
    })
    return () => {
      live = false
    }
  }, [])

  function submit(e: FormEvent) {
    e.preventDefault()
    run(draft)
  }

  const shown = pending ?? answer?.query ?? null

  return (
    <main className="screen ft-screen ft-screen--ask" aria-labelledby="ft-ask-title">
      <div className="ft-backbar">
        <BackButton />
        <h1 id="ft-ask-title" className="ft-sheet-title">
          Ask
        </h1>
      </div>

      {shown && <p className="ft-bubble">{shown}</p>}

      <div aria-live="polite" className="ft-stack ft-stack--14">
        {pending && <p className="ft-loading">Looking across your sites</p>}
        {error && <ErrorLine message={error} onRetry={() => run(shown ?? draft)} />}

        {!pending && answer && (
          <>
            <div className="ft-answer">
              <p className="ft-answer-text">{answer.summary}</p>
              {answer.filters && <p className="ft-fine">{answer.filters}</p>}
            </div>
            <ul className="ft-results" aria-label="Results">
              {answer.picks.map((p) => (
                <li key={p.event.id}>
                  <button type="button" className="ft-result" onClick={() => navigate({ name: 'event', id: p.event.id })}>
                    <span className="ft-result-top">
                      <span className="ft-event-title">{p.event.title}</span>
                      <StatusPill tone={TAG_TONE[p.tag] ?? 'quiet'}>{p.tag}</StatusPill>
                    </span>
                    <span className="ft-event-meta">{askMeta(p.event)}</span>
                    <span className="ft-result-why">{p.why}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      {ready && !answer && !pending && (
        <div className="ft-stack">
          <p className="ft-sub">Ask for events in your own words. The agent searches your sites and says why each one fits.</p>
          <button type="button" className="ft-suggest" onClick={() => run(SAMPLE_QUERY)}>
            {SAMPLE_QUERY}
          </button>
        </div>
      )}

      <form className="ft-askbar" onSubmit={submit}>
        <label htmlFor="ft-ask" className="ft-sr">
          Ask the agent
        </label>
        <input
          ref={inputRef}
          id="ft-ask"
          className="ft-field"
          type="text"
          placeholder="Ask for anything"
          autoComplete="off"
          enterKeyHint="send"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" className="ft-send" aria-label="Send" disabled={pending !== null || !draft.trim()}>
          <SendIcon />
        </button>
      </form>
    </main>
  )
}
