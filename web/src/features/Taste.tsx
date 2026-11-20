// What are you into right now? (design/screens/Taste.dc.html)
import { useEffect, useState } from 'react'
import { apiMode } from '../api'
import { errorText } from '../errors'
import { useNav } from '../nav'
import { getMe, saveTaste } from './data'
import { SAMPLE_CHIPS, SAMPLE_TASTE, SAMPLE_TIMES } from './samples'
import { ErrorLine } from './ui'
import './features.css'

const CHIPS = ['AI agents', 'Climbing', 'Hackathons', 'Design', 'Music', 'Founders', 'Running', 'Film', 'Food', 'Science talks']
const TIMES = ['Weekday evenings', 'Weekend days', 'Mornings']

function toggle(list: string[], item: string): string[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item]
}

function ToggleGroup({ label, id, options, on, onToggle }: {
  label: string
  id: string
  options: string[]
  on: string[]
  onToggle: (o: string) => void
}) {
  return (
    <div className="ft-stack" role="group" aria-labelledby={id}>
      <span id={id} className="ft-label">
        {label}
      </span>
      <div className="ft-chips">
        {options.map((o) => (
          <button key={o} type="button" className="ft-chip" aria-pressed={on.includes(o)} onClick={() => onToggle(o)}>
            {o}
          </button>
        ))}
      </div>
    </div>
  )
}

export function TasteScreen() {
  const { navigate } = useNav()
  const [text, setText] = useState('')
  const [chips, setChips] = useState<string[]>([])
  const [times, setTimes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Start from what the agent already knows; mock mode starts from the canvas sample.
  useEffect(() => {
    let live = true
    ;(async () => {
      const mock = (await apiMode()) === 'mock'
      if (mock && live) {
        setText((t) => t || SAMPLE_TASTE)
        setChips((c) => (c.length ? c : SAMPLE_CHIPS))
        setTimes((t) => (t.length ? t : SAMPLE_TIMES))
        return
      }
      try {
        const me = await getMe()
        if (!live) return
        setChips((c) => (c.length ? c : (me.into ?? []).filter((x) => CHIPS.includes(x))))
        setTimes((t) => (t.length ? t : (me.usual_times ?? []).filter((x) => TIMES.includes(x))))
      } catch {
        // Signed out or no backend: start empty.
      }
    })()
    return () => {
      live = false
    }
  }, [])

  async function submit() {
    setBusy(true)
    setError('')
    try {
      await saveTaste({ text: text.trim(), chips, usual_times: times })
      navigate({ name: 'week' })
    } catch (err) {
      setError(errorText(err))
      setBusy(false)
    }
  }

  return (
    <main className="screen ft-screen ft-screen--gap18" aria-labelledby="ft-taste-title">
      <div className="ft-head">
        <h1 id="ft-taste-title" className="ft-title">
          What are you into right now?
        </h1>
        <p className="ft-sub">Say it your way. The agent remembers it, and you can change or forget it any time.</p>
      </div>

      <div className="ft-stack">
        <label htmlFor="ft-into" className="ft-label">
          In your words
        </label>
        <textarea
          id="ft-into"
          className="ft-field"
          rows={3}
          placeholder={SAMPLE_TASTE}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </div>

      <ToggleGroup label="Or tap a few" id="ft-chips-label" options={CHIPS} on={chips} onToggle={(c) => setChips(toggle(chips, c))} />
      <ToggleGroup
        label="When you usually go"
        id="ft-times-label"
        options={TIMES}
        on={times}
        onToggle={(t) => setTimes(toggle(times, t))}
      />

      {error && <ErrorLine message={error} />}

      <div className="ft-grow" />
      <button type="button" className="ft-primary" onClick={submit} disabled={busy} aria-describedby="ft-taste-note">
        {busy ? 'Saving' : 'Show my week'}
      </button>
      <p id="ft-taste-note" className="ft-fine ft-center">
        It also learns from what you go to and skip.
      </p>
    </main>
  )
}
