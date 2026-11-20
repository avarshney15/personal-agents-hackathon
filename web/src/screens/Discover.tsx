// For you (design/screens/Discover.dc.html): "Into right now" chips and the ranked picks.
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { addInto, getFeed, removeInto } from '../api'
import { lastFeed, saveFeed } from '../app-state'
import { errorText } from '../errors'
import { place, whenShort } from '../format'
import { useNav } from '../nav'
import type { FeedResponse, Pick } from '../types'
import { Icon, Pill, RoundButton, Tile, tagTone, useFlip } from '../ui'

export function DiscoverScreen() {
  const { navigate } = useNav()
  const [feed, setFeed] = useState<FeedResponse | null>(lastFeed())
  const [loadError, setLoadError] = useState('')
  const [adding, setAdding] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [chipError, setChipError] = useState('')
  const [announce, setAnnounce] = useState('')
  const listRef = useRef<HTMLUListElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const addRef = useRef<HTMLButtonElement>(null)

  const apply = (f: FeedResponse) => {
    saveFeed(f)
    setFeed(f)
  }

  const load = () => {
    setLoadError('')
    getFeed()
      .then(apply)
      .catch((err) => setLoadError(errorText(err)))
  }
  useEffect(load, [])

  useEffect(() => {
    if (adding) inputRef.current?.focus()
  }, [adding])

  useFlip(listRef, feed?.picks.map((p) => p.event.id).join('|') ?? '')

  async function submit(e: FormEvent) {
    e.preventDefault()
    const value = text.trim()
    if (!value || busy) return
    setBusy(true)
    setChipError('')
    try {
      const f = await addInto(value)
      apply(f)
      setText('')
      setAdding(false)
      setAnnounce(`Reordered for ${f.into.join(', ')}.`)
      requestAnimationFrame(() => addRef.current?.focus())
    } catch (err) {
      setChipError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function remove(tag: string) {
    setChipError('')
    try {
      const f = await removeInto(tag)
      apply(f)
      setAnnounce(`Removed ${tag}. Picks reordered.`)
      requestAnimationFrame(() => addRef.current?.focus())
    } catch (err) {
      setChipError(errorText(err))
    }
  }

  const open = (p: Pick) => navigate({ name: 'event', id: p.event.id })

  return (
    <main className="screen discover" aria-labelledby="discover-title">
      <div className="title-row">
        <h1 id="discover-title" className="type-title">
          For you
        </h1>
        <RoundButton label="Ask the agent" icon="search" onClick={() => navigate({ name: 'ask' })} />
      </div>

      <section className="card card-line into" aria-labelledby="into-label">
        <span id="into-label" className="type-fine muted">
          Into right now
        </span>
        <div className="chips">
          {feed?.into.map((tag) => (
            <button key={tag} type="button" className="chip chip-on" aria-label={`${tag}. Remove`} onClick={() => remove(tag)}>
              {tag}
              <Icon name="close" size={14} stroke={2.2} className="chip-x" />
            </button>
          ))}
          {!adding && (
            <button ref={addRef} type="button" className="chip chip-quiet" onClick={() => setAdding(true)}>
              Add
            </button>
          )}
        </div>
        {adding && (
          <form className="add-form" onSubmit={submit}>
            <label htmlFor="into-input" className="sr-only">
              What are you into right now?
            </label>
            <input
              id="into-input"
              ref={inputRef}
              className="field"
              type="text"
              autoComplete="off"
              enterKeyHint="done"
              placeholder="Climbing and AI agents lately"
              value={text}
              disabled={busy}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  setAdding(false)
                  requestAnimationFrame(() => addRef.current?.focus())
                }
              }}
            />
            <button type="submit" className="chip chip-on" disabled={busy || !text.trim()}>
              {busy ? 'Adding…' : 'Add'}
            </button>
          </form>
        )}
        {chipError && (
          <p className="type-fine danger" role="alert">
            {chipError}
          </p>
        )}
      </section>

      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>

      {loadError && !feed && (
        <div className="card card-line stack-8" role="alert">
          <span className="type-meta">{loadError}</span>
          <button type="button" className="btn btn-quiet" onClick={load}>
            Try again
          </button>
        </div>
      )}

      {!feed && !loadError && <PickSkeleton />}

      {feed && feed.picks.length === 0 && (
        <p className="type-meta muted">Nothing for you yet. Tell the agent what you’re into, and it starts looking.</p>
      )}

      {feed && feed.picks.length > 0 && (
        <ul ref={listRef} className="picks" aria-label="Picks for you">
          {feed.picks.map((p) => (
            <li key={p.event.id} data-flip={p.event.id}>
              <button type="button" className="pick" onClick={() => open(p)}>
                <Tile id={p.event.id} title={p.event.title} />
                <span className="pick-body">
                  <span>
                    <Pill tone={tagTone(p.tag)} small>
                      {p.tag}
                    </Pill>
                  </span>
                  <span className="type-event-title">{p.event.title}</span>
                  <span className="type-meta muted">
                    {whenShort(p.event.starts_at)}
                    {place(p.event) && ` · ${place(p.event)}`}
                  </span>
                  <span className="type-fine">{p.why}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {feed && (
        <p className="type-fine muted">
          Ranked by what you’ve been to and skipped.
          {feed.arm === 'sie' && ' You’re in the new ranking this week.'}
        </p>
      )}
    </main>
  )
}

function PickSkeleton() {
  return (
    <ul className="picks" aria-busy="true" aria-label="Loading picks">
      {[0, 1, 2, 3].map((i) => (
        <li key={i} className="pick skeleton">
          <span className="tile sk" />
          <span className="pick-body">
            <span className="sk sk-line" style={{ width: 84 }} />
            <span className="sk sk-line" style={{ width: '70%' }} />
            <span className="sk sk-line" style={{ width: '45%' }} />
          </span>
        </li>
      ))}
    </ul>
  )
}
