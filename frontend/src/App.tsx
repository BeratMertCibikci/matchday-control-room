import { useEffect, useMemo, useRef, useState } from 'react'
import { Pitch } from './components/Pitch'
import { activeOverlay, OverlayCard } from './components/OverlayLayer'
import { Feed, personaFor, PlayerPanel, Scorebug, StatsPanel, TracePanel } from './components/Panels'
import { t } from './i18n'
import type { Lang, Mode } from './types'
import { useMatch } from './useMatch'
import './index.css'

const params = new URLSearchParams(location.search)
const SEED = Number(params.get('seed') ?? 5)
const START_SPEED = Number(params.get('speed') ?? 30)
const MODES: Mode[] = ['analyst', 'casual', 'fan', 'player']

export default function App() {
  const feed = useMatch(SEED, START_SPEED)
  const [lang, setLang] = useState<Lang>((params.get('lang') as Lang) ?? 'tr')
  const [mode, setMode] = useState<Mode>((params.get('mode') as Mode) ?? 'analyst')
  const [club, setClub] = useState<string | null>(null)
  const [favorite, setFavorite] = useState<string | null>(null)
  const [speed, setSpeed] = useState(START_SPEED)
  const [paused, setPaused] = useState(false)
  const [compare, setCompare] = useState(params.get('compare') === '1')
  const [voice, setVoice] = useState(false)
  const spoken = useRef(new Set<string>())

  const { meta, snapshot, crew, clock, frame, lastEvent } = feed
  useEffect(() => {
    if (meta && !club) setClub(meta.home.id)
    if (meta && !favorite) setFavorite(meta.away.players.find((p) => p.role === 'FW')?.id ?? null)
  }, [meta, club, favorite])

  // Browser text-to-speech for the narrator line (Azure AI Speech can replace this server-side).
  useEffect(() => {
    if (!voice || !('speechSynthesis' in window)) return
    const last = crew[crew.length - 1]
    if (!last || last.moment.importance < 0.6 || spoken.current.has(last.moment.id)) return
    spoken.current.add(last.moment.id)
    const u = new SpeechSynthesisUtterance(last.narration[lang])
    u.lang = lang === 'tr' ? 'tr-TR' : 'en-GB'
    speechSynthesis.speak(u)
  }, [crew, voice, lang])

  const overlay = activeOverlay(crew, clock)
  const persona = meta ? personaFor(mode, club, meta) : 'casual'
  const carrier = lastEvent && ['pass', 'carry', 'shot', 'possession_change', 'kickoff', 'corner'].includes(lastEvent.type)
    ? (lastEvent.type === 'pass' && lastEvent.outcome === 'success' ? lastEvent.target : lastEvent.player)
    : null

  const tags = useMemo(() => crew
    .filter((c) => c.overlay?.kind === 'player_tag' && c.moment.player && clock >= c.overlay.start_t && clock <= c.overlay.end_t)
    .map((c) => {
      const f = c.moment.facts as Record<string, number | string>
      const text = f.speed_kmh ? `⚡ ${f.speed_kmh} km/h` : f.shot_speed_kmh ? `🚀 ${f.shot_speed_kmh} km/h` : `★ ${String(f.milestone ?? '')}`
      return { player: c.moment.player as string, text }
    }), [crew, clock])

  if (!meta) return <div className="loading">{t('waiting', 'tr')} / {t('waiting', 'en')}</div>

  const allPlayers = [...meta.home.players, ...meta.away.players]
  const favTeam = favorite?.startsWith(meta.home.id) ? meta.home : meta.away

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">◉</span> {t('title', lang)}
          <small>{t('synthetic', lang)} · LLM: {meta.llm}</small>
        </div>
        <div className="controls">
          <div className="seg">
            {MODES.map((m) => (
              <button key={m} className={m === mode ? 'on' : ''} onClick={() => setMode(m)}>{t(m, lang)}</button>
            ))}
          </div>
          {mode === 'fan' && (
            <select value={club ?? ''} onChange={(e) => setClub(e.target.value)} aria-label={t('club', lang)}>
              <option value={meta.home.id}>{meta.home.name}</option>
              <option value={meta.away.id}>{meta.away.name}</option>
            </select>
          )}
          {mode === 'player' && (
            <select value={favorite ?? ''} onChange={(e) => setFavorite(e.target.value)} aria-label={t('favPlayer', lang)}>
              {allPlayers.map((p) => <option key={p.id} value={p.id}>{p.id.slice(0, 3)} #{p.number} {p.name}</option>)}
            </select>
          )}
          <div className="seg">
            {(['tr', 'en'] as Lang[]).map((l) => (
              <button key={l} className={l === lang ? 'on' : ''} onClick={() => setLang(l)}>{l.toUpperCase()}</button>
            ))}
          </div>
          <label className="speed">{t('speed', lang)}
            <select value={speed} onChange={(e) => { const v = Number(e.target.value); setSpeed(v); feed.setSpeed(v) }}>
              {[10, 20, 30, 60, 120, 240].map((v) => <option key={v} value={v}>{v}x</option>)}
            </select>
          </label>
          <button onClick={() => { if (paused) feed.resume(); else feed.pause(); setPaused(!paused) }}>
            {paused ? t('resume', lang) : t('pause', lang)}
          </button>
          <button onClick={feed.restart}>{t('restart', lang)}</button>
          <label className="chk"><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> {t('compare', lang)}</label>
          <label className="chk"><input type="checkbox" checked={voice} onChange={(e) => setVoice(e.target.checked)} /> {t('voice', lang)}</label>
        </div>
      </header>

      <main>
        <section className="stage">
          <div className="pitch-wrap">
            <Scorebug meta={meta} snap={snapshot} lang={lang} />
            <Pitch meta={meta} frame={frame} carrier={carrier} favorite={mode === 'player' ? favorite : null} tags={tags} />
            {overlay && !compare && (
              <div className={`ov-slot ${overlay.overlay?.kind}`}>
                <OverlayCard c={overlay} meta={meta} lang={lang} persona={persona} />
              </div>
            )}
          </div>
          {compare && overlay && (
            <div className="compare">
              <OverlayCard c={overlay} meta={meta} lang={lang} persona="analyst" label={t('analyst', lang)} />
              <OverlayCard c={overlay} meta={meta} lang={lang} persona="casual" label={t('casual', lang)} />
            </div>
          )}
          <div className="sub-title">{t('trace', lang)}</div>
          <TracePanel crew={crew} lang={lang} />
        </section>
        <aside>
          {mode === 'player' && favorite && (
            <PlayerPanel p={snapshot?.players[favorite]} lang={lang} color={favTeam.color} />
          )}
          <StatsPanel meta={meta} snap={snapshot} lang={lang} mode={mode} />
          <div className="sub-title">{t('commentary', lang)}</div>
          <Feed crew={crew} lang={lang} persona={persona} favorite={mode === 'player' ? favorite : null} />
        </aside>
      </main>
    </div>
  )
}
