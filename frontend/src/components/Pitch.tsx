import type { Frame, Meta } from '../types'

interface Tag { player: string; text: string }

interface Props {
  meta: Meta
  frame: Frame | null
  carrier: string | null
  favorite: string | null
  tags: Tag[]
}

/** 2D pitch (105 x 68 m). Positions come from the synthetic engine's per-event frames. */
export function Pitch({ meta, frame, carrier, favorite, tags }: Props) {
  const teamOf = (pid: string) => (pid.startsWith(meta.home.id) ? meta.home : meta.away)
  const nameOf = (pid: string) => {
    const team = teamOf(pid)
    return team.players.find((p) => p.id === pid)
  }
  return (
    <svg className="pitch" viewBox="-3 -3 111 74" role="img" aria-label="Live pitch view">
      <rect x={-3} y={-3} width={111} height={74} className="pitch-bg" />
      <g className="lines">
        <rect x={0} y={0} width={105} height={68} />
        <line x1={52.5} y1={0} x2={52.5} y2={68} />
        <circle cx={52.5} cy={34} r={9.15} />
        <rect x={0} y={13.84} width={16.5} height={40.32} />
        <rect x={88.5} y={13.84} width={16.5} height={40.32} />
        <rect x={0} y={24.84} width={5.5} height={18.32} />
        <rect x={99.5} y={24.84} width={5.5} height={18.32} />
        <rect x={-1.5} y={30.34} width={1.5} height={7.32} />
        <rect x={105} y={30.34} width={1.5} height={7.32} />
      </g>
      {frame &&
        Object.entries(frame.players).map(([pid, [x, y]]) => {
          const team = teamOf(pid)
          const p = nameOf(pid)
          const isFav = pid === favorite
          return (
            <g key={pid} className="player" style={{ transform: `translate(${x}px, ${y}px)` }}>
              {isFav && <circle r={2.6} className="fav-ring" />}
              <circle r={1.5} fill={team.color} className={pid === carrier ? 'carrier' : ''} />
              <text className="num" y={0.55}>{p?.number}</text>
              {pid === carrier && <text className="name-tag" y={-2.4}>{p?.name}</text>}
            </g>
          )
        })}
      {frame && (
        <circle className="ball" r={0.75} style={{ transform: `translate(${frame.ball[0]}px, ${frame.ball[1]}px)` }} />
      )}
      {frame &&
        tags.map((tag, i) =>
          frame.players[tag.player] ? (
            <g key={tag.player + i} className="metric-tag"
               style={{ transform: `translate(${frame.players[tag.player][0]}px, ${frame.players[tag.player][1] + 4.2}px)` }}>
              <rect x={-9} y={-1.9} width={18} height={3} rx={0.8} />
              <text y={0.55}>{tag.text}</text>
            </g>
          ) : null,
        )}
    </svg>
  )
}
