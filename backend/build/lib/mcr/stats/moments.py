"""Key-moment detection: turns aggregated state into candidate story beats with evidence.

Each Moment carries `facts` (the only numbers agents are allowed to quote) and `evidence`
(metric vs. baseline) so the "why it matters" explanation is grounded and checkable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from mcr.sim.engine import MatchEvent
from mcr.stats.engine import ROCKET_KMH, SPRINT_KMH, MatchState

IMPORTANCE = {
    "goal": 1.0,
    "big_chance": 0.8,
    "pressure_surge": 0.75,
    "momentum_shift": 0.7,
    "chaos_shift": 0.6,
    "elite_pass": 0.55,
    "milestone": 0.4,
    "rocket_shot": 0.4,
    "sprint": 0.3,
    "half_time": 0.9,
    "full_time": 1.0,
}

# Moments below this importance become lightweight overlays (player tags) and skip the LLM agents.
NARRATE_THRESHOLD = 0.5


@dataclass
class Moment:
    id: str
    type: str
    minute: int
    t: float
    importance: float
    team: str | None = None
    player: str | None = None
    facts: dict = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    event_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def narrate(self) -> bool:
        return self.importance >= NARRATE_THRESHOLD


class MomentDetector:
    def __init__(self, state: MatchState):
        self.s = state
        self._cooldown: dict[str, float] = {}
        self._last_check_minute = 0
        self._prev_momentum_sign = 0
        self._prev_chaos_label = "balanced"
        self._milestones: set[str] = set()
        self._n = 0

    def _ready(self, key: str, seconds: float) -> bool:
        last = self._cooldown.get(key)
        if last is not None and self.s.clock - last < seconds:
            return False
        self._cooldown[key] = self.s.clock
        return True

    def _moment(self, type_: str, ev: MatchEvent | None, **kw) -> Moment:
        self._n += 1
        return Moment(
            id=f"m{self._n}",
            type=type_,
            minute=self.s.minute,
            t=self.s.clock,
            importance=IMPORTANCE[type_],
            event_id=ev.id if ev else None,
            **kw,
        )

    def _pname(self, pid: str | None) -> str | None:
        return self.s.players[pid].name if pid else None

    def _tname(self, tid: str | None) -> str | None:
        return self.s.teams[tid].name if tid else None

    def detect(self, ev: MatchEvent) -> list[Moment]:
        s = self.s
        out: list[Moment] = []

        if ev.type == "shot":
            xg = ev.attrs.get("xg", 0.0)
            speed = ev.attrs.get("shot_speed_kmh", 0.0)
            lp = s.last_completed_pass
            assist = lp if lp and lp.target == ev.player and lp.team == ev.team and ev.t - lp.t < 15 else None
            base = {
                "player": self._pname(ev.player),
                "team": self._tname(ev.team),
                "minute": ev.minute,
                "xg": round(xg, 2),
                "shot_speed_kmh": round(speed),
                "distance_m": round(ev.attrs.get("distance_m", 0.0)),
                "score_line": s.score_line(),
                "team_xg": round(s.teams[ev.team].xg, 2),
                "opponent_xg": round(s.teams[s.other(ev.team)].xg, 2),
                "chain_passes": s.chain_passes,
            }
            if assist:
                base["assist"] = self._pname(assist.player)
                base["assist_difficulty"] = assist.attrs.get("difficulty")
            if ev.outcome == "goal":
                evidence = [{"metric": "xG of the chance", "value": round(xg, 2)}]
                if xg < 0.1:
                    evidence.append({"metric": "low-probability finish", "value": round(xg, 2), "baseline": 0.1})
                out.append(self._moment("goal", ev, team=ev.team, player=ev.player, facts=base, evidence=evidence))
            elif xg >= 0.2 and self._ready(f"chance:{ev.team}", 60):
                base["outcome"] = ev.outcome
                out.append(self._moment("big_chance", ev, team=ev.team, player=ev.player, facts=base,
                                        evidence=[{"metric": "xG", "value": round(xg, 2), "baseline": 0.1}]))
            if speed >= ROCKET_KMH and self._ready("rocket", 240):
                out.append(self._moment("rocket_shot", ev, team=ev.team, player=ev.player,
                                        facts={"player": base["player"], "shot_speed_kmh": round(speed)}))

        elif ev.type == "pass" and ev.outcome == "success":
            diff = ev.attrs.get("difficulty", 0.0)
            if diff >= 8.0 and self._ready("elite_pass", 8 * 60):
                out.append(self._moment(
                    "elite_pass", ev, team=ev.team, player=ev.player,
                    facts={"player": self._pname(ev.player), "receiver": self._pname(ev.target),
                           "team": self._tname(ev.team), "difficulty": diff,
                           "distance_m": round(ev.attrs.get("distance_m", 0.0)),
                           "under_pressure": bool(ev.attrs.get("under_pressure")),
                           "progression_m": round(ev.attrs.get("progression_m", 0.0))},
                    evidence=[{"metric": "pass difficulty (0-10)", "value": diff,
                               "baseline": s.players[ev.player].to_dict()["avg_pass_difficulty"]}],
                ))
            p = s.players[ev.player]
            if p.passes_completed == 50 and f"50p:{p.id}" not in self._milestones:
                self._milestones.add(f"50p:{p.id}")
                out.append(self._moment("milestone", ev, team=ev.team, player=ev.player,
                                        facts={"player": p.name, "milestone": "50 completed passes",
                                               "pass_accuracy": p.to_dict()["pass_accuracy"]}))

        elif ev.type == "carry":
            speed = ev.attrs.get("carrier_speed_kmh", 0.0)
            if speed >= SPRINT_KMH and self._ready(f"sprint:{ev.player}", 300):
                out.append(self._moment("sprint", ev, team=ev.team, player=ev.player,
                                        facts={"player": self._pname(ev.player), "speed_kmh": round(speed, 1)}))

        elif ev.type in ("half_time", "full_time"):
            snap = s.snapshot()
            out.append(self._moment(ev.type, ev, facts={
                "score_line": s.score_line(),
                "possession": snap["possession"],
                "xg": {tid: t["xg"] for tid, t in snap["teams"].items()},
                "shots": {tid: t["shots"] for tid, t in snap["teams"].items()},
            }))

        # Once per match minute: trend detectors over the rolling window.
        if s.minute != self._last_check_minute and ev.type not in ("half_time", "full_time"):
            self._last_check_minute = s.minute
            out.extend(self._trend_checks(ev))
        return out

    def _trend_checks(self, ev: MatchEvent) -> list[Moment]:
        s = self.s
        out: list[Moment] = []
        period_start = 45 * 60 if s.period == 2 else 0
        if s.clock - period_start < 8 * 60:
            return out  # windows need time to fill after kick-off

        for tid in s.teams:
            now = s.pressure_rate(tid)
            base = s.baseline_pressure_rate(tid, s.clock - 300)
            if s.minute >= 15 and base > 0 and now >= 1.8 * base and now >= 1.6 and self._ready(f"press:{tid}", 12 * 60):
                forced = s.window.total("turnover", s.other(tid))
                out.append(self._moment(
                    "pressure_surge", ev, team=tid,
                    facts={"team": s.teams[tid].name, "pressures_per_min": now, "baseline_per_min": base,
                           "ratio": round(now / base, 1), "turnovers_forced_5min": int(forced),
                           "score_line": s.score_line()},
                    evidence=[{"metric": "pressures per minute (last 5 min)", "value": now, "baseline": base}],
                ))

        m = s.momentum()
        sign = 1 if m > 30 else (-1 if m < -30 else 0)
        if sign and self._prev_momentum_sign and sign != self._prev_momentum_sign and self._ready("momentum", 10 * 60):
            tid = s.home_id if sign > 0 else s.away_id
            out.append(self._moment(
                "momentum_shift", ev, team=tid,
                facts={"team": s.teams[tid].name, "momentum": abs(m), "score_line": s.score_line(),
                       "xg_5min": round(s.window.total("xg", tid), 2),
                       "final_third_entries": s.teams[tid].final_third_entries},
                evidence=[{"metric": "momentum index (-100..100, home +)", "value": m}],
            ))
        if sign:
            self._prev_momentum_sign = sign

        cc = s.control_chaos()
        if cc["label"] != "balanced" and cc["label"] != self._prev_chaos_label and self._ready("chaos", 15 * 60):
            out.append(self._moment(
                "chaos_shift", ev,
                facts={"regime": cc["label"], "index": cc["index"], "turnovers_per_min": cc["turnovers_per_min"],
                       "long_pass_share": cc["long_pass_share"], "avg_chain_passes": cc["avg_chain_passes"],
                       "score_line": s.score_line()},
                evidence=[{"metric": "control vs chaos index (0..100)", "value": cc["index"]}],
            ))
        if cc["label"] != "balanced":
            self._prev_chaos_label = cc["label"]
        return out
