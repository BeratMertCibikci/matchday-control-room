"""Producer: decides what goes on screen, when, and for how long.

A live feed cannot show every insight. The producer owns a single primary overlay slot and a
ticker for minor tags, applies priority and cooldown rules, and records why each decision was
made so the broadcast team (and the judges) can audit it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from mcr.stats.moments import Moment

KIND = {
    "goal": "lower_third",
    "big_chance": "lower_third",
    "elite_pass": "lower_third",
    "pressure_surge": "stat_card",
    "momentum_shift": "stat_card",
    "chaos_shift": "stat_card",
    "milestone": "player_tag",
    "rocket_shot": "player_tag",
    "sprint": "player_tag",
    "half_time": "recap_panel",
    "full_time": "recap_panel",
}

DURATION_S = {  # match-clock seconds an overlay stays up
    "lower_third": 45,
    "stat_card": 60,
    "player_tag": 20,
    "recap_panel": 120,
}


@dataclass
class Overlay:
    id: str
    moment_id: str
    kind: str
    priority: float
    start_t: float
    end_t: float
    team: str | None
    player: str | None
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    moment_id: str
    action: str  # show | replace | ticker | drop
    reason: str
    overlay: Overlay | None = None


class Producer:
    def __init__(self) -> None:
        self.active: Overlay | None = None
        self._n = 0

    def decide(self, m: Moment, payload: dict) -> Decision:
        kind = KIND.get(m.type, "lower_third")
        self._n += 1
        ov = Overlay(
            id=f"o{self._n}", moment_id=m.id, kind=kind, priority=m.importance, start_t=m.t,
            end_t=m.t + DURATION_S[kind], team=m.team, player=m.player, payload=payload,
        )
        if kind == "player_tag":
            return Decision(m.id, "ticker", "minor tag, shown on the player without using the main slot", ov)
        if self.active and self.active.end_t > m.t:
            if m.importance > self.active.priority or kind == "recap_panel":
                prev = self.active
                self.active = ov
                return Decision(m.id, "replace", f"higher priority ({m.importance}) than active {prev.kind} "
                                                 f"({prev.priority})", ov)
            return Decision(m.id, "drop", f"slot busy with a {self.active.kind} of priority "
                                          f"{self.active.priority} until {int(self.active.end_t // 60) + 1}'")
        self.active = ov
        return Decision(m.id, "show", "slot free", ov)
