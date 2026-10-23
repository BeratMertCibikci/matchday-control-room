import { momentLabel, t } from '../i18n'
import type { CrewOutput, Lang, Meta, Persona } from '../types'

export function activeOverlay(crew: CrewOutput[], clock: number): CrewOutput | null {
  for (let i = crew.length - 1; i >= 0; i--) {
    const o = crew[i].overlay
    if (!o || o.kind === 'player_tag') continue
    if (clock >= o.start_t - 1 && clock <= o.end_t) return crew[i]
    if (clock > o.end_t) return null
  }
  return null
}

export function OverlayCard({ c, meta, lang, persona, label }:
  { c: CrewOutput; meta: Meta; lang: Lang; persona: Persona; label?: string }) {
  const kind = c.overlay?.kind ?? 'lower_third'
  const team = c.moment.team === meta.home.id ? meta.home : c.moment.team === meta.away.id ? meta.away : null
  if (kind === 'recap_panel' && c.recap) {
    const r = c.recap[lang]
    return (
      <div className="ov ov-recap">
        {label && <div className="ov-label">{label}</div>}
        <div className="ov-kicker">{c.moment.type === 'half_time' ? t('halfTime', lang) : t('fullTime', lang)}</div>
        <h2>{r.headline}</h2>
        <p>{r.summary}</p>
        <ul>{r.bullets.map((b) => <li key={b}>{b}</li>)}</ul>
      </div>
    )
  }
  const ev = c.moment.evidence[0]
  return (
    <div className={`ov ov-${kind}`} style={{ borderLeftColor: team?.color ?? '#888' }}>
      {label && <div className="ov-label">{label}</div>}
      <div className="ov-kicker">{c.moment.minute}' · {momentLabel(c.moment.type, lang)}</div>
      <div className="ov-head">{c.narration[lang]}</div>
      <div className="ov-why"><b>{t('whyMatters', lang)}:</b> {c.cards[persona][lang]}</div>
      {kind === 'stat_card' && ev && persona === 'analyst' && (
        <div className="ov-metric">
          <span>{ev.metric}</span>
          <div className="ov-metric-bars">
            {ev.baseline !== undefined && (
              <div className="base" style={{ width: `${Math.min(100, (ev.baseline / Math.max(ev.value, ev.baseline)) * 100)}%` }}>
                {ev.baseline}
              </div>
            )}
            <div className="now" style={{ width: `${ev.baseline !== undefined ? 100 : Math.min(100, Math.abs(ev.value))}%`, background: team?.color }}>
              {ev.value}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
