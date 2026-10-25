"""Run a whole match through the crew at the model's pace and cache every validated answer.

    MCR_LLM=foundry_local python -m mcr.agents.prewarm --seed 5

Afterwards the live demo replays real model output at any playback speed.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from mcr.agents.crew import BroadcastCrew
from mcr.sim.engine import MatchEngine
from mcr.stats.engine import MatchState
from mcr.stats.moments import MomentDetector


async def prewarm(seed: int, langs: tuple[str, ...]) -> Counter:
    engine = MatchEngine(seed)
    state = MatchState(engine.meta())
    detector = MomentDetector(state)
    crew = BroadcastCrew(state, langs=langs)
    statuses: Counter = Counter()
    for ev in engine.events():
        state.update(ev)
        for m in detector.detect(ev):
            out = await crew.process(m)
            for step in out.trace:
                if step["agent"] != "producer":
                    statuses[step["status"]] += 1
            if m.narrate:
                print(f"{m.minute:>3}' {m.type:<15} {out.narration[langs[0]]}")
    return statuses


def main() -> None:
    from mcr.telemetry import setup

    setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--langs", default="en,tr")
    args = ap.parse_args()
    statuses = asyncio.run(prewarm(args.seed, tuple(args.langs.split(","))))
    print("agent call statuses:", dict(statuses))


if __name__ == "__main__":
    main()
