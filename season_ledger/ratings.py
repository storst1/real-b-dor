"""Position-adjusted FotMob indices without changing the source ratings.

Each coefficient is mean(position) / mean(striker), so a source rating must be
divided by its coefficient. The player's imported main position applies to the
whole season; match-specific positions are not available in these snapshots.
"""

from copy import deepcopy
import math
import re

from .domain import contributes_to_group


POSITION_COEFFICIENTS = {
    "GK": 0.9253,
    "CB": 0.9664,
    "LB/RB": 0.9811,
    "DM/CM": 1.0021,
    "AM": 1.0158,
    "LM/RM/LW/RW": 1.0096,
    "ST/SS": 1.0000,
}

_POSITIONS = {
    "gk": "GK", "goalkeeper": "GK", "goal keeper": "GK",
    "cb": "CB", "centre back": "CB", "center back": "CB", "central defender": "CB",
    "lb": "LB/RB", "left back": "LB/RB", "left fullback": "LB/RB", "left full back": "LB/RB",
    "rb": "LB/RB", "right back": "LB/RB", "right fullback": "LB/RB", "right full back": "LB/RB",
    "dm": "DM/CM", "defensive midfield": "DM/CM", "defensive midfielder": "DM/CM",
    "cm": "DM/CM", "central midfield": "DM/CM", "central midfielder": "DM/CM",
    "am": "AM", "attacking midfield": "AM", "attacking midfielder": "AM",
    "lm": "LM/RM/LW/RW", "left midfield": "LM/RM/LW/RW", "left midfielder": "LM/RM/LW/RW",
    "rm": "LM/RM/LW/RW", "right midfield": "LM/RM/LW/RW", "right midfielder": "LM/RM/LW/RW",
    "lw": "LM/RM/LW/RW", "left winger": "LM/RM/LW/RW", "left wing": "LM/RM/LW/RW",
    "rw": "LM/RM/LW/RW", "right winger": "LM/RM/LW/RW", "right wing": "LM/RM/LW/RW",
    "st": "ST/SS", "ss": "ST/SS", "cf": "ST/SS", "striker": "ST/SS",
    "centre forward": "ST/SS", "center forward": "ST/SS", "second striker": "ST/SS",
}


def position_group(position: str | None) -> str | None:
    """Map an unambiguous main position; compound/generic positions stay unknown."""
    if not isinstance(position, str):
        return None
    key = re.sub(r"[\s\-–—]+", " ", position.strip().casefold())
    return _POSITIONS.get(key)


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def adjusted_rating(raw, coefficient) -> float | None:
    """Return full precision; presentation may round, and indices can exceed 10."""
    if (not _number(raw) or not 0 < raw <= 10
            or not _number(coefficient) or coefficient <= 0):
        return None
    return raw / coefficient


def _weighted_adjusted(rows: list[dict], coefficient: float | None) -> float | None:
    rated = [(row, adjusted_rating(row.get("rating"), coefficient)) for row in rows]
    rated = [(row, rating) for row, rating in rated if rating is not None]
    known = [(row, rating) for row, rating in rated
             if _number(row.get("rated_games")) and row["rated_games"] > 0]
    if rated and len(known) == len(rated):
        denominator = sum(row["rated_games"] for row, _ in known)
        return sum(rating * row["rated_games"] for row, rating in known) / denominator
    if len(rated) == 1:
        return rated[0][1]
    return None


def apply_position_adjustment(snapshot: dict) -> dict:
    """Copy a snapshot and derive indices from its original, unrounded ratings.

    Groups and total use competition ratings with real rating sample counts.
    Appearance counts never stand in for missing sample counts. Legacy snapshots
    that have no competition list can still adjust their existing summary rating.
    """
    result = deepcopy(snapshot)
    position = result.get("position")
    position = (position.strip() or None) if isinstance(position, str) else None
    group = position_group(position)
    coefficient = POSITION_COEFFICIENTS.get(group)
    result["rating_adjustment"] = {
        "group": group, "coefficient": coefficient, "method": "divide", "position": position,
    }
    competitions = result.get("competitions")
    competitions = [row for row in competitions if isinstance(row, dict)] if isinstance(competitions, list) else []
    for row in competitions:
        row["adjusted_rating"] = adjusted_rating(row.get("rating"), coefficient)
    has_competitions = "competitions" in result
    groups = result.get("groups")
    if isinstance(groups, dict):
        for category, stats in groups.items():
            if not isinstance(stats, dict):
                continue
            stats["adjusted_rating"] = (
                _weighted_adjusted([row for row in competitions if contributes_to_group(row, category)], coefficient)
                if has_competitions else adjusted_rating(stats.get("rating"), coefficient)
            )
    total = result.get("total")
    if isinstance(total, dict):
        total["adjusted_rating"] = (_weighted_adjusted(competitions, coefficient) if has_competitions
                                    else adjusted_rating(total.get("rating"), coefficient))
    return result
