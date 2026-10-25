export type Lang = 'en' | 'tr'
export type Persona = 'analyst' | 'casual' | 'fan_home' | 'fan_away'
export type Mode = 'analyst' | 'casual' | 'fan' | 'player'

export interface PlayerMeta { id: string; name: string; number: number; role: string }
export interface TeamMeta {
  id: string; name: string; short: string; color: string
  formation: string; manager: string; players: PlayerMeta[]
}
export interface Meta { seed: number; home: TeamMeta; away: TeamMeta; venue: string; llm: string; langs: Lang[] }

export interface Frame { ball: [number, number]; players: Record<string, [number, number]> }
export interface MatchEvent {
  id: number; t: number; minute: number; period: number; type: string
  team: string | null; player: string | null; target: string | null
  x: number | null; y: number | null; end_x: number | null; end_y: number | null
  outcome: string | null; attrs: Record<string, unknown>; frame: Frame | null
}

export interface TeamStats {
  id: string; name: string; goals: number; passes: number; passes_completed: number; pass_accuracy: number
  shots: number; shots_on_target: number; xg: number; pressures: number; tackles: number
  interceptions: number; turnovers: number; corners: number; fouls: number; final_third_entries: number
}
export interface PlayerStats {
  id: string; name: string; team: string; number: number; role: string; touches: number
  passes: number; passes_completed: number; pass_accuracy: number; key_passes: number; shots: number
  goals: number; xg: number; tackles: number; interceptions: number; pressures: number
  distance_km: number; top_speed_kmh: number; avg_pass_difficulty: number
}
export interface ControlChaos {
  index: number; turnovers_per_min: number; pressures_per_min: number
  long_pass_share: number; avg_chain_passes: number; label: 'control' | 'balanced' | 'chaos'
}
export interface Snapshot {
  minute: number; period: number; clock: number; score: Record<string, number>; score_line: string
  possession: Record<string, number>; teams: Record<string, TeamStats>; momentum: number
  momentum_series: { minute: number; value: number }[]; pressure_rate: Record<string, number>
  control_chaos: ControlChaos; top_players: PlayerStats[]; players: Record<string, PlayerStats>; finished: boolean
}

export interface Moment {
  id: string; type: string; minute: number; t: number; importance: number
  team: string | null; player: string | null; facts: Record<string, unknown>
  evidence: { metric: string; value: number; baseline?: number }[]
}
export interface TraceStep {
  moment_id: string; agent: string; status: string; latency_ms: number; attempts: number
  note: string; language: string | null
}
export interface Overlay {
  id: string; moment_id: string; kind: 'lower_third' | 'stat_card' | 'player_tag' | 'recap_panel'
  priority: number; start_t: number; end_t: number; team: string | null; player: string | null
}
export interface Recap { headline: string; summary: string; bullets: string[] }
export interface CrewOutput {
  moment: Moment; why: string
  narration: Record<Lang, string>
  cards: Record<Persona, Record<Lang, string>>
  decision: { action: string; reason: string }
  overlay: Overlay | null; trace: TraceStep[]
  recap: Record<Lang, Recap> | null
}

export interface ToolCall { name: string; args: Record<string, unknown>; result: unknown }
export interface BoothAnswer {
  id: string; question: string; language: Lang; answer: string
  mode: 'native' | 'planned'; status: string; latency_ms: number; tools: ToolCall[]; note: string
}
