import { useState } from 'react'
import { ASK_SUGGESTIONS, t } from '../i18n'
import type { BoothAnswer, Lang } from '../types'

const STATUS_CLASS: Record<string, string> = { ok: 'ok', retried: 'warn', fallback: 'bad' }

function argText(args: Record<string, unknown>) {
  return Object.values(args).map(String).join(', ')
}

export function AskBooth({ answers, pending, lang, onAsk }: {
  answers: BoothAnswer[]; pending: string[]; lang: Lang; onAsk: (q: string, lang: Lang) => void
}) {
  const [q, setQ] = useState('')
  const submit = (text: string) => {
    if (!text.trim()) return
    onAsk(text.trim(), lang)
    setQ('')
  }
  return (
    <div className="panel ask">
      <form onSubmit={(e) => { e.preventDefault(); submit(q) }} className="ask-form">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('askPlaceholder', lang)} maxLength={300} />
        <button type="submit" className="ask-send">{t('askSend', lang)}</button>
      </form>
      <div className="ask-chips">
        {ASK_SUGGESTIONS[lang].map((s) => <button key={s} onClick={() => submit(s)}>{s}</button>)}
      </div>
      {pending.length > 0 && <div className="ask-thinking">{t('askThinking', lang)}</div>}
      {answers.slice(-3).reverse().map((a) => (
        <div key={a.id} className="ask-answer">
          <div className="ask-q">“{a.question}”</div>
          <div className="ask-a">{a.answer || '—'}</div>
          <div className="ask-tools">
            <span className="ask-tools-label">{t('toolsUsed', lang)}:</span>
            {a.tools.map((c, i) => (
              <span key={i} className="tool" title={JSON.stringify(c.result)}>🔧 {c.name}({argText(c.args)})</span>
            ))}
            <span className={`step ${STATUS_CLASS[a.status] ?? ''}`} title={a.note}>
              booth·{a.mode} <em>{a.status}</em> <small>{a.latency_ms}ms</small>
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}
