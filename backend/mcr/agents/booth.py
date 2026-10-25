"""Ask the booth: viewers type a question and the Booth agent answers it from live match data.

The Booth agent never sees the stats directly. It has to fetch them through tools (match overview,
team stats, player stats, key moments, top players), and FactGuard checks the answer against exactly
what those tools returned. Two modes:

  native   the model decides which tools to call (Agent Framework function calling). Default for
           hosted models; MCR_BOOTH_TOOLS=native enables it for on-device models too.
  planned  a deterministic planner picks the tools from the question and the model writes the
           answer from their results. Default for on-device models, which are unreliable at tool calls.

Either way there is a timeout, one guarded retry and a templated, grounded fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Annotated, Any

from agent_framework import Agent

from mcr.agents.cache import call_slots
from mcr.agents.clients import backend_name, build_chat_client
from mcr.agents.guard import FactGuard
from mcr.stats.engine import MatchState
from mcr.stats.moments import Moment
from mcr.telemetry import tracer

log = logging.getLogger(__name__)

BOOTH_INSTRUCTIONS = (
    "You are the Booth analyst in a live football broadcast. A viewer asked `question`. "
    "Answer in the requested `language` in at most three sentences. "
    "Only use numbers that appear in tool results or in `tool_results`; never invent statistics, players or events. "
    "All clubs and players are fictional and synthetic. If the data cannot answer the question, say so briefly."
)
NATIVE_HINT = " Call the tools you need before answering."

MOMENT_KINDS = ("goal", "big_chance", "pressure_surge", "momentum_shift", "chaos_shift", "elite_pass")


def _fold(text: str) -> str:
    """Lowercase and strip accents so 'Baskı', 'baski' and 'BASKI' all match."""
    t = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("İ", "i")).lower()
    return "".join(c for c in t if not unicodedata.combining(c))


@dataclass
class ToolCall:
    name: str
    args: dict
    result: Any

    def to_dict(self) -> dict:
        return {"name": self.name, "args": self.args, "result": self.result}


class BoothTools:
    """Read-only views of the shared MatchState. Every call is recorded for the trace and the guard."""

    def __init__(self, state: MatchState, moments: list[Moment]):
        self.s = state
        self.moments = moments
        self.calls: list[ToolCall] = []

    # ------------------------------------------------------------ lookups
    def _team_id(self, team: str) -> str | None:
        key = _fold(team or "")
        for side in (self.s.meta.home, self.s.meta.away):
            names = {_fold(side["id"]), _fold(side["short"]), _fold(side["name"]), _fold(side["name"].split()[0])}
            if key in names or any(n and n in key for n in names if len(n) > 3):
                return side["id"]
        return None

    def _player_id(self, player: str) -> str | None:
        key = _fold(player or "")
        if not key:
            return None
        for pid, p in self.s.players.items():
            full = _fold(p.name)
            if key == full or key == _fold(pid) or key in re.split(r"[\s-]+", full) or full in key:
                return pid
        return None

    def _record(self, name: str, args: dict, result: Any) -> Any:
        self.calls.append(ToolCall(name, args, result))
        return result

    # ------------------------------------------------------------ tools
    def match_overview(self) -> dict:
        """Current minute, score, possession %, momentum (-100 away dominant .. +100 home dominant) and the
        control-vs-chaos index."""
        s = self.s
        cc = s.control_chaos()
        return self._record("match_overview", {}, {
            "minute": s.minute, "score_line": s.score_line(),
            "home": s.meta.home["name"], "away": s.meta.away["name"],
            "possession": {s.teams[t].name: v for t, v in s.possession_pct().items()},
            "momentum": s.momentum(),
            "momentum_leader": s.meta.home["name"] if s.momentum() > 0 else s.meta.away["name"],
            "control_chaos_index": cc["index"], "control_chaos_label": cc["label"],
        })

    def team_stats(self, team: Annotated[str, "Team name or short code, e.g. Riverside or RVA"]) -> dict:
        """Team totals (goals, shots, xG, passes, pass accuracy) and pressing: pressures per minute now
        versus the team's match average."""
        tid = self._team_id(team)
        if tid is None:
            return self._record("team_stats", {"team": team}, {"error": f"unknown team {team}"})
        t = self.s.teams[tid].to_dict()
        keep = ("name", "goals", "shots", "shots_on_target", "xg", "passes", "pass_accuracy", "pressures",
                "tackles", "interceptions", "turnovers")
        out = {k: t[k] for k in keep if k in t}
        out["pressures_per_min_now"] = self.s.pressure_rate(tid)
        out["pressures_per_min_match_avg"] = self.s.baseline_pressure_rate(tid, self.s.clock)
        return self._record("team_stats", {"team": team}, out)

    def player_stats(self, player: Annotated[str, "Player name or surname"]) -> dict:
        """One player's live stats: touches, passes, pass accuracy, key passes, shots, goals, xG,
        tackles, distance covered and top speed."""
        pid = self._player_id(player)
        if pid is None:
            return self._record("player_stats", {"player": player}, {"error": f"unknown player {player}"})
        p = self.s.players[pid].to_dict()
        keep = ("name", "team", "role", "touches", "passes", "pass_accuracy", "key_passes", "shots", "goals",
                "xg", "tackles", "interceptions", "distance_km", "top_speed_kmh")
        return self._record("player_stats", {"player": player}, {k: p[k] for k in keep if k in p})

    def key_moments(self, kind: Annotated[str, "One of: all, " + ", ".join(MOMENT_KINDS)] = "all",
                    team: Annotated[str, "Optional team name or short code to filter by"] = "") -> list:
        """The most recent key moments detected by the analytics, with their facts and evidence."""
        tid = self._team_id(team) if team else None
        ms = [m for m in self.moments if m.type in MOMENT_KINDS and (kind in ("all", "", None) or m.type == kind)
              and (tid is None or m.team == tid)]
        out = [{"type": m.type, "minute": m.minute, "facts": m.facts, "evidence": m.evidence} for m in ms[-6:]]
        args = {"kind": kind, **({"team": team} if team else {})}
        return self._record("key_moments", args, out)

    def top_players(self, metric: Annotated[str, "xg, goals, key_passes, distance_km or top_speed_kmh"] = "xg") -> list:
        """The three best players so far by one metric."""
        metric = metric if metric in ("xg", "goals", "key_passes", "distance_km", "top_speed_kmh") else "xg"
        rows = [p.to_dict() for p in self.s.players.values()]
        ranked = sorted(rows, key=lambda p: p.get(metric, 0), reverse=True)[:3]
        out = [{"name": p["name"], "team": p["team"], metric: p[metric]} for p in ranked]
        return self._record("top_players", {"metric": metric}, out)

    def all(self) -> list:
        return [self.match_overview, self.team_stats, self.player_stats, self.key_moments, self.top_players]


