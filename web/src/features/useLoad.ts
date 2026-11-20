// Loads data for a screen once, with reload and in-place updates.
import { useCallback, useEffect, useState } from 'react'
import { errorText } from '../errors'

export type Load<T> =
  | { state: 'loading' }
  | { state: 'error'; message: string }
  | { state: 'ready'; data: T }

/** Runs `fn` (a stable data call) on mount; `reload` runs it again; `set` replaces the data. */
export function useLoad<T>(fn: () => Promise<T>) {
  const [load, setLoad] = useState<Load<T>>({ state: 'loading' })
  const [call] = useState(() => fn)

  const reload = useCallback(() => {
    let live = true
    call().then(
      (data) => live && setLoad({ state: 'ready', data }),
      (err) => live && setLoad({ state: 'error', message: errorText(err) }),
    )
    return () => {
      live = false
    }
  }, [call])

  useEffect(() => reload(), [reload])

  const set = useCallback((data: T) => setLoad({ state: 'ready', data }), [])
  return { load, reload, set }
}
