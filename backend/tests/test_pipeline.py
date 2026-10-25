import asyncio
import json
from collections import Counter

import pytest
from agent_framework import BaseChatClient, ChatResponse, Message

from mcr.agents.crew import BroadcastCrew
from mcr.agents.guard import FactGuard
from mcr.sim.engine import generate_match, MatchEngine
from mcr.stats.engine import MatchState, pass_difficulty
from mcr.stats.moments import MomentDetector
from mcr.stats.xg import shot_xg


def run_match(seed=5):
    engine = MatchEngine(seed)
    state = MatchState(engine.meta())
    det = MomentDetector(state)
    moments = []
    for ev in engine.events():
        state.update(ev)
        moments.extend(det.detect(ev))
    return state, moments


def test_simulator_is_deterministic():
    _, a = generate_match(11)
    _, b = generate_match(11)
    assert [e.to_dict() for e in a] == [e.to_dict() for e in b]


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_simulator_is_football_realistic(seed):
    state, _ = run_match(seed)
    teams = state.teams.values()
    total_passes = sum(t.passes for t in teams)
    total_shots = sum(t.shots for t in teams)
    goals = sum(t.goals for t in teams)
    assert 700 <= total_passes <= 1300
    assert 12 <= total_shots <= 45
    assert 0 <= goals <= 8
    for t in teams:
        assert 65 <= t.to_dict()["pass_accuracy"] <= 92
    assert state.finished


def test_xg_model_shape():
    assert shot_xg(100, 34) > shot_xg(94, 34) > shot_xg(85, 34) > shot_xg(70, 34)
    assert shot_xg(95, 34) > shot_xg(95, 5)
    assert 0.1 < shot_xg(94, 34) < 0.35


def test_hidden_tactical_switch_is_detected():
    """Riverside switch to a high press at 58' silently; analytics must surface it from the data."""
    _, moments = run_match(5)
    surges = [m for m in moments if m.type == "pressure_surge" and m.team == "RVA"]
    assert surges, "expected a Riverside pressure surge to be detected"
    assert any(m.facts["ratio"] >= 1.8 for m in surges)


def test_moments_carry_grounded_facts():
    _, moments = run_match(5)
    types = Counter(m.type for m in moments)
    assert types["goal"] >= 1 and types["full_time"] == 1
    for m in moments:
        if m.type == "goal":
            assert {"player", "xg", "score_line"} <= m.facts.keys()


def test_guard_rejects_invented_numbers():
    g = FactGuard()
    facts = {"xg": 0.65, "minute": 70, "score_line": "NBR 0-1 RVA"}
    assert g.check("A 0.65 xG chance in minute 70, NBR 0-1 RVA", facts).ok
    assert g.check("0,65 xG'lik pozisyon", facts).ok  # Turkish decimal comma
    bad = g.check("His 14th goal this season from 0.65 xG", facts)
    assert not bad.ok and bad.unknown_numbers == ["14"]


def test_pass_difficulty_bounds():
    _, events = generate_match(3)
    vals = [pass_difficulty(e, e.team == "NBR") for e in events if e.type == "pass"]
    assert min(vals) >= 0 and max(vals) <= 10


class FlakyClient(BaseChatClient):
    """Hallucinates numbers on the first call of each prompt and times out on the persona agent."""

    def __init__(self):
        super().__init__()
        self.calls = Counter()

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        payload = json.loads(messages[-1].text)
        self.calls[payload["task"]] += 1
        if payload["task"] == "personas":
            await asyncio.sleep(5)
        if payload["task"] == "explain" and "feedback" not in payload:
            text = "He has now scored 27 goals in 31 games."
        else:
            text = "A big moment in the match."
        return ChatResponse(messages=Message("assistant", [text]))


async def test_crew_recovers_from_hallucination_and_timeouts():
    state, moments = run_match(5)
    goal = next(m for m in moments if m.type == "goal")
    crew = BroadcastCrew(state, langs=("en",), client=FlakyClient(), timeout_s=0.3)
    out = await crew.process(goal)
    steps = {(t["agent"], t["status"]) for t in out.trace}
    assert ("analyst", "retried") in steps  # guard caught "27"/"31" and the retry passed
    assert ("persona", "fallback") in steps  # timed out twice, templated fallback used
    assert out.cards["casual"]["en"]  # the broadcast still has content
    assert "27" not in out.why


async def test_offline_crew_end_to_end():
    state, moments = run_match(5)
    crew = BroadcastCrew(state, langs=("en", "tr"))
    outs = [await crew.process(m) for m in moments]
    assert all(o.narration["tr"] and o.narration["en"] for o in outs)
    ft = outs[-1]
    assert ft.recap and ft.recap["tr"]["headline"].startswith("Maç sonu")
    assert all(t["status"] != "fallback" for o in outs for t in o.trace)


