// Team dashboard at /ops (design/screens/Ops.dc.html): a wide desktop page, not the phone frame.
import { getOps } from './data'
import { pct, usd } from './format'
import type { ArmStat, OpsSummary } from './types'
import { ErrorLine, Loading, StatusPill } from './ui'
import { useLoad } from './useLoad'
import './features.css'

function tiles(o: OpsSummary) {
  return [
    { label: 'Agent runs today', value: String(o.runs_today), note: 'RSVPs, cancels, waitlists and reads' },
    { label: 'Confirmed on the site', value: pct(o.confirmed_pct), note: 'Status read back after each action' },
    { label: 'Handed to a person', value: String(o.handed_to_person), note: 'Codes, payments, human checks' },
    { label: 'Cost per RSVP', value: usd(o.cost_per_rsvp_usd), note: 'Under each person’s daily limit' },
  ]
}

/** New arm first, as drawn. Bars share one scale, at least 25%, so 18% fills 72%. */
function armRows(arms: ArmStat[]) {
  const sorted = [...arms].sort((a, b) => (a.arm === b.arm ? 0 : a.arm === 'sie' ? -1 : 1))
  const scale = Math.max(0.25, ...sorted.map((a) => a.rsvp_rate))
  return sorted.map((a) => ({ ...a, width: `${Math.round((a.rsvp_rate / scale) * 100)}%` }))
}

export function OpsScreen() {
  const { load, reload } = useLoad(getOps, 'Couldn’t load the dashboard.')

  return (
    <main className="ft-ops" aria-labelledby="ft-ops-title" aria-busy={load.state === 'loading'}>
      <div className="ft-ops-inner">
        <header className="ft-ops-head">
          <div className="ft-head">
            <h1 id="ft-ops-title" className="ft-title">
              How the agent is doing
            </h1>
            <p className="ft-sub">
              For the team, not the app. Runs traced in Amplitude, ranking tested in Statsig, rehearsals on simulated
              sites.
            </p>
          </div>
          {load.state === 'ready' && load.data.sample && <span className="ft-sample">Sample data</span>}
        </header>

        {load.state === 'loading' && <Loading label="Loading the dashboard" />}
        {load.state === 'error' && <ErrorLine message={load.message} onRetry={reload} />}

        {load.state === 'ready' && (
          <>
            <dl className="ft-tiles">
              {tiles(load.data).map((t) => (
                <div key={t.label} className="ft-stat">
                  <dt>{t.label}</dt>
                  <dd>
                    <span className="ft-stat-value">{t.value}</span>
                    <span className="ft-stat-note">{t.note}</span>
                  </dd>
                </div>
              ))}
            </dl>

            <div className="ft-ops-row">
              <section className="ft-panel ft-panel--arms" aria-labelledby="ft-arms-title">
                <div className="ft-panel-head">
                  <h2 id="ft-arms-title" className="ft-h3">
                    Ranking experiment
                  </h2>
                  <span className="ft-fine">Share of For you picks people RSVP to</span>
                </div>
                {armRows(load.data.arms).map((a) => (
                  <div key={a.arm} className="ft-arm">
                    <div className="ft-arm-head">
                      <span>{a.label}</span>
                      <span>{pct(a.rsvp_rate * 100)}</span>
                    </div>
                    <div
                      className={`ft-arm-bar${a.arm === 'sie' ? '' : ' ft-arm-bar--old'}`}
                      role="img"
                      aria-label={`${a.label}: ${pct(a.rsvp_rate * 100)} of picks RSVPed to`}
                    >
                      <div style={{ width: a.width }} />
                    </div>
                    <span className="ft-fine">{a.how}</span>
                  </div>
                ))}
              </section>

              <section className="ft-panel ft-panel--sims" aria-labelledby="ft-sims-title">
                <h2 id="ft-sims-title" className="ft-h3">
                  Rehearsals on simulated sites
                </h2>
                <ul className="ft-sims">
                  {load.data.rehearsals.map((r) => (
                    <li key={r.name} className={`ft-sim${r.ok ? '' : ' ft-sim--bad'}`}>
                      <span>{r.name}</span>
                      <span>{r.value}</span>
                    </li>
                  ))}
                </ul>
              </section>
            </div>

            <div className="ft-table-wrap" role="region" aria-labelledby="ft-runs-caption" tabIndex={0}>
              <table className="ft-table">
                <caption id="ft-runs-caption" className="ft-sr">
                  Recent agent runs
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Run</th>
                    <th scope="col">Site</th>
                    <th scope="col">Steps</th>
                    <th scope="col">Model</th>
                    <th scope="col">Cost</th>
                    <th scope="col">Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {load.data.recent.length === 0 && (
                    <tr>
                      <td colSpan={6} className="ft-fine">
                        No runs yet today.
                      </td>
                    </tr>
                  )}
                  {load.data.recent.map((r, i) => (
                    <tr key={`${r.run}-${i}`}>
                      <td>{r.run}</td>
                      <td>{r.site}</td>
                      <td className="ft-num">{r.steps}</td>
                      <td>{r.model}</td>
                      <td className="ft-num">{usd(r.cost_usd)}</td>
                      <td>
                        <StatusPill tone={r.ok ? 'going' : 'needs'}>{r.outcome}</StatusPill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </main>
  )
}
