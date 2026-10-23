"""FactGuard: rejects agent text that quotes numbers not present in the moment's facts.

LLMs are good at tone and bad at arithmetic. On a live broadcast a wrong number is worse than no
number, so every generated line is checked against the deterministic stats before it can air.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUM = re.compile(r"(?<![\w])(\d+(?:[.,]\d+)?)")


def _collect(value, out: set[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        v = abs(float(value))
        out.update({round(v, 2), round(v, 1), float(round(v)), round(v * 100, 1), round(v * 100)})
    elif isinstance(value, str):
        for m in _NUM.findall(value):
            out.add(round(float(m.replace(",", ".")), 2))
    elif isinstance(value, dict):
        for v in value.values():
            _collect(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect(v, out)


@dataclass
class GuardResult:
    ok: bool
    unknown_numbers: list[str]

    def feedback(self) -> str:
        return (
            "Your previous answer quoted numbers that are not in the facts: "
            + ", ".join(self.unknown_numbers)
            + ". Rewrite it using only numbers that appear in the facts."
        )


class FactGuard:
    # Small integers are safe in prose ("one of two", "five minutes", "10 out of 10").
    SAFE = {float(i) for i in range(0, 11)} | {45.0, 90.0, 100.0}

    def check(self, text: str, facts: dict, extra_allowed: list | None = None) -> GuardResult:
        allowed: set[float] = set(self.SAFE)
        _collect(facts, allowed)
        if extra_allowed:
            _collect(extra_allowed, allowed)
        unknown = []
        for raw in _NUM.findall(text):
            v = round(float(raw.replace(",", ".")), 2)
            if v in allowed or round(v, 1) in allowed or float(round(v)) in allowed and v == round(v):
                continue
            unknown.append(raw)
        return GuardResult(ok=not unknown, unknown_numbers=unknown)
