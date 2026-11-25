"""Confirmed championship outcomes for leagues decided by a separate playoff."""

from copy import deepcopy
from datetime import date


_MLS_COMPETITIONS = {"MLS1", "POUS"}
_BEST_OF_THREE_CODES = ("1RH", "1RR", "1RE")
_FINISHED_STATES = {
    "regularly_terminated", "extra_time", "penalty_shootout", "terminated",
    "finished", "after_extra_time", "after_penalties",
}


def _identity(value) -> str | None:
    if type(value) not in (str, int):
        return None
    return str(value).strip() or None


def _count(value) -> bool:
    return type(value) is int and value >= 0


def _season(row: dict) -> str | None:
    season = _identity(row.get("season_id"))
    seasons = row.get("season_ids")
    if isinstance(seasons, list) and {_identity(value) for value in seasons} != {season}:
        return None
    return season


def _finished_day(match: dict) -> date | None:
    value = match.get("date")
    if not isinstance(value, str) or match.get("game_state") not in _FINISHED_STATES:
        return None
    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        return None
    return day if day <= date.today() else None


def _final_result(playoff: dict, team_id: str) -> tuple[str, dict] | None:
    final = playoff.get("final_result")
    outcome = playoff.get("final_outcome")
    if (outcome not in {"winner", "runner_up"} or playoff.get("stage_code") not in {"FF", "FFR"}
            or not isinstance(final, dict) or _identity(final.get("team_id")) != team_id):
        return None
    game_ids = final.get("game_ids")
    basis = final.get("basis")
    expected = {"single_match_score": 1, "aggregate_score": 2}.get(basis)
    if (not expected or not isinstance(game_ids, list) or len(game_ids) != expected
            or len({_identity(value) for value in game_ids}) != expected
            or any(_identity(value) is None for value in game_ids)
            or _identity(final.get("opponent_id")) in {None, team_id}):
        return None
    scored, conceded = final.get("team_goals"), final.get("opponent_goals")
    if not _count(scored) or not _count(conceded) or scored == conceded:
        return None
    if (scored > conceded) != (outcome == "winner"):
        return None
    return ("winner" if outcome == "winner" else "not_winner"), {
        "basis": "playoff_final_result", "game_ids": [str(value) for value in game_ids],
        "final_result": deepcopy(final),
    }


def _best_of_three_loss(playoff: dict, team_id: str, season_id: str) -> dict | None:
    # Verified POUS season rounds and MLS rules: the first team to win two
    # games advances; aggregate goals never determine a best-of-three series.
    matches = playoff.get("matches")
    if (playoff.get("stage_code") != "1RE" or not isinstance(matches, list) or len(matches) != 3
            or any(not isinstance(row, dict) for row in matches)):
        return None
    by_code = {row.get("stage_code"): row for row in matches}
    if set(by_code) != set(_BEST_OF_THREE_CODES):
        return None
    ordered = [by_code[code] for code in _BEST_OF_THREE_CODES]
    game_ids = [_identity(row.get("id")) for row in ordered]
    days = [_finished_day(row) for row in ordered]
    opponents = {_identity(row.get("opponent_id")) for row in ordered}
    if (None in game_ids or len(set(game_ids)) != 3 or any(day is None for day in days)
            or not days[0] < days[1] < days[2]
            or len(opponents) != 1 or None in opponents or team_id in opponents):
        return None
    wins = []
    for row in ordered:
        if (_identity(row.get("team_id")) != team_id
                or _identity(row.get("competition_id")) != "POUS"
                or _identity(row.get("season_id")) != season_id):
            return None
        scored, conceded = row.get("goals_for"), row.get("goals_against")
        if not _count(scored) or not _count(conceded) or scored == conceded:
            return None
        if row.get("game_state") in {"penalty_shootout", "after_penalties"} and row.get("score_includes_shootout") is not True:
            return None
        wins.append(scored > conceded)
    if sum(wins[:2]) != 1 or wins[2] is not False:
        return None
    return {"basis": "mls_best_of_three_elimination", "game_ids": game_ids,
            "opponent_id": next(iter(opponents)), "series_wins": 1, "series_losses": 2}


def apply_league_championship(competitions: list[dict]) -> list[dict]:
    """Copy rows and derive MLS outcomes only from a verified source linkage.

    A regular-season conference/table rank is not an MLS Cup championship.
    Input metadata is discarded; incomplete, conflicting or advancing series
    remain unconfirmed. The same outcome annotates both linked league phases.
    """
    result = deepcopy(competitions) if isinstance(competitions, list) else []
    pairs = {}
    for row in result:
        if not isinstance(row, dict):
            continue
        row.pop("league_championship", None)
        identity = _identity(row.get("id"))
        team_id, season_id = _identity(row.get("team_id")), _season(row)
        if identity in _MLS_COMPETITIONS and team_id and season_id:
            pairs.setdefault((team_id, season_id), {}).setdefault(identity, []).append(row)
    for (team_id, season_id), phases in pairs.items():
        if set(phases) != _MLS_COMPETITIONS or any(len(rows) != 1 for rows in phases.values()):
            continue
        regular, playoff = phases["MLS1"][0], phases["POUS"][0]
        confirmed = _final_result(playoff, team_id)
        if confirmed:
            outcome, evidence = confirmed
        elif evidence := _best_of_three_loss(playoff, team_id, season_id):
            outcome = "not_winner"
        else:
            continue
        metadata = {"status": "confirmed", "outcome": outcome,
                    "competition_id": "POUS", "team_id": team_id, "season_id": season_id, **evidence}
        regular["league_championship"] = deepcopy(metadata)
        playoff["league_championship"] = deepcopy(metadata)
    return result