class CountingClient(BaseChatClient):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls += 1
        from mcr.agents.clients import render_offline
        return ChatResponse(messages=Message("assistant", [render_offline(json.loads(messages[-1].text))]))


async def test_validated_answers_are_cached_and_reused(tmp_path):
    from mcr.agents.cache import ResponseCache

    state, moments = run_match(5)
    goal = next(m for m in moments if m.type == "goal")
    client = CountingClient()
    crew = BroadcastCrew(state, langs=("en", "tr"), client=client)
    crew.cache = ResponseCache(tmp_path / "cache.json")
    await crew.process(goal)
    first = client.calls
    crew2 = BroadcastCrew(state, langs=("en", "tr"), client=client)
    crew2.cache = ResponseCache(tmp_path / "cache.json")
    out = await crew2.process(goal)
    assert first == 5 and client.calls == first
    assert {t["status"] for t in out.trace if t["agent"] != "producer"} == {"cached"}


async def test_rate_limiter_spaces_calls():
    import time
    from mcr.agents.cache import RateLimiter

    lim = RateLimiter(rpm=600)  # 0.1 s apart
    t0 = time.monotonic()
    await asyncio.gather(*(lim.wait() for _ in range(4)))
    assert time.monotonic() - t0 >= 0.29


# ---------------------------------------------------------------- ask the booth
from agent_framework import Content, FunctionInvocationLayer  # noqa: E402

from mcr.agents.booth import BoothAgent  # noqa: E402


async def test_booth_answers_from_tools_offline():
    state, moments = run_match(5)
    booth = BoothAgent(state, moments, native=False)
    a = await booth.ask("Riverside neden bu kadar baskı yapıyor?", "tr")
    assert a.status == "ok" and a.mode == "planned"
    assert [t["name"] for t in a.tools] == ["team_stats", "key_moments"]
    assert a.tools[0]["args"] == {"team": "RVA"}
    assert "Riverside" in a.answer and FactGuard().check(a.answer, {"t": [t["result"] for t in a.tools]}).ok


class ToolCallingClient(FunctionInvocationLayer, BaseChatClient):
    """Behaves like a hosted model with function calling: asks for team_stats, then answers from its result."""

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        results = [c for m in messages for c in m.contents if c.type == "function_result"]
        if not results:
            call = Content.from_function_call(call_id="c1", name="team_stats", arguments={"team": "Riverside"})
            return ChatResponse(messages=Message("assistant", [call]))
        data = json.loads(results[-1].result) if isinstance(results[-1].result, str) else results[-1].result
        if isinstance(data, list):  # some versions wrap tool output in content items
            data = json.loads(data[0].text)
        return ChatResponse(messages=Message("assistant", [
            f"Riverside are pressing {data['pressures_per_min_now']} times a minute."]))


async def test_booth_native_tool_calling():
    state, moments = run_match(5)
    booth = BoothAgent(state, moments, client=ToolCallingClient(), native=True)
    a = await booth.ask("Why is Riverside pressing?", "en")
    assert a.mode == "native" and a.status == "ok", a.note
    assert a.tools and a.tools[0]["name"] == "team_stats"
    assert str(a.tools[0]["result"]["pressures_per_min_now"]) in a.answer


class InventingClient(BaseChatClient):
    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        return ChatResponse(messages=Message("assistant", ["Riverside have made 347 pressures, a league record."]))


async def test_booth_rejects_invented_numbers():
    state, moments = run_match(5)
    a = await BoothAgent(state, moments, client=InventingClient(), native=False).ask("Who is pressing?", "en")
    assert a.status == "fallback" and "347" not in a.answer and "347" in a.note


def test_eval_switch_and_guard_probe():
    from mcr.eval.__main__ import eval_guard, eval_switch

    sw = eval_switch(range(1, 6))
    assert sw["detected"] == 5 and sw["median_delay_min"] <= 6
    g = eval_guard(5)
    assert g["clean_pass_pct"] == 100.0 and g["invented_caught_pct"] >= 85 and g["nudged_caught_pct"] >= 85


def test_websocket_booth_question_and_bad_control_messages():
    from fastapi.testclient import TestClient

    from mcr.api.app import app

    with TestClient(app) as client, client.websocket_connect("/ws/match?seed=5&speed=240&langs=en") as ws:
        assert json.loads(ws.receive_text())["type"] == "meta"
        ws.send_text("not json")
        ws.send_text(json.dumps({"type": "speed", "value": "fast"}))
        ws.send_text(json.dumps({"type": "pause"}))
        ws.send_text(json.dumps({"type": "ask", "question": "Who is on top right now?", "lang": "en", "id": "q1"}))
        for _ in range(500):
            msg = json.loads(ws.receive_text())
            if msg["type"] == "answer":
                break
        assert msg["data"]["id"] == "q1" and msg["data"]["status"] == "ok"
        assert [t["name"] for t in msg["data"]["tools"]][0] == "match_overview"
