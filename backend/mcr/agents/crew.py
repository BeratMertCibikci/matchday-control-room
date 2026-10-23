"""The broadcast crew: specialised agents that turn a detected moment into on-air content.

Flow for each narrated moment (shared state = the MatchState snapshot + the moment's facts):

    Analyst ──► FactGuard ──┬─► Narrator[lang]  ──► FactGuard ─┐
                            └─► Persona[lang]   ──► FactGuard ─┴─► Producer ──► overlay
    (half/full time) Recap[lang] ──► FactGuard ──► Producer

Every LLM call has a timeout, one guarded retry with corrective feedback, and a deterministic
fallback, so a failing model degrades the broadcast instead of breaking it. Each step is traced.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_framework import Agent

from mcr.agents import templates
from mcr.agents.cache import ResponseCache, default_cache, default_limiter
from mcr.agents.clients import backend_name, build_chat_client, render_offline
from mcr.agents.guard import FactGuard
from mcr.agents.producer import Producer
from mcr.stats.engine import MatchState
from mcr.stats.moments import Moment

LANG_NAMES = {"en": "English", "tr": "Turkish", "es": "Spanish", "fr": "French", "ar": "Arabic", "pt": "Portuguese"}

COMMON_RULES = (
    "You work in a live football broadcast crew. The input is a JSON object. "
    "Only use numbers that appear in `facts` or `context`; never invent statistics, players or events. "
    "All clubs and players are fictional and synthetic. Be concise."
)

ANALYST_INSTRUCTIONS = COMMON_RULES + (
    " You are the Analyst. Explain in at most two sentences WHY this moment matters for the match: "
    "tactics, control vs chaos, pressure, rhythm, or what the numbers say compared with the scoreline. "
    "Reply in English with plain text only."
)
NARRATOR_INSTRUCTIONS = COMMON_RULES + (
    " You are the Narrator, a TV commentator. Write ONE energetic commentary line (max 20 words) for this "
    "moment in the requested `language`. Plain text only."
)
PERSONA_INSTRUCTIONS = COMMON_RULES + (
    " You are the Persona editor. Rewrite the analyst's explanation (`why`) for four audiences in the requested "
    "`language`: `analyst` (data-rich, 2 sentences, keep key numbers), `casual` (one simple sentence, at most one "
    "number), `fan_home` (one sentence from the perspective of a `home` supporter), `fan_away` (one sentence from "
    "the perspective of an `away` supporter). Reply with a JSON object with exactly those four keys."
)
RECAP_INSTRUCTIONS = COMMON_RULES + (
    " You are the Recap writer. Using `facts` and the list of key `moments`, write a recap in the requested "
    "`language`. Reply with a JSON object: {\"headline\": str, \"summary\": str (3 sentences max), "
    "\"bullets\": [3 short social-media bullets]}."
)


@dataclass
class TraceStep:
    moment_id: str
    agent: str
    status: str  # ok | retried | fallback | skipped
    latency_ms: int
    attempts: int
    note: str = ""
    language: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class CrewOutput:
    moment: dict
    why: str
    narration: dict
    cards: dict
    decision: dict
    overlay: dict | None
    trace: list[dict] = field(default_factory=list)
    recap: dict | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    start, end = t.find("{"), t.rfind("}")
    return t[start:end + 1] if start >= 0 and end > start else t


class BroadcastCrew:
    def __init__(self, state: MatchState, langs: tuple[str, ...] = ("en", "tr"), client: Any | None = None,
                 timeout_s: float | None = None):
        self.state = state
        self.langs = langs
        # On-device models are slower than hosted ones, so they get a longer default timeout.
        default_timeout = "45" if backend_name() == "foundry_local" else "12"
        self.timeout_s = timeout_s or float(os.getenv("MCR_AGENT_TIMEOUT_S", default_timeout))
        self.client = client or build_chat_client()
        self.model_label = "offline-templates" if backend_name() == "offline" and client is None else backend_name()
        self.cache = default_cache(backend_name()) if client is None else ResponseCache(None)
        self.limiter = default_limiter(backend_name()) if client is None else default_limiter("offline")
        self.analyst = Agent(self.client, ANALYST_INSTRUCTIONS, name="analyst")
        self.narrator = Agent(self.client, NARRATOR_INSTRUCTIONS, name="narrator")
        self.persona = Agent(self.client, PERSONA_INSTRUCTIONS, name="persona")
        self.recapper = Agent(self.client, RECAP_INSTRUCTIONS, name="recap")
        self.guard = FactGuard()
        self.producer = Producer()
        self.log: list[dict] = []  # narrated moments, for recaps
        self.home = state.meta.home["name"]
        self.away = state.meta.away["name"]

    # ------------------------------------------------------------------ core call
    async def _call(self, agent: Agent, payload: dict, moment: Moment, validate: Callable[[str], tuple[bool, Any, str]],
                    language: str | None = None) -> tuple[Any, TraceStep]:
        t0 = time.perf_counter()
        key = ResponseCache.key(self.model_label, agent.name, payload)
        cached = self.cache.get(key)
        if cached is not None:
            return cached, TraceStep(moment.id, agent.name, "cached", int((time.perf_counter() - t0) * 1000), 0,
                                     "validated model answer reused", language)
        messages = json.dumps(payload, ensure_ascii=False)
        note = ""
        for attempt in (1, 2):
            try:
                await self.limiter.wait()
                resp = await asyncio.wait_for(agent.run(messages), timeout=self.timeout_s)
                ok, value, feedback = validate(resp.text or "")
                if ok:
                    status = "ok" if attempt == 1 else "retried"
                    self.cache.put(key, value)
                    return value, TraceStep(moment.id, agent.name, status, int((time.perf_counter() - t0) * 1000),
                                            attempt, note, language)
                note = feedback
                payload = dict(payload, feedback=feedback)
                messages = json.dumps(payload, ensure_ascii=False)
            except asyncio.TimeoutError:
                note = f"timeout after {self.timeout_s}s"
            except Exception as exc:  # model/network errors must not stop the broadcast
                note = f"{type(exc).__name__}: {str(exc)[:120]}"
        fallback = render_offline(payload)
        _, value, _ = validate(fallback)
        if value is None:  # templates are grounded by construction; never leave the slot empty
            value = json.loads(fallback) if fallback.lstrip().startswith("{") else fallback
        return value, TraceStep(moment.id, agent.name, "fallback", int((time.perf_counter() - t0) * 1000), 2,
                                note or "rejected", language)

    def _text_validator(self, facts: dict, extra: list | None = None):
        def v(text: str):
            text = text.strip().strip('"')
            if not text:
                return False, None, "empty answer"
            g = self.guard.check(text, facts, extra)
            return g.ok, text, "" if g.ok else g.feedback()
        return v

    def _json_validator(self, keys: tuple[str, ...], facts: dict, extra: list | None = None, text_keys=None):
        def v(text: str):
            try:
                obj = json.loads(_strip_json(text))
            except json.JSONDecodeError:
                return False, None, "answer was not valid JSON"
            if not all(k in obj for k in keys):
                return False, None, f"JSON must contain keys {list(keys)}"
            for k in text_keys or keys:
                val = obj[k] if isinstance(obj[k], str) else " ".join(map(str, obj[k]))
                g = self.guard.check(val, facts, extra)
                if not g.ok:
                    return False, None, g.feedback()
            return True, obj, ""
        return v

    def _context(self) -> dict:
        s = self.state
        return {
            "minute": s.minute,
            "score_line": s.score_line(),
            "possession": s.possession_pct(),
            "home": self.home,
            "away": self.away,
        }

    # ------------------------------------------------------------------ public
    async def process(self, m: Moment) -> CrewOutput:
        ctx = self._context()
        extra = [ctx["minute"], ctx["possession"], m.minute]
        base = {"moment_type": m.type, "facts": m.facts, "evidence": m.evidence, "context": ctx,
                "home": self.home, "away": self.away,
                "team_is_home": None if m.team is None else self.state.is_home(m.team)}
        trace: list[TraceStep] = []

        if m.type in ("half_time", "full_time"):
            return await self._recap(m, base, extra)

        if not m.narrate:
            # Minor tags skip the LLM entirely: deterministic, instant, zero cost.
            narration = {lang: templates.narrate(m.type, m.facts, lang) for lang in self.langs}
            why = templates.explain(m.type, m.facts, "en")
            cards = {p: dict(narration) for p in templates.PERSONAS}
            trace.append(TraceStep(m.id, "producer", "skipped", 0, 0, "minor moment, templated tag"))
        else:
            why, step = await self._call(self.analyst, dict(base, task="explain"), m,
                                         self._text_validator(m.facts, extra))
            trace.append(step)
            jobs = []
            for lang in self.langs:
                jobs.append(self._call(self.narrator, dict(base, task="narrate", language=lang,
                                                           language_name=LANG_NAMES.get(lang, lang)),
                                       m, self._text_validator(m.facts, extra), lang))
                jobs.append(self._call(self.persona, dict(base, task="personas", why=why, language=lang,
                                                          language_name=LANG_NAMES.get(lang, lang)),
                                       m, self._json_validator(tuple(templates.PERSONAS), m.facts, extra), lang))
            results = await asyncio.gather(*jobs)
            narration, cards = {}, {p: {} for p in templates.PERSONAS}
            for i, lang in enumerate(self.langs):
                (line, s1), (variants, s2) = results[2 * i], results[2 * i + 1]
                narration[lang] = line
                for p in templates.PERSONAS:
                    cards[p][lang] = variants[p]
                trace.extend([s1, s2])

        payload = {"type": m.type, "minute": m.minute, "narration": narration, "cards": cards,
                   "facts": m.facts, "evidence": m.evidence}
        t0 = time.perf_counter()
        decision = self.producer.decide(m, payload)
        trace.append(TraceStep(m.id, "producer", decision.action, int((time.perf_counter() - t0) * 1000), 1,
                               decision.reason))
        out = CrewOutput(
            moment=m.to_dict(), why=why, narration=narration, cards=cards,
            decision={"action": decision.action, "reason": decision.reason},
            overlay=decision.overlay.to_dict() if decision.overlay and decision.action != "drop" else None,
            trace=[t.to_dict() for t in trace],
        )
        if m.narrate:
            self.log.append({"type": m.type, "minute": m.minute, "facts": m.facts})
        return out

    async def _recap(self, m: Moment, base: dict, extra: list) -> CrewOutput:
        moments = [x for x in self.log if x["type"] in ("goal", "big_chance", "pressure_surge", "momentum_shift")][-12:]
        jobs = [
            self._call(self.recapper, dict(base, task="recap", kind=m.type, moments=moments, language=lang,
                                           language_name=LANG_NAMES.get(lang, lang)),
                       m, self._json_validator(("headline", "summary", "bullets"), m.facts,
                                               extra + [x["facts"] for x in moments] + [x["minute"] for x in moments]),
                       lang)
            for lang in self.langs
        ]
        results = await asyncio.gather(*jobs)
        recap = {lang: r for lang, (r, _) in zip(self.langs, results)}
        trace = [s for _, s in results]
        narration = {lang: templates.narrate(m.type, m.facts, lang) for lang in self.langs}
        payload = {"type": m.type, "minute": m.minute, "narration": narration, "recap": recap, "facts": m.facts}
        decision = self.producer.decide(m, payload)
        trace.append(TraceStep(m.id, "producer", decision.action, 0, 1, decision.reason))
        return CrewOutput(
            moment=m.to_dict(), why=recap[self.langs[0]]["summary"], narration=narration,
            cards={p: {lang: recap[lang]["summary"] for lang in self.langs} for p in templates.PERSONAS},
            decision={"action": decision.action, "reason": decision.reason},
            overlay=decision.overlay.to_dict() if decision.overlay else None,
            trace=[t.to_dict() for t in trace], recap=recap,
        )