# ---------------------------------------------------------------- planner (deterministic tool choice)
TOPICS = {
    "press": ("press", "baski", "pres", "intensity", "yogun"),
    "chance": ("xg", "chance", "sans", "firsat", "shot", "sut", "pozisyon"),
    "momentum": ("momentum", "dominat", "on top", "ustun", "kontrol", "control", "chaos", "kaos", "better", "iyi oyn"),
    "best": ("best", "star", "top player", "man of", "en iyi", "yildiz", "fastest", "hizli", "kosu", "kost", "ran ", "run", "distance"),
    "goal": ("goal", "gol", "score", "skor"),
}


def plan(tools: BoothTools, question: str) -> list[tuple[str, dict]]:
    q = _fold(question)
    words = re.findall(r"[a-z0-9]+", q)
    teams = []
    for side in (tools.s.meta.home, tools.s.meta.away):
        if any(tools._team_id(w) == side["id"] for w in words if len(w) >= 3):
            teams.append(side["id"])
    players = []
    for w in words:
        pid = tools._player_id(w) if len(w) >= 4 else None
        if pid and pid not in players:
            players.append(pid)
    topics = [t for t, keys in TOPICS.items() if any(k in q for k in keys)]

    steps: list[tuple[str, dict]] = []
    for pid in players[:2]:
        steps.append(("player_stats", {"player": tools.s.players[pid].name}))
    if "press" in topics:
        for tid in teams or [tools.s.home_id, tools.s.away_id]:
            steps.append(("team_stats", {"team": tid}))
        steps.append(("key_moments", {"kind": "pressure_surge", **({"team": teams[0]} if len(teams) == 1 else {})}))
    if "chance" in topics and not players:
        for tid in teams or [tools.s.home_id, tools.s.away_id]:
            if ("team_stats", {"team": tid}) not in steps:
                steps.append(("team_stats", {"team": tid}))
    if "goal" in topics:
        steps.append(("key_moments", {"kind": "goal"}))
    if "best" in topics:
        metric = "distance_km" if any(k in q for k in ("kosu", "kost", "ran ", "run", "distance")) else (
            "top_speed_kmh" if any(k in q for k in ("fastest", "hizli")) else "xg")
        steps.append(("top_players", {"metric": metric}))
    if "momentum" in topics or not steps:
        steps.insert(0, ("match_overview", {}))
        if "momentum" in topics:
            steps.append(("key_moments", {"kind": "momentum_shift"}))
    elif teams and not any(n == "team_stats" for n, _ in steps) and not players:
        steps.extend(("team_stats", {"team": tid}) for tid in teams)
    return steps


