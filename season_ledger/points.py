"""Season points: confirmed honours plus an explicitly supplied weighted rating."""

import math
import re
import unicodedata

from .domain import is_champions_league
from .competitions import is_supercup
from .league_outcomes import apply_league_championship


FORMULA_VERSION = "9"
# Transfermarkt IDs verified against saved competition snapshots. IDs keep
# similarly named leagues and lower divisions outside the championship bonus.
TOP_FIVE_LEAGUE_IDS = {"GB1", "FR1", "ES1", "IT1", "L1"}
# Verified Transfermarkt top-division identities. Historical editions and
# playoff phases share one identity for the season's main-league selection.
MEDIUM_LEAGUE_IDS = {"MLS1", "PO1", "SA1", "NL1", "BE1", "TR1", "RU1", "ARG1", "BRA1", "MEX1"}
LEAGUE_PHASE_IDS = {
    "POUS": "MLS1",
    "PONL": "NL1", "NL1P": "NL1",
    "EJPL": "BE1", "POBE": "BE1", "BPO4": "BE1", "ESJP": "BE1",
    "AR1N": "ARG1", "ARGC": "ARG1", "CDLP": "ARG1", "CDCA": "ARG1",
    "MEXA": "MEX1", "POMX": "MEX1", "POME": "MEX1",
}
BONUS_RULES = {
    "league": {"winner": 37},
    "champions_league": {"winner": 75, "runner_up": 50, "semifinal": 25, "quarterfinal": 12},
    "europa_league": {"winner": 25},
    "conference_league": {"winner": 10},
    "cup": {"winner": 12},
    "world_cup": {"winner": 87, "runner_up": 25, "semifinal": 12},
    "euro_copa": {"winner": 75, "runner_up": 25},
    "afcon": {"winner": 25, "runner_up": 8},
}
BONUS_MIN_GAMES = {
    "league": 15,
    "champions_league": 5,
    "europa_league": 5,
    "conference_league": 5,
    "cup": 1,
    "world_cup": 3,
    "euro_copa": 3,
    "afcon": 3,
}


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _name(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(re.findall(r"[^\W_]+", "".join(c for c in text if not unicodedata.combining(c))))


def _league_tier(identity: str) -> int:
    if identity in TOP_FIVE_LEAGUE_IDS:
        return 0
    return 1 if identity in MEDIUM_LEAGUE_IDS else 2


def _league_penalties(competitions: list[dict]) -> list[dict]:
    """Use the most-played league, combining clubs and verified league phases."""
    if not isinstance(competitions, list):
        return []
    leagues = {}
    for row in competitions:
        if (not isinstance(row, dict) or is_supercup(row)
                or not _number(row.get("games")) or row["games"] <= 0):
            continue
        identity = str(row.get("id") or "").strip()
        if not identity:
            continue  # A missing league identity does not prove a lower tier.
        if row.get("category") != "domestic_league" and identity not in LEAGUE_PHASE_IDS:
            continue  # TM stores some verified league playoff phases as 'other'.
        canonical = LEAGUE_PHASE_IDS.get(identity, identity)
        league = leagues.setdefault(canonical, {"games": 0, "row": row, "name": None})
        league["games"] += row["games"]
        if row["games"] > league["row"]["games"]:
            league["row"] = row
        if identity == canonical and row.get("name"):
            league["name"] = row["name"]
    if not leagues:
        return []
    # Equal appearance counts use the better tier, then a stable identity.
    identity, league = min(leagues.items(), key=lambda item: (-item[1]["games"], _league_tier(item[0]), item[0]))
    tier = _league_tier(identity)
    if tier == 0:
        return []
    common = {"competition_id": identity, "competition": league["name"] or league["row"].get("name") or identity,
              "team": league["row"].get("team"), "games": league["games"]}
    result = [{**common, "kind": "outside_top_five", "outcome": "outside_top_five", "points": -15}]
    if tier == 2:
        result.append({**common, "kind": "outside_medium_leagues", "outcome": "outside_medium_leagues", "points": -25})
    return result


def _kind(row: dict) -> str | None:
    if is_supercup(row):
        return None
    category = row.get("category")
    if category == "domestic_league":
        return "league" if str(row.get("id") or "").strip() in TOP_FIVE_LEAGUE_IDS else None
    if category == "continental":
        if is_champions_league(row):
            return "champions_league"
        identity = str(row.get("id") or "").strip()
        if identity:
            return {"EL": "europa_league", "UCOL": "conference_league"}.get(identity)
        return {"uefa europa league": "europa_league", "europa league": "europa_league",
                "uefa conference league": "conference_league", "uefa europa conference league": "conference_league",
                "conference league": "conference_league", "europa conference league": "conference_league"}.get(_name(row.get("name")))
    if category == "domestic_cup":
        kind = row.get("competition_type_id")
        if kind == 15:  # Youth cups have no bonus; supercups were excluded above.
            return None
        name = _name(row.get("name"))
        if any(term in name for term in ("youth", "u19", "u21")):
            return None
        return "cup" if kind in (None, 8, 14) else None
    if category == "national_major":
        identity = str(row.get("id") or "").strip()
        if identity:
            if identity in {"FIWC", "WM"}:
                return "world_cup"
            if identity == "AFCN":
                return "afcon"
            return "euro_copa" if identity in {"EURO", "COPA", "CA16"} else None
        name = _name(row.get("name"))
        if name in {"world cup", "fifa world cup"}:
            return "world_cup"
        if name in {"uefa euro", "euro", "european championship", "copa america", "copa america centenario"}:
            return "euro_copa"
        if name in {"africa cup of nations", "african cup of nations"}:
            return "afcon"
    return None


def _outcome(row: dict) -> tuple[str | None, str | None]:
    outcome = row.get("final_outcome")
    if outcome in {"winner", "runner_up"}:
        return outcome, None
    place = str(row.get("place") or "").strip()
    if place in {"1", "2"}:
        return ("winner" if place == "1" else "runner_up"), None
    stages = {str(stage) for stage in row.get("stage_history", [])}
    if row.get("stage_code"):
        stages.add(str(row["stage_code"]))
    if place == "Финал" or stages.intersection({"FF", "FFH", "FFR"}):
        return None, "Исход финала не подтверждён источником."
    if outcome == "semifinal" or place in {"Полуфинал", "1/2 финала"} or stages.intersection({"HF", "HFH", "HFR"}):
        return "semifinal", None
    if outcome == "quarterfinal" or place in {"Четвертьфинал", "1/4 финала"} or stages.intersection({"VF", "VFH", "VFR"}):
        return "quarterfinal", None
    return None, None


def calculate_season_points(snapshot: dict, *, weighted_rating: float | None = None) -> dict:
    """Award one best result per category; never substitute an average for weights."""
    if weighted_rating is not None and (not _number(weighted_rating) or not 0 <= weighted_rating <= 10):
        raise ValueError("Взвешенная оценка должна быть числом от 0 до 10.")
    awarded = {}
    unresolved = []
    has_champions_league = False
    has_national_team = False
    # Recompute proof from the linked source rows; arbitrary saved status
    # metadata alone cannot confirm a championship or suppress uncertainty.
    for row in apply_league_championship(snapshot.get("competitions", [])):
        if not isinstance(row, dict) or not _number(row.get("games")) or row["games"] <= 0:
            continue
        kind = _kind(row)
        has_champions_league |= kind == "champions_league"
        # TM appearance counts include unrated games. Its national types also
        # cover youth and Olympic sides displayed under 'other'. Raw FotMob
        # national flags are not used: legacy club feeds can mislabel them.
        has_national_team |= (row.get("category") in {"national_regular", "national_major"}
                              or (type(row.get("competition_type_id")) is int
                                  and row["competition_type_id"] in {11, 17, 19, 20}))
        if kind is None or row["games"] < BONUS_MIN_GAMES[kind]:
            continue
        outcome, reason = _outcome(row)
        if kind == "league":
            championship = row.get("league_championship")
            if championship:
                outcome = "winner" if championship["outcome"] == "winner" else None
                reason = None
            elif str(row.get("id")) in {"MLS1", "POUS"}:
                outcome = None
                reason = "Результат плей-офф чемпионата не подтверждён источником."
            elif row.get("competition_complete") is not True:
                # A current table leader has not yet won the championship.
                reason = "Чемпионат ещё не завершён." if row.get("competition_complete") is False else "Завершение чемпионата не подтверждено источником."
                outcome = None
            elif row.get("place") is None:
                reason = "Итоговое место в чемпионате недоступно."
            else:
                outcome = "winner" if str(row["place"]).strip() == "1" else None
        elif kind == "cup" and row.get("competition_type_id") not in (8, 14):
            reason = "Тип национального кубка не подтверждён источником." if outcome == "winner" else None
            outcome = None
        if reason:
            unresolved.append({"kind": kind, "competition_id": row.get("id"),
                               "competition": row.get("name"), "team": row.get("team"), "reason": reason})
            continue
        points = BONUS_RULES[kind].get(outcome, 0)
        if points and points > awarded.get(kind, {}).get("points", 0):
            awarded[kind] = {"kind": kind, "competition_id": row.get("id"),
                             "competition": row.get("name"), "team": row.get("team"),
                             "outcome": outcome, "points": points}
    bonuses = [awarded[kind] for kind in BONUS_RULES if kind in awarded]
    # Unknown results cannot change a category whose maximum is already earned.
    pending = [row for row in unresolved if awarded.get(row["kind"], {}).get("points", 0) < max(BONUS_RULES[row["kind"]].values())]
    if not has_champions_league:
        bonuses.append({"kind": "no_champions_league", "competition_id": "CL", "competition": "Лига чемпионов",
                        "team": None, "outcome": "no_appearances", "points": -25})
    if not has_national_team:
        bonuses.append({"kind": "no_national_team", "competition_id": None, "competition": "Сборная",
                        "team": None, "outcome": "no_appearances", "points": -12})
    bonuses.extend(_league_penalties(snapshot.get("competitions", [])))
    bonus_points = sum(row["points"] for row in bonuses)
    base_points = weighted_rating * 100 if weighted_rating is not None else None
    status = "awaiting_weighted_rating" if weighted_rating is None else "awaiting_results" if pending else "complete"
    return {"formula_version": FORMULA_VERSION, "weighted_rating": weighted_rating,
            "base_points": base_points, "bonus_points": bonus_points,
            "total": base_points + bonus_points if status == "complete" else None,
            "status": status, "bonuses": bonuses, "pending": pending}
