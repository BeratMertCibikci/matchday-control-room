"""Synthetic, football-realistic match engine.

Produces a deterministic (seeded) stream of match events: passes, carries, shots, tackles,
interceptions, pressure events, possession changes, fouls, set pieces and period markers.
Every event carries a positional frame (ball + 22 players) so a client can animate a 2D pitch.

Coordinate system: x in [0, 105] metres, y in [0, 68] metres. The home team always attacks
towards x = 105 (no side swap at half-time, to keep overlays simple).
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Iterator

from mcr.stats.xg import shot_xg
from mcr.sim.teams import Player, TacticalProfile, Team, default_teams

PITCH_X = 105.0
PITCH_Y = 68.0


@dataclass
class MatchEvent:
    id: int
    t: float  # match clock in seconds
    minute: int
    period: int
    type: str
    team: str | None = None
    player: str | None = None
    target: str | None = None
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    outcome: str | None = None
    attrs: dict = field(default_factory=dict)
    frame: dict | None = None

    def to_dict(self, with_frame: bool = True) -> dict:
        d = asdict(self)
        if not with_frame:
            d.pop("frame", None)
        return d


@dataclass
class MatchMeta:
    seed: int
    home: dict
    away: dict
    venue: str = "Harbourside Stadium (synthetic)"
    competition: str = "Synthetic League"


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class MatchEngine:
    def __init__(self, seed: int = 7, teams: tuple[Team, Team] | None = None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.home, self.away = teams or default_teams()
        self._profiles = {self.home.id: self.home.profile, self.away.id: self.away.profile}
        self._seq = 0
        self.clock = 0.0
        self.period = 1
        self.ball = [52.5, 34.0]
        self.possession: Team = self.home
        self.carrier: Player = self._player_by_role(self.home, "FW")
        self.score = {self.home.id: 0, self.away.id: 0}
        # Actions left in an open-field transition after a turnover (counter-attack window).
        self._transition = 0

    # ------------------------------------------------------------------ helpers
    def meta(self) -> MatchMeta:
        def team_meta(t: Team) -> dict:
            return {
                "id": t.id,
                "name": t.name,
                "short": t.short,
                "color": t.color,
                "formation": t.meta.get("formation"),
                "manager": t.meta.get("manager"),
                "players": [
                    {"id": p.id, "name": p.name, "number": p.number, "role": p.role} for p in t.players
                ],
            }

        return MatchMeta(seed=self.seed, home=team_meta(self.home), away=team_meta(self.away))

    def _other(self, team: Team) -> Team:
        return self.away if team is self.home else self.home

    def _dir(self, team: Team) -> int:
        return 1 if team is self.home else -1

    def _rel_x(self, team: Team, x: float) -> float:
        """Distance travelled from the team's own goal line along its attacking direction."""
        return x if team is self.home else PITCH_X - x

    def _abs_x(self, team: Team, rel: float) -> float:
        return rel if team is self.home else PITCH_X - rel

    def _player_by_role(self, team: Team, role: str) -> Player:
        return next(p for p in team.players if p.role == role)

    def _gk(self, team: Team) -> Player:
        return self._player_by_role(team, "GK")

    def profile(self, team: Team) -> TacticalProfile:
        if team.switch and self.minute >= team.switch[0]:
            return team.switch[1]
        return self._profiles[team.id]

    @property
    def minute(self) -> int:
        return int(self.clock // 60) + 1

    def _frame(self) -> dict:
        players = {}
        for team in (self.home, self.away):
            prof = self.profile(team)
            in_poss = team is self.possession
            ball_rel = self._rel_x(team, self.ball[0])
            shift = (ball_rel - 52.5) * 0.7 + (prof.block_depth - 0.5) * 10 + (10 if in_poss else -4)
            for p in team.players:
                if p.role == "GK":
                    rel = _clamp(4 + max(0.0, ball_rel - 52.5) * 0.15, 2, 18)
                    y = _clamp(34 + (self.ball[1] - 34) * 0.15, 28, 40)
                else:
                    rel = _clamp(p.base_x * 70 + shift, 3, 102)
                    spread = 0.75 + 0.35 * prof.width if in_poss else 0.65 + 0.2 * prof.width
                    y = 34 + (p.base_y - 0.5) * PITCH_Y * spread + (self.ball[1] - 34) * 0.3
                rel += self.rng.uniform(-1.2, 1.2)
                y = _clamp(y + self.rng.uniform(-1.2, 1.2), 1, 67)
                players[p.id] = [round(self._abs_x(team, rel), 2), round(y, 2)]
        if self.carrier:
            players[self.carrier.id] = [round(self.ball[0], 2), round(self.ball[1], 2)]
        return {"ball": [round(self.ball[0], 2), round(self.ball[1], 2)], "players": players}

    def _emit(self, type_: str, **kw) -> MatchEvent:
        self._seq += 1
        ev = MatchEvent(id=self._seq, t=round(self.clock, 1), minute=self.minute, period=self.period, type=type_, **kw)
        ev.frame = self._frame()
        return ev

    def _nearest(self, team: Team, x: float, y: float, frame: dict, exclude: set[str] | None = None) -> Player:
        best, best_d = None, 1e9
        for p in team.players:
            if exclude and p.id in exclude:
                continue
            px, py = frame["players"][p.id]
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best_d:
                best, best_d = p, d
        return best

    def _change_possession(self, team: Team, player: Player, reason: str) -> MatchEvent:
        self.possession = team
        self.carrier = player
        if reason in ("interception", "tackle"):
            # Winning the ball against a high line opens space; direct teams exploit it best.
            opp_depth = self.profile(self._other(team)).block_depth
            self._transition = 3 if self.rng.random() < 0.35 + 0.6 * self.profile(team).directness * opp_depth else 0
        else:
            self._transition = 0
        return self._emit(
            "possession_change", team=team.id, player=player.id, x=self.ball[0], y=self.ball[1], outcome=reason
        )

    def _advance(self, seconds: float) -> None:
        self.clock += max(0.8, seconds)

    # ------------------------------------------------------------------ actions
    def _pressure(self, att: Team, dfn: Team) -> MatchEvent | None:
        prof = self.profile(dfn)
        p_rel = self._rel_x(att, self.ball[0]) / PITCH_X  # 0 = attacker's own goal
        # High-block teams press deep in the opponent's half; everyone presses near their own box.
        zone = 0.55 + prof.block_depth * (1 - p_rel) * 0.9 + 0.45 * p_rel
        prob = _clamp(0.17 * prof.press_intensity * zone, 0.02, 0.6)
        if self._transition:
            prob *= 0.4
        if self.rng.random() >= prob:
            return None
        frame = self._frame()
        presser = self._nearest(dfn, self.ball[0], self.ball[1], frame)
        return self._emit(
            "pressure", team=dfn.id, player=presser.id, target=self.carrier.id, x=self.ball[0], y=self.ball[1],
            attrs={"zone_rel": round(p_rel, 2)},
        )

    def _pass(self, att: Team, dfn: Team, pressured: bool) -> list[MatchEvent]:
        prof = self.profile(att)
        frame = self._frame()
        p_rel = self._rel_x(att, self.ball[0])
        options = []
        for mate in att.players:
            if mate.id == self.carrier.id or (mate.role == "GK" and p_rel > 35):
                continue
            mx, my = frame["players"][mate.id]
            gain = self._rel_x(att, mx) - p_rel
            dist = math.dist((mx, my), self.ball)
            if dist < 4:
                continue
            w = 1.0 + max(0.0, gain) * (0.02 + 0.06 * prof.directness) - max(0.0, -gain) * 0.02
            w *= math.exp(-dist / (18 + 30 * prof.directness))
            options.append((mate, w, mx, my))
        if not options:
            options = [(m, 1.0, *frame["players"][m.id]) for m in att.players if m.id != self.carrier.id]
        total = sum(w for _, w, _, _ in options)
        r = self.rng.random() * total
        for mate, w, mx, my in options:
            r -= w
            if r <= 0:
                break
        end_x = _clamp(mx + self._dir(att) * self.rng.uniform(0, 4), 0.5, PITCH_X - 0.5)
        end_y = _clamp(my + self.rng.uniform(-2.5, 2.5), 0.5, PITCH_Y - 0.5)
        dist = math.dist((end_x, end_y), self.ball)
        gain = self._rel_x(att, end_x) - p_rel
        success_p = self.carrier.passing * (1 - 0.0065 * max(0.0, dist - 15)) * (0.8 if pressured else 1.0)
        success_p *= 1 - 0.12 * max(0.0, gain) / 40
        success_p = _clamp(success_p + 0.1 + (0.1 if self._transition else 0.0), 0.3, 0.97)
        success = self.rng.random() < success_p
        height = "high" if dist > 32 else ("ground" if dist < 20 else self.rng.choice(["ground", "high"]))
        ball_speed = 24 + dist * 1.05 + self.rng.uniform(-4, 6)
        start = list(self.ball)
        passer = self.carrier
        events: list[MatchEvent] = []
        self.ball = [end_x, end_y]
        if success:
            self.carrier = mate
        ev = self._emit(
            "pass", team=att.id, player=passer.id, target=mate.id, x=round(start[0], 2), y=round(start[1], 2),
            end_x=round(end_x, 2), end_y=round(end_y, 2), outcome="success" if success else "fail",
            attrs={
                "distance_m": round(dist, 1),
                "ball_speed_kmh": round(ball_speed, 1),
                "under_pressure": pressured,
                "height": height,
                "progression_m": round(gain, 1),
            },
        )
        events.append(ev)
        self._advance(self.rng.uniform(1.8, 6.0) * prof.tempo)
        if not success:
            if self.rng.random() < 0.78:
                frame = self._frame()
                interceptor = self._nearest(dfn, end_x, end_y, frame)
                self.carrier = interceptor
                events.append(self._emit("interception", team=dfn.id, player=interceptor.id, x=end_x, y=end_y))
                events.append(self._change_possession(dfn, interceptor, "interception"))
            else:
                self.ball = [end_x, _clamp(end_y, 0.0, PITCH_Y)]
                frame = self._frame()
                taker = self._nearest(dfn, end_x, end_y, frame)
                events.append(self._change_possession(dfn, taker, "out_of_play"))
                self._advance(self.rng.uniform(6, 14))
        return events

    def _carry(self, att: Team, dfn: Team, pressured: bool) -> list[MatchEvent]:
        prof = self.profile(att)
        start = list(self.ball)
        length = self.rng.uniform(10, 24) if self._transition else self.rng.uniform(4, 16)
        nx = _clamp(self.ball[0] + self._dir(att) * length, 1, PITCH_X - 1)
        ny = _clamp(self.ball[1] + self.rng.uniform(-6, 6), 1, PITCH_Y - 1)
        speed = self.carrier.pace * self.rng.uniform(0.6, 1.0)
        events: list[MatchEvent] = []
        frame = self._frame()
        defender = self._nearest(dfn, nx, ny, frame)
        tackle_p = 0.11 * (0.5 + defender.tackling) * (1.4 if pressured else 1.0) * (0.6 if self._transition else 1.0)
        carrier = self.carrier
        if self.rng.random() < tackle_p:
            self.ball = [(start[0] + nx) / 2, (start[1] + ny) / 2]
            if self.rng.random() < 0.12:
                events.append(self._emit("foul", team=dfn.id, player=defender.id, target=carrier.id,
                                         x=self.ball[0], y=self.ball[1]))
                self._advance(self.rng.uniform(15, 35))
                return events
            self.carrier = defender
            events.append(self._emit("tackle", team=dfn.id, player=defender.id, target=carrier.id,
                                     x=self.ball[0], y=self.ball[1], outcome="won"))
            events.append(self._change_possession(dfn, defender, "tackle"))
            self._advance(self.rng.uniform(1.5, 3.5))
            return events
        self.ball = [nx, ny]
        events.append(self._emit(
            "carry", team=att.id, player=carrier.id, x=round(start[0], 2), y=round(start[1], 2),
            end_x=round(nx, 2), end_y=round(ny, 2), outcome="success",
            attrs={"distance_m": round(math.dist(start, (nx, ny)), 1), "carrier_speed_kmh": round(speed, 1),
                   "under_pressure": pressured},
        ))
        self._advance(length / (speed / 3.6) * prof.tempo + self.rng.uniform(0.3, 1.2))
        return events

    def _shot(self, att: Team, dfn: Team, pressured: bool) -> list[MatchEvent]:
        shooter = self.carrier
        rel_x = self._rel_x(att, self.ball[0])
        xg = shot_xg(rel_x, self.ball[1], under_pressure=pressured)
        goal_p = xg * (0.55 + 0.5 * shooter.finishing)
        power = self.rng.random()
        speed = 62 + 58 * power + self.rng.uniform(-4, 4)
        roll = self.rng.random()
        if roll < goal_p:
            outcome = "goal"
        elif pressured and roll < goal_p + 0.22:
            outcome = "blocked"
        elif roll < goal_p + (1 - goal_p) * 0.45:
            outcome = "saved"
        else:
            outcome = "off_target"
        goal_x = PITCH_X if att is self.home else 0.0
        end_y = 34 + self.rng.uniform(-3.4, 3.4) if outcome in ("goal", "saved") else 34 + self.rng.uniform(-9, 9)
        start = list(self.ball)
        events: list[MatchEvent] = []
        if outcome == "goal":
            self.score[att.id] += 1
        events.append(self._emit(
            "shot", team=att.id, player=shooter.id, x=round(start[0], 2), y=round(start[1], 2),
            end_x=goal_x, end_y=round(end_y, 2), outcome=outcome,
            attrs={"xg": round(xg, 3), "shot_speed_kmh": round(speed, 1), "under_pressure": pressured,
                   "distance_m": round(math.dist(start, (goal_x, 34)), 1),
                   "score": dict(self.score)},
        ))
        if outcome == "goal":
            self._advance(self.rng.uniform(50, 75))
            self.ball = [52.5, 34.0]
            other = self._other(att)
            kicker = self._player_by_role(other, "FW")
            self.possession, self.carrier = other, kicker
            events.append(self._emit("kickoff", team=other.id, player=kicker.id, x=52.5, y=34.0))
        elif outcome in ("saved", "blocked") and self.rng.random() < 0.35:
            self._advance(self.rng.uniform(15, 30))
            corner_y = 0.5 if end_y < 34 else 67.5
            self.ball = [PITCH_X - 0.5 if att is self.home else 0.5, corner_y]
            taker = max(att.players, key=lambda p: p.passing if p.role == "MF" else 0)
            self.carrier = taker
            events.append(self._emit("corner", team=att.id, player=taker.id, x=self.ball[0], y=self.ball[1]))
        else:
            self._advance(self.rng.uniform(10, 25))
            gk = self._gk(dfn)
            self.ball = [self._abs_x(dfn, 6.0), 34.0]
            events.append(self._change_possession(dfn, gk, "save" if outcome == "saved" else "goal_kick"))
        return events

    def _step(self) -> list[MatchEvent]:
        att, dfn = self.possession, self._other(self.possession)
        events: list[MatchEvent] = []
        press = self._pressure(att, dfn)
        if press:
            events.append(press)
        pressured = press is not None
        rel_x = self._rel_x(att, self.ball[0])
        central = abs(self.ball[1] - 34) < 20
        if rel_x > 88 and central:
            shot_p = 0.2
        elif rel_x > 78 and central:
            shot_p = 0.06
        elif rel_x > 70:
            shot_p = 0.02
        else:
            shot_p = 0.0
        r = self.rng.random()
        if r < shot_p:
            events.extend(self._shot(att, dfn, pressured))
        elif r < shot_p + (1 - shot_p) * (0.3 - 0.1 * self.profile(att).directness):
            events.extend(self._carry(att, dfn, pressured))
        else:
            events.extend(self._pass(att, dfn, pressured))
        if self._transition and self.possession is att:
            self._transition -= 1
        return events

    # ------------------------------------------------------------------ public
    def events(self) -> Iterator[MatchEvent]:
        stoppage = {1: self.rng.randint(1, 3) * 60, 2: self.rng.randint(3, 6) * 60}
        yield self._emit("kickoff", team=self.home.id, player=self.carrier.id, x=52.5, y=34.0,
                         attrs={"period": 1})
        while True:
            end = (45 * 60 if self.period == 1 else 90 * 60) + stoppage[self.period]
            if self.clock >= end:
                if self.period == 1:
                    yield self._emit("half_time", attrs={"score": dict(self.score)})
                    self.period = 2
                    self.clock = 45 * 60
                    self.ball = [52.5, 34.0]
                    self.possession = self.away
                    self.carrier = self._player_by_role(self.away, "FW")
                    yield self._emit("kickoff", team=self.away.id, player=self.carrier.id, x=52.5, y=34.0,
                                     attrs={"period": 2})
                    continue
                yield self._emit("full_time", attrs={"score": dict(self.score)})
                return
            yield from self._step()


def generate_match(seed: int = 7) -> tuple[MatchMeta, list[MatchEvent]]:
    engine = MatchEngine(seed)
    return engine.meta(), list(engine.events())
