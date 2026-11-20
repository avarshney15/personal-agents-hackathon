// What the agent did (design/screens/Activity.dc.html): today's spend and every action.
import { useNav } from '../nav'
import { getActivity } from './data'
import { usd } from './format'
import type { ActivityItem } from './types'
import { ErrorLine, Loading, RESULT_TONE, StatusPill, useLoad } from './ui'
import './features.css'

function Row({ item, onOpen }: { item: ActivityItem; onOpen?: () => void }) {
  const body = (
    <>
      <span className="ft-row-text">
        <span className="ft-row-title ft-row-title--15">{item.what}</span>
        <span className="ft-row-sub">{item.where}</span>
      </span>
      <StatusPill tone={RESULT_TONE[item.tone] ?? 'going'}>{item.result}</StatusPill>
    </>
  )
  return (
    <li className="ft-li">
      {onOpen ? (
        <button type="button" className="ft-row" onClick={onOpen}>
          {body}
        </button>
      ) : (
        <div className="ft-row">{body}</div>
      )}
    </li>
  )
}

export function ActivityScreen() {
  const { navigate } = useNav()
  const { load, reload } = useLoad(getActivity)

  const spend = load.state === 'ready' ? load.data.spend : null
  const share = spend && spend.limit_usd > 0 ? Math.min(1, spend.spent_usd / spend.limit_usd) : 0

  return (
    <main className="screen ft-screen" aria-labelledby="ft-activity-title" aria-busy={load.state === 'loading'}>
      <div className="ft-head">
        <h1 id="ft-activity-title" className="ft-title">
          What the agent did
        </h1>
        <p className="ft-sub">Every action, on which site, and how it went. Tap one to see each step.</p>
      </div>

      {load.state === 'loading' && <Loading label="Loading what the agent did" />}
      {load.state === 'error' && <ErrorLine message={load.message} onRetry={reload} />}

      {spend && (
        <section className="ft-card ft-card--pad" aria-labelledby="ft-limit-title">
          <div className="ft-spend-head">
            <h2 id="ft-limit-title" className="ft-spend-title">
              Today’s limit
            </h2>
            <span className="ft-spend-value">
              {usd(spend.spent_usd)} of {usd(spend.limit_usd)}
            </span>
          </div>
          <div
            className="ft-bar"
            role="meter"
            aria-label="Spent today"
            aria-valuemin={0}
            aria-valuemax={spend.limit_usd}
            aria-valuenow={spend.spent_usd}
            aria-valuetext={`${usd(spend.spent_usd)} of ${usd(spend.limit_usd)}`}
          >
            <div style={{ width: `${Math.round(share * 100)}%` }} />
          </div>
          <p className="ft-fine">The agent stops and asks before going over.</p>
        </section>
      )}

      {load.state === 'ready' && load.data.items.length === 0 && (
        <div className="ft-empty">
          <span className="ft-row-title">Nothing yet today</span>
          <span className="ft-row-sub">When the agent reads your sites or RSVPs for you, each action shows here.</span>
        </div>
      )}

      {load.state === 'ready' && load.data.items.length > 0 && (
        <ul className="ft-card ft-card--list ft-list" aria-label="Actions">
          {load.data.items.map((item) => (
            <Row
              key={item.id}
              item={item}
              onOpen={item.run_id ? () => navigate({ name: 'rsvp', runId: item.run_id! }) : undefined}
            />
          ))}
        </ul>
      )}
    </main>
  )
}
