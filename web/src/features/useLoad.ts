// Loads data for a screen once, with reload and in-place updates.
import { useCallback, useEffect, useState } from 'react'
import { failText } from './fail'

export type Load<T> =
  | { state: 'loading' }
  | { state: 'error'; message: string }
  | { state: 'ready'; data: T }

/** Runs `fn` (a stable data call) on mount; `reload` runs it again; `set` replaces the data.
 *  On failure the screen shows `failMessage` (or the server's own detail for a 4xx). */
export function useLoad<T>(fn: () => Promise<T>, failMessage: string) {
  const [load, setLoad] = useState<Load<T>>({ state: 'loading' })
  const [call] = useState(() => fn)

  const run = useCallback(() => {
    let live = true
    call().then(
      (data) => live && setLoad({ state: 'ready', data }),
      (err) => live && setLoad({ state: 'error', message: failText(err, failMessage) }),
    )
    return () => {
      live = false
    }
  }, [call, failMessage])

  useEffect(() => run(), [run])

  // Try again: show the loading line, then run once more.
  const reload = useCallback(() => {
    setLoad({ state: 'loading' })
    run()
  }, [run])

  const set = useCallback((data: T) => setLoad({ state: 'ready', data }), [])
  return { load, reload, set }
}
