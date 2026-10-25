"""Evaluation harness: measures the analytics, the guard and the agents with numbers, not vibes.

    python -m mcr.eval                         # offline backend, writes eval/results-offline.{md,json}
    MCR_LLM=foundry_local python -m mcr.eval --moments 12

Four sections:
  1. Tactical-switch detection: Riverside silently switch to a high press at 58' in every seed.
     Does the analytics find it, and how many minutes later?
  2. FactGuard probe: grounded lines must pass; the same lines with an invented number, or with a
     real number nudged off by a little, must be rejected.
  3. Crew run: every narrated moment of one match through the configured model, with the cache off.
     Status of each agent call (ok / retried / fallback) and latency.
  4. Booth: fixed viewer questions with the tools a good answer needs. Grounded answers, tool choice
     and latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from mcr.agents import templates
from mcr.agents.booth import BoothAgent
from mcr.agents.cache import ResponseCache
from mcr.agents.clients import backend_name
from mcr.agents.crew import BroadcastCrew
from mcr.agents.guard import FactGuard
from mcr.sim.engine import MatchEngine
from mcr.sim.teams import default_teams
from mcr.stats.engine import MatchState
from mcr.stats.moments import MomentDetector

RIVERSIDE = default_teams()[1]
SWITCH_MINUTE, _ = RIVERSIDE.switch

BOOTH_QUESTIONS = [
    ("Why is Riverside pressing so much?", "en", {"team_stats"}),
    ("Riverside neden bu kadar baskı yapıyor?", "tr", {"team_stats"}),
    ("Who is on top right now?", "en", {"match_overview"}),
    ("Şu an hangi takım üstün?", "tr", {"match_overview"}),
    ("Who has run the most?", "en", {"top_players"}),
    ("How is Dario Vance playing?", "en", {"player_stats"}),
    ("Son golü anlat", "tr", {"key_moments"}),
    ("How many chances has Northbridge created?", "en", {"team_stats"}),
]


def replay(seed: int):
    engine = MatchEngine(seed)
    state = MatchState(engine.meta())
    det = MomentDetector(state)
    for ev in engine.events():
        state.update(ev)
        for m in det.detect(ev):
            yield state, m


def pct(a: int, b: int) -> float:
    return round(100 * a / b, 1) if b else 0.0


def p(values: list[float], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return int(s[min(len(s) - 1, int(q * len(s)))])


# ---------------------------------------------------------------- 1. analytics
def eval_switch(seeds: range) -> dict:
    rows = []
    for seed in seeds:
        found = None
        for _, m in replay(seed):
            if m.type == "pressure_surge" and m.team == RIVERSIDE.id and m.minute >= SWITCH_MINUTE - 2:
                found = m
                break
        rows.append({"seed": seed, "detected": found is not None,
                     "minute": found.minute if found else None,
                     "delay_min": found.minute - SWITCH_MINUTE if found else None,
                     "ratio": found.facts.get("ratio") if found else None})
    delays = [r["delay_min"] for r in rows if r["detected"]]
    return {"seeds": len(rows), "detected": sum(r["detected"] for r in rows),
            "median_delay_min": statistics.median(delays) if delays else None, "rows": rows}


# ---------------------------------------------------------------- 2. guard
def eval_guard(seed: int) -> dict:
    rng = random.Random(7)
    guard = FactGuard()
    clean = caught_invented = caught_nudged = n = n_nudged = collisions = 0
    misses: list[str] = []
    for _, m in replay(seed):
        if not m.narrate or m.type in ("half_time", "full_time"):
            continue
        for lang in ("en", "tr"):
            for text in (templates.narrate(m.type, m.facts, lang), templates.explain(m.type, m.facts, lang)):
                n += 1
                clean += guard.check(text, m.facts, [m.minute]).ok
                invented = text + (f" Bu sezon {rng.randint(11, 60)}. golü." if lang == "tr"
                                   else f" He has {rng.randint(11, 60)} goals this season.")
                hit = not guard.check(invented, m.facts, [m.minute]).ok
                caught_invented += hit
                collisions += not hit  # the invented number happens to equal a real one in the facts
                nums = [v for v in m.facts.values() if isinstance(v, float) and 0.05 < v < 50]
                if nums:
                    v = nums[0]
                    txt = templates.num(v, lang)
                    if txt in text:
                        n_nudged += 1
                        nudged = text.replace(txt, templates.num(round(v * 1.3 + 0.07, 2), lang), 1)
                        bad = guard.check(nudged, m.facts, [m.minute])
                        caught_nudged += not bad.ok
                        if bad.ok and len(misses) < 3:
                            misses.append(nudged)
    return {"lines": n, "clean_pass_pct": pct(clean, n), "invented_caught_pct": pct(caught_invented, n),
            "invented_collisions": collisions,
            "nudged_lines": n_nudged, "nudged_caught_pct": pct(caught_nudged, n_nudged), "nudged_misses": misses}


# ---------------------------------------------------------------- 3. crew
async def eval_crew(seed: int, limit: int | None) -> dict:
    statuses: dict[str, Counter] = defaultdict(Counter)
    latency: dict[str, list[float]] = defaultdict(list)
    guard_rejections = 0
    crew = None
    done = 0
    t0 = time.perf_counter()
    for state, m in replay(seed):
        if crew is None:
            crew = BroadcastCrew(state, langs=("en", "tr"))
            crew.cache = ResponseCache(None)  # measure the model, not the cache
        if not m.narrate:
            continue
        out = await crew.process(m)
        for s in out.trace:
            if s["agent"] == "producer":
                continue
            statuses[s["agent"]][s["status"]] += 1
            if s["status"] in ("ok", "retried"):
                latency[s["agent"]].append(s["latency_ms"])
            if "not in the facts" in (s["note"] or ""):
                guard_rejections += 1
        done += 1
        if limit and done >= limit:
            break
    agents = {}
    for a, c in statuses.items():
        total = sum(c.values())
        agents[a] = {"calls": total, "ok_pct": pct(c["ok"], total), "retried_pct": pct(c["retried"], total),
                     "fallback_pct": pct(c["fallback"], total), "p50_ms": p(latency[a], 0.5),
                     "p95_ms": p(latency[a], 0.95)}
    total = sum(sum(c.values()) for c in statuses.values())
    grounded = sum(c["ok"] + c["retried"] for c in statuses.values())
    return {"moments": done, "calls": total, "model_answer_pct": pct(grounded, total),
            "guard_rejections": guard_rejections, "wall_s": round(time.perf_counter() - t0, 1), "agents": agents}


# ---------------------------------------------------------------- 4. booth
async def eval_booth(seed: int) -> dict:
    state = moments = None
    collected = []
    for state, m in replay(seed):
        collected.append(m)
    booth = BoothAgent(state, collected)
    rows = []
    for q, lang, expected in BOOTH_QUESTIONS:
        a = await booth.ask(q, lang)
        used = {t["name"] for t in a.tools}
        rows.append({"question": q, "status": a.status, "mode": a.mode, "tools": sorted(used),
                     "tool_match": expected <= used, "latency_ms": a.latency_ms, "answer": a.answer})
    n = len(rows)
    return {"mode": rows[0]["mode"] if rows else None, "questions": n,
            "model_answer_pct": pct(sum(r["status"] in ("ok", "retried") for r in rows), n),
            "tool_match_pct": pct(sum(r["tool_match"] for r in rows), n),
            "p50_ms": p([r["latency_ms"] for r in rows], 0.5), "rows": rows}


# ---------------------------------------------------------------- report
def to_markdown(r: dict) -> str:
    s, g, c, b = r["switch"], r["guard"], r["crew"], r["booth"]
    lines = [
        f"# Evaluation: `{r['backend']}`",
        "",
        f"Seed {r['seed']}, generated by `python -m mcr.eval`.",
        "",
    ]
    if r["backend"] == "offline":
        lines += ["> Offline backend: the agents answer with the template client, so the crew and booth sections "
                  "check the pipeline, not a model. Run `MCR_LLM=foundry_local python -m mcr.eval` for model numbers.",
                  ""]
    lines += [
        "## Analytics: hidden tactical switch",
        f"Riverside switch to a high press at {SWITCH_MINUTE}' without any event saying so. Detected in "
        f"**{s['detected']}/{s['seeds']} seeds**, median delay **{s['median_delay_min']} min**.",
        "",
        "## FactGuard",
        "| Probe | Result |",
        "|---|---|",
        f"| Grounded lines that pass | {g['clean_pass_pct']}% of {g['lines']} |",
        f"| Invented number caught | {g['invented_caught_pct']}% |",
        f"| Real number nudged by ~30% caught | {g['nudged_caught_pct']}% of {g['nudged_lines']} |",
        "",
        f"Every invented number the guard let through ({g['invented_collisions']}) happened to equal a real number "
        "in that moment's facts, which no number check can tell apart.",
        "",
        f"## Crew ({c['moments']} narrated moments, {c['calls']} agent calls, cache off, {c['wall_s']} s)",
        f"Model answers that aired: **{c['model_answer_pct']}%**. FactGuard rejected {c['guard_rejections']} "
        "model answers before they aired.",
        "",
        "| Agent | Calls | ok | retried | fallback | p50 | p95 |",
        "|---|---|---|---|---|---|---|",
    ]
    for a, v in sorted(c["agents"].items()):
        lines.append(f"| {a} | {v['calls']} | {v['ok_pct']}% | {v['retried_pct']}% | {v['fallback_pct']}% | "
                     f"{v['p50_ms']} ms | {v['p95_ms']} ms |")
    lines += [
        "",
        f"## Ask the booth ({b['questions']} questions, mode `{b['mode']}`)",
        f"Model answers: **{b['model_answer_pct']}%**, right tools used: **{b['tool_match_pct']}%**, "
        f"p50 latency {b['p50_ms']} ms.",
        "",
        "| Question | Status | Tools |",
        "|---|---|---|",
    ]
    for row in b["rows"]:
        lines.append(f"| {row['question']} | {row['status']} | {', '.join(row['tools'])} |")
    return "\n".join(lines) + "\n"


async def run(seed: int, seeds: int, limit: int | None) -> dict:
    return {
        "backend": backend_name(), "seed": seed,
        "switch": eval_switch(range(1, seeds + 1)),
        "guard": eval_guard(seed),
        "crew": await eval_crew(seed, limit),
        "booth": await eval_booth(seed),
    }


def main() -> None:
    from mcr.telemetry import setup

    setup()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=20, help="seeds for the tactical-switch check")
    ap.add_argument("--moments", type=int, default=None, help="limit narrated moments (slow local models)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[3] / "eval"))
    args = ap.parse_args()
    result = asyncio.run(run(args.seed, args.seeds, args.moments))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    md = to_markdown(result)
    (out / f"results-{result['backend']}.md").write_text(md)
    (out / f"results-{result['backend']}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(md)


if __name__ == "__main__":
    main()
