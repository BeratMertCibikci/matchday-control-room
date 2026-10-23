import { momentLabel, t } from '../i18n'
import type { CrewOutput, Lang, Meta, Mode, Persona, PlayerStats, Snapshot } from '../types'

export function personaFor(mode: Mode, club: string | null, meta: Meta): Persona {
  if (mode === 'analyst') return 'analyst'
  if (mode === 'fan') return club === meta.away.id ? 'fan_away' : 'fan_home'
  return 'casual'
}

export function Scorebug({ meta, snap, lang }: { meta: Meta; snap: Snapshot | null; lang: Lang }) {
  const h = snap?.score[meta.home.id] ?? 0
  const a = snap?.score[meta.away.id] ?? 0
  const minute = snap?.minute ?? 1
  const label = snap?.finished ? t('fullTime', lang) : `${minute}'`
  return (
    <div className="scorebug">
      <span className="team" style={{ borderColor: meta.home.color }}>{meta.home.short}</span>
      <span className="score">{h} - {a}</span>
      <span className="team" style={{ borderColor: meta.away.color }}>{meta.away.short}</span>
      <span className="clock">{label}</span>
    </div>
  )
}

function Bar({ left, right, lc, rc, label, fmt = (v: number) => String(v) }:
  { left: number; right: number; lc: string; rc: string; label: string; fmt?: (v: number) => string }) {
  const total = left + right || 1
  return (
    <div className="bar">
      <div className="bar-label"><span>{fmt(left)}</span><span>{label}</span><span>{fmt(right)}</span></div>
      <div className="bar-track">
        <div style={{ width: `${(100 * left) / total}%`, background: lc }} />
        <div style={{ width: `${(100 * right) / total}%`, background: rc }} />
      </div>
    </div>
  )
}

function Sparkline({ series, meta }: { series: { minute: number; value: number }[]; meta: Meta }) {
  if (series.length < 2) return <svg className="spark" viewBox="0 0 100 40" />
  const maxM = Math.max(95, series[series.length - 1].minute)
  const pts = series.map((p) => `${(100 * p.minute) / maxM},${20 - (p.value / 100) * 18}`).join(' ')
  return (
    <svg className="spark" viewBox="0 0 100 40" preserveAspectRatio="none">
      <rect x={0} y={0} width={100} height={20} fill={meta.home.color} opacity={0.08} />
      <rect x={0} y={20} width={100} height={20} fill={meta.away.color} opacity={0.08} />
      <line x1={0} y1={20} x2={100} y2={20} className="axis" />
      <polyline points={pts} className="spark-line" />
    </svg>
  )
}

export function StatsPanel({ meta, snap, lang, mode }: { meta: Meta; snap: Snapshot | null; lang: Lang; mode: Mode }) {
  if (!snap) return <div className="panel stats" />
  const H = snap.teams[meta.home.id]
  const A = snap.teams[meta.away.id]
  const hc = meta.home.color
  const ac = meta.away.color
  const cc = snap.control_chaos
  const rich = mode === 'analyst'
  return (
    <div className="panel stats">
      <Bar left={snap.possession[meta.home.id]} right={snap.possession[meta.away.id]} lc={hc} rc={ac}
           label={t('possession', lang)} fmt={(v) => `${Math.round(v)}%`} />
      <Bar left={H.shots} right={A.shots} lc={hc} rc={ac} label={t('shots', lang)} />
      {rich && <Bar left={H.xg} right={A.xg} lc={hc} rc={ac} label="xG" fmt={(v) => v.toFixed(2)} />}
      {rich && <Bar left={H.shots_on_target} right={A.shots_on_target} lc={hc} rc={ac} label={t('onTarget', lang)} />}
      {rich && <Bar left={H.pass_accuracy} right={A.pass_accuracy} lc={hc} rc={ac} label={t('passAcc', lang)}
                    fmt={(v) => `${Math.round(v)}%`} />}
      {rich && <Bar left={snap.pressure_rate[meta.home.id]} right={snap.pressure_rate[meta.away.id]} lc={hc} rc={ac}
                    label={t('pressRate', lang)} fmt={(v) => v.toFixed(1)} />}
      <div className="sub-title">{t('momentum', lang)}</div>
      <Sparkline series={snap.momentum_series} meta={meta} />
      <div className="sub-title">{t('controlChaos', lang)} · <b className={`cc-${cc.label}`}>{t(cc.label, lang)}</b></div>
      <div className="cc-track"><div className="cc-dot" style={{ left: `${cc.index}%` }} /></div>
      {rich && (
        <div className="cc-parts">
          <span>{t('turnovers', lang)}: <b>{cc.turnovers_per_min}</b></span>
          <span>{t('longShare', lang)}: <b>{cc.long_pass_share}%</b></span>
          <span>{t('chain', lang)}: <b>{cc.avg_chain_passes}</b></span>
        </div>
      )}
    </div>
  )
}