def run_plan(tools: BoothTools, steps: list[tuple[str, dict]]) -> None:
    fns = {f.__name__: f for f in tools.all()}
    for name, args in steps:
        with tracer.start_as_current_span(f"mcr.tool {name}") as span:
            span.set_attribute("mcr.tool.args", json.dumps(args))
            fns[name](**args)


METRIC_LABELS = {"xg": ("xG", "xG"), "goals": ("goals", "gol"), "key_passes": ("key passes", "kilit pas"),
                 "distance_km": ("distance (km)", "koşu mesafesi (km)"),
                 "top_speed_kmh": ("top speed (km/h)", "en yüksek hız (km/s)")}


# ---------------------------------------------------------------- template answer (offline + fallback)
def _fmt(v) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def template_answer(calls: list[ToolCall], lang: str) -> str:
    tr = lang == "tr"
    parts: list[str] = []
    for c in calls:
        r = c.result
        if isinstance(r, dict) and "error" in r:
            continue
        if c.name == "match_overview":
            parts.append(
                (f"{r['minute']}. dakikada skor {r['score_line']}. Momentum {_fmt(abs(r['momentum']))} ile "
                 f"{r['momentum_leader']} tarafında, oyun '{r['control_chaos_label']}' modunda (kaos endeksi "
                 f"{_fmt(r['control_chaos_index'])}).") if tr else
                (f"It's {r['score_line']} in minute {r['minute']}. Momentum sits with {r['momentum_leader']} "
                 f"({_fmt(abs(r['momentum']))}) and the game reads as '{r['control_chaos_label']}' "
                 f"(chaos index {_fmt(r['control_chaos_index'])})."))
        elif c.name == "team_stats":
            parts.append(
                (f"{r['name']}: {_fmt(r['xg'])} xG, {r['shots']} şut, %{_fmt(r['pass_accuracy'])} pas isabeti; "
                 f"şu an dakikada {_fmt(r['pressures_per_min_now'])} baskı (maç ortalaması "
                 f"{_fmt(r['pressures_per_min_match_avg'])}).") if tr else
                (f"{r['name']}: {_fmt(r['xg'])} xG from {r['shots']} shots, {_fmt(r['pass_accuracy'])}% passing, "
                 f"pressing {_fmt(r['pressures_per_min_now'])} times a minute now against a match average of "
                 f"{_fmt(r['pressures_per_min_match_avg'])}."))
        elif c.name == "player_stats":
            parts.append(
                (f"{r['name']}: {r['goals']} gol, {_fmt(r['xg'])} xG, {r['key_passes']} kilit pas, "
                 f"{_fmt(r['distance_km'])} km koşu, en yüksek hız {_fmt(r['top_speed_kmh'])} km/s.") if tr else
                (f"{r['name']}: {r['goals']} goals, {_fmt(r['xg'])} xG, {r['key_passes']} key passes, "
                 f"{_fmt(r['distance_km'])} km covered, top speed {_fmt(r['top_speed_kmh'])} km/h."))
        elif c.name == "key_moments" and r:
            last = r[-1]
            if c.args.get("kind") == "pressure_surge":  # the strongest surge tells the story, not the latest
                last = max(r, key=lambda m: m["facts"].get("ratio", 0))
            f = last["facts"]
            if last["type"] == "pressure_surge":
                parts.append(
                    (f"{last['minute']}. dakikada {f.get('team', '')} baskıyı normalin {_fmt(f.get('ratio'))} katına "
                     f"çıkardı.") if tr else
                    (f"In minute {last['minute']} {f.get('team', '')} pressed at {_fmt(f.get('ratio'))}x their "
                     f"normal rate."))
            elif last["type"] == "goal":
                parts.append(
                    (f"Son gol {last['minute']}. dakikada {f.get('player', '')} ({_fmt(f.get('xg'))} xG), "
                     f"skor {f.get('score_line', '')}.") if tr else
                    (f"The latest goal came in minute {last['minute']} from {f.get('player', '')} "
                     f"({_fmt(f.get('xg'))} xG), making it {f.get('score_line', '')}."))
            else:
                label = last["type"].replace("_", " ")
                parts.append((f"Son kritik an: {last['minute']}. dakikada {label}.") if tr else
                             (f"Latest key moment: {label} in minute {last['minute']}."))
        elif c.name == "top_players" and r:
            metric = next(k for k in r[0] if k not in ("name", "team"))
            label = METRIC_LABELS.get(metric, (metric, metric))[1 if tr else 0]
            names = ", ".join(f"{p['name']} ({_fmt(p[metric])})" for p in r)
            parts.append((f"{label} sıralaması: {names}.") if tr else (f"Top by {label}: {names}."))
    if not parts:
        return ("Bu soruyu maç verisiyle cevaplayamıyorum." if tr else
                "I can't answer that from the match data.")
    text = " ".join(parts[:3])
    return re.sub(r"(\d)\.(\d)", r"\1,\2", text) if tr else text  # Turkish decimal comma


