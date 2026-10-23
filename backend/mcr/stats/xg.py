"""A small, transparent expected-goals (xG) model.

Logistic model on distance and shooting angle, the two features that explain most of the
variance in public xG models. Coefficients are hand-tuned so that typical values look familiar
(penalty spot ~0.3-0.35, edge of the box ~0.05-0.08). Being explainable matters more here than
being state of the art: every number we show can be traced back to these two inputs.
"""

from __future__ import annotations

import math

GOAL_WIDTH = 7.32


def shot_geometry(rel_x: float, y: float) -> tuple[float, float]:
    """Distance (m) to goal centre and visible goal angle (radians) from a shot location.

    rel_x is measured from the shooter's own goal line, so the target goal is at rel_x = 105.
    """
    dx = max(0.5, 105.0 - rel_x)
    dy = abs(y - 34.0)
    distance = math.hypot(dx, dy)
    a = math.atan2(GOAL_WIDTH * dx, dx * dx + dy * dy - (GOAL_WIDTH / 2) ** 2)
    if a < 0:
        a += math.pi
    return distance, a


def shot_xg(rel_x: float, y: float, under_pressure: bool = False) -> float:
    distance, angle = shot_geometry(rel_x, y)
    z = -1.3 - 0.105 * distance + 1.55 * angle - (0.3 if under_pressure else 0.0)
    return round(1 / (1 + math.exp(-z)), 4)