export function PlayerPanel({ p, lang, color }: { p: PlayerStats | undefined; lang: Lang; color: string }) {
  if (!p) return null
  const rows: [string, string | number][] = [
    [t('goals', lang), p.goals], [t('shots', lang), p.shots], ['xG', p.xg.toFixed(2)],
    [t('keyPasses', lang), p.key_passes], [t('passAcc', lang), `${p.pass_accuracy}%`],
    [t('passDiff', lang), p.avg_pass_difficulty], [t('touches', lang), p.touches],
    [t('distance', lang), `${p.distance_km} km`], [t('topSpeed', lang), `${p.top_speed_kmh} km/h`],
  ]
  return (
    <div className="panel player-panel" style={{ borderTopColor: color }}>
      <div className="player-name">#{p.number} {p.name} <small>{p.role}</small></div>
      <div className="player-grid">
        {rows.map(([k, v]) => <div key={k}><span>{k}</span><b>{v}</b></div>)}
      </div>
    </div>
  )
}

export function Feed({ crew, lang, persona, favorite }: { crew: CrewOutput[]; lang: Lang; persona: Persona; favorite: string | null }) {
  const items = crew.filter((c) => c.moment.importance >= 0.5 || c.moment.player === favorite).slice(-14).reverse()
  return (
    <div className="panel feed">
      {items.map((c) => (
        <div key={c.moment.id} className={`feed-item ${c.moment.player && c.moment.player === favorite ? 'fav' : ''} ${c.moment.type}`}>
          <span className="feed-min">{c.moment.minute}'</span>
          <div>
            <div className="feed-line">{c.narration[lang]}</div>
            {c.moment.importance >= 0.5 && <div className="feed-why">{c.cards[persona][lang]}</div>}
          </div>
        </div>
      ))}
    </div>
  )
}

const STATUS_CLASS: Record<string, string> = {
  ok: 'ok', retried: 'warn', fallback: 'bad', show: 'ok', replace: 'warn', drop: 'muted', ticker: 'muted', skipped: 'muted',
}

export function TracePanel({ crew, lang }: { crew: CrewOutput[]; lang: Lang }) {
  const rows = crew.filter((c) => c.moment.importance >= 0.5).slice(-6).reverse()
  return (
    <div className="panel trace">
      {rows.map((c) => (
        <div key={c.moment.id} className="trace-row">
          <div className="trace-head">{c.moment.minute}' {momentLabel(c.moment.type, lang)}</div>
          <div className="trace-steps">
            {c.trace.map((s, i) => (
              <span key={i} className={`step ${STATUS_CLASS[s.status] ?? ''}`} title={s.note}>
                {s.agent}{s.language ? `·${s.language}` : ''} <em>{s.status}</em>
                {s.latency_ms > 0 && <small> {s.latency_ms}ms</small>}
              </span>
            ))}
          </div>
          {c.decision.action === 'drop' && <div className="trace-note">{c.decision.reason}</div>}
        </div>
      ))}
    </div>
  )
}