# ---------------------------------------------------------------- the agent
@dataclass
class BoothAnswer:
    id: str
    question: str
    language: str
    answer: str
    mode: str
    status: str  # ok | retried | fallback
    latency_ms: int
    tools: list[dict] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def native_tools_default() -> bool:
    env = os.getenv("MCR_BOOTH_TOOLS")
    if env:
        return env.lower() == "native"
    return backend_name() in ("foundry", "azure_openai")


class BoothAgent:
    def __init__(self, state: MatchState, moments: list[Moment], client: Any | None = None,
                 native: bool | None = None, timeout_s: float | None = None):
        self.state = state
        self.moments = moments
        self.client = client or build_chat_client()
        self.native = native_tools_default() if native is None else native
        default_timeout = "60" if backend_name() == "foundry_local" else "20"
        self.timeout_s = timeout_s or float(os.getenv("MCR_BOOTH_TIMEOUT_S", default_timeout))
        self.slots = call_slots(backend_name()) if client is None else None
        self.guard = FactGuard()
        self._n = 0

    async def _run(self, agent: Agent, prompt: str):
        if self.slots is None:
            return await asyncio.wait_for(agent.run(prompt), timeout=self.timeout_s)
        async with self.slots:
            return await asyncio.wait_for(agent.run(prompt), timeout=self.timeout_s)

    async def ask(self, question: str, lang: str = "en", qid: str | None = None) -> BoothAnswer:
        with tracer.start_as_current_span("mcr.booth ask") as span:
            out = await self._ask(question, lang, qid)
            span.set_attributes({"mcr.booth.mode": out.mode, "mcr.status": out.status,
                                 "mcr.booth.tools": [t["name"] for t in out.tools], "mcr.latency_ms": out.latency_ms})
            return out

    async def _ask(self, question: str, lang: str = "en", qid: str | None = None) -> BoothAnswer:
        self._n += 1
        qid = qid or f"q{self._n}"
        lang = lang if lang in ("en", "tr") else "en"
        t0 = time.perf_counter()
        question = question.strip()[:300]
        tools = BoothTools(self.state, self.moments)
        ctx = {"minute": self.state.minute, "score_line": self.state.score_line(),
               "home": self.state.meta.home["name"], "away": self.state.meta.away["name"]}
        mode = "native" if self.native else "planned"
        note = ""
        payload: dict = {"task": "ask", "question": question, "language": lang, "context": ctx}

        for attempt in (1, 2):
            try:
                if mode == "native":
                    tools.calls.clear()
                    agent = Agent(self.client, BOOTH_INSTRUCTIONS + NATIVE_HINT, name="booth", tools=tools.all())
                    resp = await self._run(agent, json.dumps(payload, ensure_ascii=False))
                else:
                    if not tools.calls:
                        run_plan(tools, plan(tools, question))
                    p = dict(payload, tool_results=[c.to_dict() for c in tools.calls],
                             offline_answer=template_answer(tools.calls, lang))
                    agent = Agent(self.client, BOOTH_INSTRUCTIONS, name="booth")
                    resp = await self._run(agent, json.dumps(p, ensure_ascii=False))
                text = (resp.text or "").strip().strip('"')
                if not text:
                    note = "empty answer"
                elif mode == "native" and not tools.calls:
                    note = "answered without calling any tool"
                else:
                    # Only tool results and the live context count; a number in the question itself does not,
                    # so "Is it true they made 500 pressures?" cannot launder 500 into the answer.
                    g = self.guard.check(text, {"tools": [c.result for c in tools.calls], "context": ctx})
                    if g.ok:
                        return BoothAnswer(qid, question, lang, text, mode, "ok" if attempt == 1 else "retried",
                                           int((time.perf_counter() - t0) * 1000),
                                           [c.to_dict() for c in tools.calls], note)
                    note = g.feedback()
                payload = dict(payload, feedback=note)
            except asyncio.TimeoutError:
                note = f"timeout after {self.timeout_s}s"
            except Exception as exc:  # model/network errors must not break the booth
                root = exc
                while root.__cause__ is not None:
                    root = root.__cause__
                note = f"{type(root).__name__}: {str(root)[:200]}"
                log.warning("booth call failed (attempt %d): %s", attempt, note)
            if mode == "native":
                mode = "planned"  # second attempt: deterministic tool choice, model only writes the answer
                tools.calls.clear()

        if not tools.calls:
            run_plan(tools, plan(tools, question))
        return BoothAnswer(qid, question, lang, template_answer(tools.calls, lang), "planned", "fallback",
                           int((time.perf_counter() - t0) * 1000), [c.to_dict() for c in tools.calls], note)
