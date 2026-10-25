import { useCallback, useEffect, useRef, useState } from 'react'
import type { BoothAnswer, CrewOutput, Frame, Lang, MatchEvent, Meta, Moment, Snapshot } from './types'

export interface MatchFeed {
  connected: boolean
  ended: boolean
  meta: Meta | null
  frame: Frame | null
  clock: number
  lastEvent: MatchEvent | null
  events: MatchEvent[]
  snapshot: Snapshot | null
  moments: Moment[]
  crew: CrewOutput[]
  answers: BoothAnswer[]
  pending: string[]
  ask: (question: string, lang: Lang) => void
  setSpeed: (v: number) => void
  pause: () => void
  resume: () => void
  restart: () => void
}

function wsUrl(seed: number, speed: number) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const base = import.meta.env.VITE_API_WS ?? `${proto}://${location.host}`
  return `${base}/ws/match?seed=${seed}&speed=${speed}&langs=en,tr`
}

export function useMatch(seed: number, initialSpeed: number): MatchFeed {
  const [run, setRun] = useState(0)
  const [connected, setConnected] = useState(false)
  const [ended, setEnded] = useState(false)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [frame, setFrame] = useState<Frame | null>(null)
  const [clock, setClock] = useState(0)
  const [lastEvent, setLastEvent] = useState<MatchEvent | null>(null)
  const [events, setEvents] = useState<MatchEvent[]>([])
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [moments, setMoments] = useState<Moment[]>([])
  const [crew, setCrew] = useState<CrewOutput[]>([])
  const [answers, setAnswers] = useState<BoothAnswer[]>([])
  const [pending, setPending] = useState<string[]>([])
  const ws = useRef<WebSocket | null>(null)

  useEffect(() => {
    setEnded(false); setEvents([]); setMoments([]); setCrew([]); setAnswers([]); setPending([]); setSnapshot(null); setClock(0)
    const sock = new WebSocket(wsUrl(seed, initialSpeed))
    ws.current = sock
    sock.onopen = () => setConnected(true)
    sock.onclose = () => setConnected(false)
    sock.onmessage = (msg) => {
      const { type, data } = JSON.parse(msg.data)
      switch (type) {
        case 'meta': setMeta(data); break
        case 'event': {
          const ev = data as MatchEvent
          if (ev.frame) setFrame(ev.frame)
          setClock(ev.t)
          setLastEvent(ev)
          setEvents((prev) => (prev.length > 60 ? [...prev.slice(-60), ev] : [...prev, ev]))
          break
        }
        case 'state': setSnapshot(data); break
        case 'moment': setMoments((prev) => [...prev, data]); break
        case 'crew': setCrew((prev) => [...prev, data]); break
        case 'answer':
          setAnswers((prev) => [...prev, data])
          setPending((prev) => prev.filter((id) => id !== data.id))
          break
        case 'end': setEnded(true); break
      }
    }
    return () => { sock.close() }
    // initialSpeed is only used for the first connection; later changes go through setSpeed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed, run])

  const send = useCallback((obj: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(obj))
  }, [])

  return {
    connected, ended, meta, frame, clock, lastEvent, events, snapshot, moments, crew, answers, pending,
    ask: (question, lang) => {
      const id = `q${Date.now()}`
      setPending((prev) => [...prev, id])
      send({ type: 'ask', question, lang, id })
    },
    setSpeed: (v) => send({ type: 'speed', value: v }),
    pause: () => send({ type: 'pause' }),
    resume: () => send({ type: 'resume' }),
    restart: () => setRun((r) => r + 1),
  }
}
