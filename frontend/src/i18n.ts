import type { Lang } from './types'

const STRINGS = {
  title: { en: 'Matchday Control Room', tr: 'Matchday Kontrol Odası' },
  mode: { en: 'Mode', tr: 'Mod' },
  analyst: { en: 'Analyst', tr: 'Analist' },
  casual: { en: 'Casual fan', tr: 'Sıradan taraftar' },
  fan: { en: 'Club fan', tr: 'Kulüp taraftarı' },
  player: { en: 'Player focus', tr: 'Oyuncu odağı' },
  club: { en: 'My club', tr: 'Takımım' },
  favPlayer: { en: 'My player', tr: 'Oyuncum' },
  speed: { en: 'Speed', tr: 'Hız' },
  pause: { en: 'Pause', tr: 'Duraklat' },
  resume: { en: 'Resume', tr: 'Devam' },
  restart: { en: 'Restart', tr: 'Baştan' },
  compare: { en: 'Compare modes', tr: 'Modları karşılaştır' },
  possession: { en: 'Possession', tr: 'Topa sahip olma' },
  shots: { en: 'Shots', tr: 'Şut' },
  onTarget: { en: 'On target', tr: 'İsabetli' },
  passAcc: { en: 'Pass accuracy', tr: 'Pas isabeti' },
  pressures: { en: 'Pressures', tr: 'Pres' },
  pressRate: { en: 'Press / min (5\')', tr: 'Pres / dk (5\')' },
  momentum: { en: 'Momentum', tr: 'Momentum' },
  controlChaos: { en: 'Control vs chaos', tr: 'Kontrol vs kaos' },
  control: { en: 'Control', tr: 'Kontrol' },
  chaos: { en: 'Chaos', tr: 'Kaos' },
  balanced: { en: 'Balanced', tr: 'Dengeli' },
  turnovers: { en: 'Turnovers/min', tr: 'Top kaybı/dk' },
  longShare: { en: 'Long passes', tr: 'Uzun pas' },
  chain: { en: 'Passes/possession', tr: 'Pas/atak' },
  commentary: { en: 'Live commentary', tr: 'Canlı anlatım' },
  trace: { en: 'Agent trace', tr: 'Agent izi' },
  whyMatters: { en: 'Why it matters', tr: 'Neden önemli' },
  distance: { en: 'Distance', tr: 'Mesafe' },
  topSpeed: { en: 'Top speed', tr: 'Maks. hız' },
  goals: { en: 'Goals', tr: 'Gol' },
  keyPasses: { en: 'Key passes', tr: 'Kilit pas' },
  passDiff: { en: 'Avg pass difficulty', tr: 'Ort. pas zorluğu' },
  touches: { en: 'Touches', tr: 'Topla buluşma' },
  waiting: { en: 'Connecting to the match feed…', tr: 'Maç akışına bağlanıyor…' },
  synthetic: { en: 'Synthetic data · fictional clubs', tr: 'Sentetik veri · kurgusal kulüpler' },
  fullTime: { en: 'Full-time', tr: 'Maç sonu' },
  halfTime: { en: 'Half-time', tr: 'Devre arası' },
  voice: { en: 'Voice', tr: 'Ses' },
} as const

const MOMENT_LABELS: Record<string, Record<Lang, string>> = {
  goal: { en: 'Goal', tr: 'Gol' },
  big_chance: { en: 'Big chance', tr: 'Büyük fırsat' },
  pressure_surge: { en: 'Pressure surge', tr: 'Pres artışı' },
  momentum_shift: { en: 'Momentum shift', tr: 'Momentum değişimi' },
  chaos_shift: { en: 'Game rhythm', tr: 'Oyun ritmi' },
  elite_pass: { en: 'Elite pass', tr: 'Elit pas' },
  milestone: { en: 'Milestone', tr: 'Kilometre taşı' },
  rocket_shot: { en: 'Shot speed', tr: 'Şut hızı' },
  sprint: { en: 'Sprint', tr: 'Sprint' },
  half_time: { en: 'Half-time', tr: 'Devre arası' },
  full_time: { en: 'Full-time', tr: 'Maç sonu' },
}

export function momentLabel(type: string, lang: Lang): string {
  return MOMENT_LABELS[type]?.[lang] ?? type
}

export type StringKey = keyof typeof STRINGS

export function t(key: StringKey, lang: Lang): string {
  return STRINGS[key][lang]
}
