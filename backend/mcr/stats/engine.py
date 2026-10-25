"""Incremental match-state aggregation and explainable metrics.

Everything here is deterministic Python: the agents narrate and explain, but numbers always come
from this module so they can be fact-checked.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

from mcr.sim.engine import MatchEvent, MatchMeta

WINDOW_S = 5 * 60  # rolling window for momentum / pressure / chaos
SPRINT_KMH = 32.0
ROCKET_KMH = 110.0


def zone_value(rel_x: float, y: float) -> float:
    """Simple expected-threat style value of a location, 0..1 (from the attacker's view)."""
    progress = max(0.0, min(1.0, rel_x / 105.0))
    central = 1 - min(1.0, abs(y - 34) / 34)
    return round((progress ** 3) * (0.55 + 0.45 * central), 4)


def pass_difficulty(ev: MatchEvent, team_is_home: bool) -> float:
    """0..10 rating: longer, forward, pressured passes into the box are harder."""
    a = ev.attrs
    dist = a.get("distance_m", 0.0)
    prog = max(0.0, a.get("progression_m", 0.0))
    end_rel = ev.end_x if team_is_home else 105 - ev.end_x
    in_box = end_rel > 88.5 and abs(ev.end_y - 34) < 20.16
    score = 1.5 + min(dist / 9, 3.5) + (2.0 if a.get("under_pressure") else 0) + min(prog / 12, 2.0)
    score += 1.0 if in_box else 0
    score += 0.5 if a.get("height") == "high" else 0
    return round(min(10.0, score), 1)


@dataclass
class TeamStats:
    id: str
    name: str
    goals: int = 0
    passes: int = 0
    passes_completed: int = 0
    shots: int = 0
    shots_on_target: int = 0
    xg: float = 0.0
    possession_s: float = 0.0
    pressures: int = 0
    tackles: int = 0
    interceptions: int = 0
    turnovers: int = 0
    corners: int = 0
    fouls: int = 0
    final_third_entries: int = 0

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["xg"] = round(self.xg, 2)
        d["pass_accuracy"] = round(100 * self.passes_completed / self.passes, 1) if self.passes else 0.0
        return d


@dataclass
class PlayerStats:
    id: str
    name: str
    team: str
    number: int
    role: str
    touches: int = 0
    passes: int = 0
    passes_completed: int = 0
    key_passes: int = 0
    shots: int = 0
    goals: int = 0
    xg: float = 0.0
    tackles: int = 0
    interceptions: int = 0
    pressures: int = 0
    distance_m: float = 0.0
    top_speed_kmh: float = 0.0
    difficulty_sum: float = 0.0

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "difficulty_sum"}
        d["xg"] = round(self.xg, 2)
        d["distance_km"] = round(self.distance_m / 1000, 2)
        d.pop("distance_m")
        d["top_speed_kmh"] = round(self.top_speed_kmh, 1)
        d["pass_accuracy"] = round(100 * self.passes_completed / self.passes, 1) if self.passes else 0.0
        d["avg_pass_difficulty"] = round(self.difficulty_sum / self.passes, 1) if self.passes else 0.0
        return d


@dataclass
class Window:
    """Events within the rolling window, used for momentum, pressure and chaos."""

    items: deque = field(default_factory=deque)

    def add(self, t: float, kind: str, team: str, value: float = 1.0) -> None:
        self.items.append((t, kind, team, value))

    def trim(self, now: float) -> None:
        while self.items and self.items[0][0] < now - WINDOW_S:
            self.items.popleft()

    def total(self, kind: str, team: str | None = None) -> float:
        return sum(v for _, k, tm, v in self.items if k == kind and (team is None or tm == team))


