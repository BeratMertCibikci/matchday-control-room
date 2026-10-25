"""HTTP + WebSocket API.

WS /ws/match streams a live synthetic match: raw events (with positional frames), periodic state
snapshots, and crew outputs (narration, persona cards, overlay decisions, agent trace). Crew work
runs in background tasks so model latency never stalls the event feed.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from mcr.agents.booth import BoothAgent
from mcr.agents.clients import backend_name
from mcr.agents.crew import BroadcastCrew
from mcr.sim.engine import MatchEngine, generate_match
from mcr.stats.engine import MatchState
from mcr.stats.moments import MomentDetector
from mcr.telemetry import setup as setup_telemetry

log = logging.getLogger("mcr")
OTEL = setup_telemetry()
DEFAULT_SEED = int(os.getenv("MCR_DEFAULT_SEED", "5"))
STATIC_DIR = Path(os.getenv("MCR_STATIC_DIR", Path(__file__).resolve().parents[3] / "frontend" / "dist"))

app = FastAPI(title="Matchday Control Room", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "llm": backend_name(), "telemetry": OTEL}


@app.get("/api/match/{seed}/meta")
def match_meta(seed: int) -> dict:
    return MatchEngine(seed).meta().__dict__


@app.get("/api/match/{seed}/events")
def match_events(seed: int, format: str = Query("json", pattern="^(json|jsonl|csv)$"), frames: bool = False):
    """Export a full synthetic match as a dataset (the 'data innovation' deliverable)."""
    meta, events = generate_match(seed)
    if format == "csv":
        buf = io.StringIO()
        cols = ["id", "t", "minute", "period", "type", "team", "player", "target", "x", "y", "end_x", "end_y",
                "outcome", "attrs"]
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        for e in events:
            d = e.to_dict(with_frame=False)
            d["attrs"] = json.dumps(d["attrs"])
            w.writerow(d)
        return PlainTextResponse(buf.getvalue(), media_type="text/csv")
    rows = [e.to_dict(with_frame=frames) for e in events]
    if format == "jsonl":
        return PlainTextResponse("\n".join(json.dumps(r) for r in rows), media_type="application/x-ndjson")
    return JSONResponse({"meta": meta.__dict__, "events": rows})


class MatchSession:
    """One viewer's live match: engine -> stats -> moments -> crew, paced in (accelerated) real time."""

    def __init__(self, ws: WebSocket, seed: int, speed: float, langs: tuple[str, ...]):
        self.ws = ws
        self.engine = MatchEngine(seed)
        self.state = MatchState(self.engine.meta())
        self.detector = MomentDetector(self.state)
        self.crew = BroadcastCrew(self.state, langs=langs)
        self.moments: list = []
        self.booth = BoothAgent(self.state, self.moments, client=self.crew.client)
        self.booth.slots = self.crew.slots  # one queue for every model call of this process
        self.speed = speed
        self.paused = asyncio.Event()
        self.paused.set()  # set = running
        self.tasks: set[asyncio.Task] = set()
        self.send_lock = asyncio.Lock()

    async def send(self, kind: str, data) -> None:
        async with self.send_lock:
            await self.ws.send_text(json.dumps({"type": kind, "data": data}, ensure_ascii=False))

    async def _crew(self, moment) -> None:
        try:
            out = await self.crew.process(moment)
            await self.send("crew", out.to_dict())
        except Exception:  # pragma: no cover - logged, never kills the stream
            log.exception("crew failed for %s", moment.id)

    async def _ask(self, msg: dict) -> None:
        qid = str(msg.get("id") or "")
        try:
            out = await self.booth.ask(str(msg.get("question", "")), str(msg.get("lang", "en")), qid or None)
            await self.send("answer", out.to_dict())
        except Exception:  # pragma: no cover - logged, never kills the stream
            log.exception("booth failed")
            await self.send("answer", {"id": qid, "question": str(msg.get("question", "")), "language": "en",
                                       "answer": "", "mode": "planned", "status": "fallback", "latency_ms": 0,
                                       "tools": [], "note": "booth error"})

    async def controls(self) -> None:
        while True:
            try:
                msg = json.loads(await self.ws.receive_text())
            except json.JSONDecodeError:
                continue  # ignore malformed control messages instead of dropping the viewer
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "speed":
                try:
                    self.speed = max(1.0, min(240.0, float(msg.get("value", self.speed))))
                except (TypeError, ValueError):
                    pass
            elif msg.get("type") == "pause":
                self.paused.clear()
            elif msg.get("type") == "resume":
                self.paused.set()
            elif msg.get("type") == "ask" and str(msg.get("question", "")).strip():
                task = asyncio.create_task(self._ask(msg))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

    async def run(self) -> None:
        await self.send("meta", {**self.engine.meta().__dict__, "llm": self.crew.model_label,
                                 "langs": list(self.crew.langs)})
        last_t = 0.0
        for i, ev in enumerate(self.engine.events()):
            await self.paused.wait()
            dt = ev.t - last_t if ev.t >= last_t else 0.0
            last_t = ev.t
            await asyncio.sleep(min(1.5, dt / self.speed))
            self.state.update(ev)
            await self.send("event", ev.to_dict())
            for m in self.detector.detect(ev):
                self.moments.append(m)
                await self.send("moment", m.to_dict())
                task = asyncio.create_task(self._crew(m))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
            if i % 8 == 0 or ev.type in ("shot", "half_time", "full_time", "kickoff"):
                await self.send("state", self.state.snapshot())
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.send("state", self.state.snapshot())
        await self.send("end", {"score_line": self.state.score_line()})


@app.websocket("/ws/match")
async def ws_match(ws: WebSocket, seed: int = DEFAULT_SEED, speed: float = 30.0, langs: str = "en,tr"):
    await ws.accept()
    lang_tuple = tuple(x for x in langs.split(",") if x) or ("en",)
    session = MatchSession(ws, seed, speed, lang_tuple)
    control = asyncio.create_task(session.controls())
    try:
        await session.run()
        await control  # keep the socket open after full time so viewers can still ask the booth
    except WebSocketDisconnect:
        pass
    finally:
        control.cancel()
        for t in list(session.tasks):
            t.cancel()


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = STATIC_DIR / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(STATIC_DIR / "index.html")
