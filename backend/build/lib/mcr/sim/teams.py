"""Fictional clubs, squads and tactical profiles used by the synthetic match engine.

All names are invented. No real club, player or league data is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    number: int
    role: str  # GK, DF, MF, FW
    # Base formation slot, normalised to the team's own half: x in [0, 1] (own goal -> halfway),
    # y in [0, 1] (left touchline -> right touchline).
    base_x: float
    base_y: float
    passing: float  # 0..1
    finishing: float  # 0..1
    pace: float  # top sprint speed in km/h
    tackling: float  # 0..1


@dataclass(frozen=True)
class TacticalProfile:
    name: str
    press_intensity: float  # probability multiplier for applying pressure off the ball
    directness: float  # 0 = patient build-up, 1 = long/direct play
    tempo: float  # seconds per action multiplier (lower = faster)
    block_depth: float  # 0 = deep low block, 1 = very high line
    width: float  # lateral spread of the shape


@dataclass
class Team:
    id: str
    name: str
    short: str
    color: str
    players: list[Player]
    profile: TacticalProfile
    # Optional in-match tactical switch: (minute, new profile). The engine applies it silently;
    # it is never emitted as an event, so detecting it is the analytics layer's job.
    switch: tuple[int, TacticalProfile] | None = None
    meta: dict = field(default_factory=dict)


POSSESSION = TacticalProfile("possession", press_intensity=0.8, directness=0.2, tempo=0.9, block_depth=0.7, width=0.9)
COUNTER = TacticalProfile("counter", press_intensity=0.45, directness=0.8, tempo=1.1, block_depth=0.3, width=0.7)
HIGH_PRESS = TacticalProfile("high_press", press_intensity=1.6, directness=0.55, tempo=0.8, block_depth=0.85, width=0.8)

# 4-3-3 slots (own half normalised)
_433 = [
    ("GK", 0.04, 0.50),
    ("DF", 0.22, 0.12), ("DF", 0.18, 0.37), ("DF", 0.18, 0.63), ("DF", 0.22, 0.88),
    ("MF", 0.45, 0.30), ("MF", 0.40, 0.50), ("MF", 0.45, 0.70),
    ("FW", 0.75, 0.15), ("FW", 0.82, 0.50), ("FW", 0.75, 0.85),
]
# 4-4-2 slots
_442 = [
    ("GK", 0.04, 0.50),
    ("DF", 0.20, 0.12), ("DF", 0.17, 0.37), ("DF", 0.17, 0.63), ("DF", 0.20, 0.88),
    ("MF", 0.42, 0.12), ("MF", 0.38, 0.38), ("MF", 0.38, 0.62), ("MF", 0.42, 0.88),
    ("FW", 0.72, 0.40), ("FW", 0.75, 0.60),
]

_NB_NAMES = [
    ("Tomas Reyner", 1), ("Callum Ashby", 2), ("Idris Okafor", 5), ("Lukas Brenn", 4), ("Mateo Silvan", 3),
    ("Jonah Pell", 8), ("Ravi Castell", 6), ("Eli Marchetti", 10), ("Kofi Annan-Ward", 7),
    ("Dario Vance", 9), ("Noah Lindqvist", 11),
]
_RA_NAMES = [
    ("Bram Kessler", 1), ("Owen Taft", 2), ("Samir Haddou", 4), ("Felix Moreau", 5), ("Aiden Rook", 3),
    ("Yusuf Demir", 7), ("Henrik Sol", 6), ("Marco Ilic", 8), ("Theo Barnaby", 11),
    ("Kai Mensah", 9), ("Luca Ferrante", 10),
]


def _squad(prefix: str, names, slots, quality: float) -> list[Player]:
    players = []
    for i, ((name, number), (role, bx, by)) in enumerate(zip(names, slots)):
        passing = {"GK": 0.55, "DF": 0.70, "MF": 0.84, "FW": 0.72}[role] * quality
        finishing = {"GK": 0.05, "DF": 0.25, "MF": 0.45, "FW": 0.80}[role] * quality
        tackling = {"GK": 0.2, "DF": 0.8, "MF": 0.65, "FW": 0.35}[role]
        pace = {"GK": 26.0, "DF": 32.0, "MF": 32.5, "FW": 34.5}[role] + (i % 3) * 0.6
        players.append(
            Player(
                id=f"{prefix}{number}",
                name=name,
                number=number,
                role=role,
                base_x=bx,
                base_y=by,
                passing=min(passing, 0.97),
                finishing=min(finishing, 0.95),
                pace=pace,
                tackling=tackling,
            )
        )
    return players


def default_teams() -> tuple[Team, Team]:
    home = Team(
        id="NBR",
        name="Northbridge FC",
        short="NBR",
        color="#e4572e",
        players=_squad("NBR", _NB_NAMES, _433, quality=1.02),
        profile=POSSESSION,
        meta={"formation": "4-3-3", "manager": "A. Halloran"},
    )
    away = Team(
        id="RVA",
        name="Riverside Athletic",
        short="RVA",
        color="#2e86ab",
        players=_squad("RVA", _RA_NAMES, _442, quality=1.0),
        profile=COUNTER,
        switch=(58, HIGH_PRESS),
        meta={"formation": "4-4-2", "manager": "S. Okonkwo"},
    )
    return home, away