class MatchState:
    def __init__(self, meta: MatchMeta):
        self.meta = meta
        self.home_id = meta.home["id"]
        self.away_id = meta.away["id"]
        self.teams = {
            meta.home["id"]: TeamStats(meta.home["id"], meta.home["name"]),
            meta.away["id"]: TeamStats(meta.away["id"], meta.away["name"]),
        }
        self.players: dict[str, PlayerStats] = {}
        for side in (meta.home, meta.away):
            for p in side["players"]:
                self.players[p["id"]] = PlayerStats(p["id"], p["name"], side["id"], p["number"], p["role"])
        self.window = Window()
        self.clock = 0.0
        self.minute = 1
        self.period = 1
        self.possession_team: str | None = None
        self.last_event: MatchEvent | None = None
        self.last_frame: dict | None = None
        self.chain_team: str | None = None
        self.chain_passes = 0
        self.chain_start_t = 0.0
        self.last_completed_pass: MatchEvent | None = None
        self.momentum_series: list[dict] = []
        self.minute_pressures: dict[str, list[int]] = defaultdict(list)
        self._pressure_this_minute = {self.home_id: 0, self.away_id: 0}
        self.recent: deque[MatchEvent] = deque(maxlen=6)
        self.finished = False

    # ---------------------------------------------------------------- helpers
    def is_home(self, team_id: str | None) -> bool:
        return team_id == self.home_id

    def other(self, team_id: str) -> str:
        return self.away_id if team_id == self.home_id else self.home_id

    def rel_x(self, team_id: str, x: float) -> float:
        return x if self.is_home(team_id) else 105 - x

    def score(self) -> dict:
        return {tid: t.goals for tid, t in self.teams.items()}

    def score_line(self) -> str:
        h, a = self.teams[self.home_id], self.teams[self.away_id]
        return f"{self.meta.home['short']} {h.goals}-{a.goals} {self.meta.away['short']}"

    # ---------------------------------------------------------------- metrics
    def possession_pct(self) -> dict:
        total = sum(t.possession_s for t in self.teams.values()) or 1
        return {tid: round(100 * t.possession_s / total, 1) for tid, t in self.teams.items()}

    def momentum(self) -> float:
        """-100 (away dominant) .. +100 (home dominant) over the rolling window."""
        h = self.window.total("threat", self.home_id) + 3 * self.window.total("xg", self.home_id)
        a = self.window.total("threat", self.away_id) + 3 * self.window.total("xg", self.away_id)
        if h + a < 1e-6:
            return 0.0
        return round(100 * (h - a) / (h + a), 1)

    def pressure_rate(self, team_id: str) -> float:
        """Pressures per minute in the rolling window."""
        span = min(WINDOW_S, max(60.0, self.clock - (45 * 60 if self.period == 2 else 0)))
        return round(self.window.total("pressure", team_id) / (span / 60), 2)

    def control_chaos(self) -> dict:
        """0 = full control, 100 = pure chaos, with the components that produce the number."""
        span_min = min(WINDOW_S, max(60.0, self.clock)) / 60
        turnovers_pm = self.window.total("turnover") / span_min
        pressures_pm = self.window.total("pressure") / span_min
        passes = self.window.total("pass")
        long_share = self.window.total("long_pass") / passes if passes else 0.0
        chain = self.window.total("chain_len") / max(1.0, self.window.total("chain_end"))
        index = 100 * (
            0.4 * min(1.0, turnovers_pm / 5)
            + 0.25 * min(1.0, pressures_pm / 5)
            + 0.2 * min(1.0, long_share / 0.4)
            + 0.15 * (1 - min(1.0, chain / 8))
        )
        return {
            "index": round(index, 1),
            "turnovers_per_min": round(turnovers_pm, 2),
            "pressures_per_min": round(pressures_pm, 2),
            "long_pass_share": round(100 * long_share, 1),
            "avg_chain_passes": round(chain, 1),
            "label": "chaos" if index >= 60 else ("control" if index <= 40 else "balanced"),
        }

    def baseline_pressure_rate(self, team_id: str, before_t: float) -> float:
        """Average pressures per minute from kick-off until `before_t`."""
        minutes = self.minute_pressures[team_id]
        n = max(1, int(before_t // 60))
        vals = minutes[:n]
        return round(sum(vals) / max(1, len(vals)), 2)

    # ---------------------------------------------------------------- update
    def _accumulate_distance(self, frame: dict, dt: float) -> None:
        if self.last_frame:
            for pid, (x, y) in frame["players"].items():
                px, py = self.last_frame["players"].get(pid, (x, y))
                # Frames are sampled per event, so add a jogging baseline for unobserved movement.
                self.players[pid].distance_m += 0.25 * math.dist((x, y), (px, py)) + 1.0 * dt
        self.last_frame = frame

    def _end_chain(self) -> None:
        if self.chain_team is not None:
            self.window.add(self.clock, "chain_len", self.chain_team, self.chain_passes)
            self.window.add(self.clock, "chain_end", self.chain_team)
        self.chain_passes = 0
        self.chain_start_t = self.clock

    def update(self, ev: MatchEvent) -> None:
        dt = max(0.0, ev.t - self.clock) if ev.period == self.period else 0.0
        if self.possession_team and dt:
            self.teams[self.possession_team].possession_s += dt
        # Per-minute pressure bins (for surge baselines)
        while len(self.minute_pressures[self.home_id]) < int(ev.t // 60):
            for tid in (self.home_id, self.away_id):
                self.minute_pressures[tid].append(self._pressure_this_minute[tid])
                self._pressure_this_minute[tid] = 0
        if ev.period != self.period:
            # The second-half clock restarts at 45:00 while first-half stoppage time ran past it,
            # so rolling rates start fresh instead of counting first-half events twice.
            self.window = Window()
        self.clock, self.minute, self.period = ev.t, ev.minute, ev.period
        self.window.trim(self.clock)
        if ev.frame:
            self._accumulate_distance(ev.frame, dt)

        team = self.teams.get(ev.team) if ev.team else None
        player = self.players.get(ev.player) if ev.player else None
        if player and ev.type in ("pass", "carry", "shot"):
            player.touches += 1

        if ev.type == "kickoff":
            self.possession_team = ev.team
            self.chain_team = ev.team
            self._end_chain()
        elif ev.type == "pass":
            team.passes += 1
            player.passes += 1
            diff = pass_difficulty(ev, self.is_home(ev.team))
            ev.attrs["difficulty"] = diff
            player.difficulty_sum += diff
            self.window.add(self.clock, "pass", ev.team)
            if ev.attrs.get("distance_m", 0) > 30:
                self.window.add(self.clock, "long_pass", ev.team)
            if ev.outcome == "success":
                team.passes_completed += 1
                player.passes_completed += 1
                self.chain_passes += 1
                self.last_completed_pass = ev
                gain = zone_value(self.rel_x(ev.team, ev.end_x), ev.end_y) - zone_value(
                    self.rel_x(ev.team, ev.x), ev.y
                )
                if gain > 0:
                    self.window.add(self.clock, "threat", ev.team, gain)
                if self.rel_x(ev.team, ev.x) < 70 <= self.rel_x(ev.team, ev.end_x):
                    team.final_third_entries += 1
        elif ev.type == "carry":
            speed = ev.attrs.get("carrier_speed_kmh", 0.0)
            player.top_speed_kmh = max(player.top_speed_kmh, speed)
            gain = zone_value(self.rel_x(ev.team, ev.end_x), ev.end_y) - zone_value(self.rel_x(ev.team, ev.x), ev.y)
            if gain > 0:
                self.window.add(self.clock, "threat", ev.team, gain)
            if self.rel_x(ev.team, ev.x) < 70 <= self.rel_x(ev.team, ev.end_x):
                team.final_third_entries += 1
        elif ev.type == "shot":
            team.shots += 1
            player.shots += 1
            xg = ev.attrs.get("xg", 0.0)
            team.xg += xg
            player.xg += xg
            self.window.add(self.clock, "xg", ev.team, xg)
            if ev.outcome in ("goal", "saved"):
                team.shots_on_target += 1
            if ev.outcome == "goal":
                team.goals += 1
                player.goals += 1
            lp = self.last_completed_pass
            if lp and lp.target == ev.player and lp.team == ev.team and ev.t - lp.t < 15:
                self.players[lp.player].key_passes += 1
        elif ev.type == "pressure":
            team.pressures += 1
            player.pressures += 1
            self._pressure_this_minute[ev.team] += 1
            self.window.add(self.clock, "pressure", ev.team)
        elif ev.type == "tackle":
            team.tackles += 1
            player.tackles += 1
        elif ev.type == "interception":
            team.interceptions += 1
            player.interceptions += 1
        elif ev.type == "possession_change":
            loser = self.other(ev.team)
            if ev.outcome in ("interception", "tackle", "out_of_play"):
                self.teams[loser].turnovers += 1
                self.window.add(self.clock, "turnover", loser)
            self._end_chain()
            self.possession_team = ev.team
            self.chain_team = ev.team
        elif ev.type == "corner":
            team.corners += 1
        elif ev.type == "foul":
            team.fouls += 1
        elif ev.type == "full_time":
            self.finished = True

        if self.momentum_series and self.momentum_series[-1]["minute"] == self.minute:
            self.momentum_series[-1]["value"] = self.momentum()
        else:
            self.momentum_series.append({"minute": self.minute, "value": self.momentum()})
        self.recent.append(ev)
        self.last_event = ev

    # ---------------------------------------------------------------- views
    def snapshot(self) -> dict:
        top = sorted(self.players.values(), key=lambda p: (p.goals, p.xg + 0.3 * p.key_passes, p.touches),
                     reverse=True)[:6]
        return {
            "minute": self.minute,
            "period": self.period,
            "clock": self.clock,
            "score": self.score(),
            "score_line": self.score_line(),
            "possession": self.possession_pct(),
            "teams": {tid: t.to_dict() for tid, t in self.teams.items()},
            "momentum": self.momentum(),
            "momentum_series": self.momentum_series[-100:],
            "pressure_rate": {tid: self.pressure_rate(tid) for tid in self.teams},
            "control_chaos": self.control_chaos(),
            "top_players": [p.to_dict() for p in top],
            "players": {pid: p.to_dict() for pid, p in self.players.items()},
            "finished": self.finished,
        }

    def player_view(self, player_id: str) -> dict | None:
        p = self.players.get(player_id)
        return p.to_dict() if p else None
