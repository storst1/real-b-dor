"""Season snapshots from the same Transfermarkt JSON SDK as the match journal.

The SDK's stock season totals omit national teams. We deliberately aggregate its
raw performance records so that national-team appearances remain visible, while
keeping unknown statistics distinct from zero.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import re
from typing import TYPE_CHECKING

import httpx
from transfermarkt_api.exceptions import TransfermarktError
from transfermarkt_api.tfmkt import TfmktClient
from season_ledger.league_outcomes import apply_league_championship

if TYPE_CHECKING:
    from season_ledger.domain import Season


_NATIONAL_TYPES = {11, 17, 19, 20}
_MAJOR_IDS = {"FIWC", "WM", "EURO", "CA", "COPA", "CA16", "GC", "AFR", "ASIA", "AFCON", "AFC", "AFAC", "AFCN"}
_STAGES = {"FF": "Финал", "HF": "Полуфинал", "VF": "1/4 финала", "AF": "1/8 финала", "16F": "1/16 финала"}
_OPTIONAL_ERRORS = (TransfermarktError, httpx.HTTPError, ValueError, TypeError, KeyError)


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _array(value) -> list:
    return value if isinstance(value, list) else []


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _integer(value) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
        return int(value)
    return None


def _date(value) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _https(value) -> str | None:
    value = _text(value)
    return value if value and value.startswith("https://") else None


def _stage(value) -> str | None:
    value = str(value or "")
    # First/second legs are HFH/HFR, VFH/VFR, AFH/AFR upstream.
    return _STAGES.get(value) or (_STAGES.get(value[:-1]) if value.endswith(("H", "R")) else None)


def _national(info: dict) -> bool:
    return info.get("isNationalGame") is True or info.get("competitionTypeId") in _NATIONAL_TYPES


def _team_score(game: dict) -> tuple[int, int] | None:
    teams = _object(game.get("clubsInformation"))
    club, opponent = _object(teams.get("club")), _object(teams.get("opponent"))
    scored = _integer(club.get("goalsTotal"))
    conceded = _integer(club.get("opponentGoalsTotal"))
    opposite_scored = _integer(opponent.get("goalsTotal"))
    opposite_conceded = _integer(opponent.get("opponentGoalsTotal"))
    if scored is None or (conceded is None and opposite_scored is None):
        return None
    if conceded is None:
        conceded = opposite_scored
    # Conflicting score perspectives cannot reliably identify a champion.
    if (opposite_scored is not None and opposite_scored != conceded) or (opposite_conceded is not None and opposite_conceded != scored):
        return None
    return scored, conceded


def resolve_final(records: list[dict]) -> dict:
    """Resolve the team's final, even when the player did not appear in it.

    Performance totals include extra-time goals and, for penalty_shootout,
    the decisive shootout goals. For two-legged finals both FFH and FFR are
    required. A tied aggregate may depend on competition-specific away-goal
    rules, so it deliberately remains unresolved instead of using one leg.
    """
    if not records:
        return {"place": None, "stage_code": None, "final_outcome": None, "final_result": None}
    records = sorted(records, key=lambda row: _object(_object(row.get("gameInformation")).get("date")).get("dateTimeUTC") or "")
    latest = records[-1]
    info = _object(latest.get("gameInformation"))
    stage_code = _text(info.get("competitionGroupId"))
    result = {"place": _stage(stage_code), "stage_code": stage_code, "final_outcome": None, "final_result": None}
    if stage_code not in {"FF", "FFR"}:
        return result
    finals = [latest]
    basis = "single_match_score"
    team = _object(_object(latest.get("clubsInformation")).get("club"))
    opponent = _object(_object(latest.get("clubsInformation")).get("opponent"))
    if stage_code == "FFR":
        team_id, opponent_id = _text(str(team.get("clubId") or "")), _text(str(opponent.get("clubId") or ""))
        if not team_id or not opponent_id:
            return result
        first_legs = []
        for record in records[:-1]:
            first_info = _object(record.get("gameInformation"))
            teams = _object(record.get("clubsInformation"))
            if (first_info.get("competitionGroupId") == "FFH"
                    and first_info.get("competitionId") == info.get("competitionId")
                    and str(first_info.get("seasonId")) == str(info.get("seasonId"))
                    and str(_object(teams.get("club")).get("clubId")) == team_id
                    and str(_object(teams.get("opponent")).get("clubId")) == opponent_id):
                first_legs.append(record)
        if len(first_legs) != 1:
            return result
        finals = [first_legs[0], latest]
        basis = "aggregate_score"
    scores = [_team_score(record) for record in finals]
    if any(score is None for score in scores):
        return result
    scored, conceded = sum(score[0] for score in scores), sum(score[1] for score in scores)
    result["final_result"] = {
        "basis": basis,
        "game_ids": [str(_object(record.get("gameInformation")).get("gameId")) for record in finals],
        "team_id": str(team.get("clubId")),
        "opponent_id": str(opponent.get("clubId")) if opponent.get("clubId") is not None else None,
        "team_goals": scored,
        "opponent_goals": conceded,
        "includes_shootout": any(_object(record.get("gameInformation")).get("gameState") in {"penalty_shootout", "after_penalties"} for record in finals),
    }
    if scored != conceded:
        winner = scored > conceded
        result.update(place="1" if winner else "2", final_outcome="winner" if winner else "runner_up")
    return result


def league_completion(table: dict, seasons: dict, season_id: str) -> bool | None:
    """Confirm a complete single-table league from its actual season schedule.

    `table.meta.seasonId` always describes today's season, even for historical
    queries. The season route's `gameDays` is the requested season's schedule.
    Grouped/multi-phase leagues and incomplete metadata remain unknown.
    """
    expected_values = {
        value for row in _array(_object(seasons).get("seasons"))
        if isinstance(row, dict) and str(row.get("seasonId")) == str(season_id)
        and (value := _integer(row.get("gameDays"))) is not None and value > 0
    }
    if len(expected_values) != 1:
        return None
    expected = expected_values.pop()
    tables = _array(_object(table).get("tables"))
    if len(tables) != 1:
        return None
    rows = _array(_object(tables[0]).get("clubs"))
    if len(rows) < 2 or any(not isinstance(row, dict) for row in rows):
        return None
    identities = [str(row.get("clubId") or "") for row in rows]
    ranks = [_integer(_object(row.get("ranking")).get("current")) for row in rows]
    if (not all(identities) or len(set(identities)) != len(rows)
            or set(ranks) != set(range(1, len(rows) + 1))):
        return None
    if any(_text(_object(row.get("group")).get(key)) for row in rows for key in ("name", "division")):
        return None
    games = [_object(row.get("game")) for row in rows]
    counts = [_integer(row.get("totalCount")) for row in games]
    if any(count is None or count > expected for count in counts):
        return None
    if any(row.get("isPlaying") is True for row in games) or any(count < expected for count in counts):
        return False
    return True if all(row.get("isPlaying") is False for row in games) else None


def _match_details(record: dict, competitions: dict, clubs: dict) -> dict:
    info = _object(record.get("gameInformation"))
    teams = _object(record.get("clubsInformation"))
    team, opponent = _object(teams.get("club")), _object(teams.get("opponent"))
    team_id = str(team.get("clubId"))
    opponent_id = str(opponent["clubId"]) if opponent.get("clubId") is not None else None
    competition_id = str(info.get("competitionId"))
    stats = _object(record.get("statistics"))
    general = _object(stats.get("generalStatistics"))
    playing_time = _object(stats.get("playingTimeStatistics"))
    goals = _object(stats.get("goalStatistics"))
    score = _team_score(record)
    venue = _text(team.get("venue"))
    return {
        "id": str(info.get("gameId")),
        "date": _object(info.get("date")).get("dateTimeUTC"),
        "team_id": team_id, "team": _text(clubs.get(team_id, {}).get("name")) or team_id,
        "opponent_id": opponent_id,
        "opponent": _text(clubs.get(opponent_id, {}).get("name")) or opponent_id,
        "competition_id": competition_id,
        "competition": _text(competitions.get(competition_id, {}).get("name")) or competition_id,
        "competition_type_id": _integer(info.get("competitionTypeId")),
        "season_id": str(info.get("seasonId")) if info.get("seasonId") is not None else None,
        "stage_code": _text(info.get("competitionGroupId")),
        "venue": venue,
        "is_home": True if venue == "home" else False if venue == "away" else None,
        "goals_for": score[0] if score else None,
        "goals_against": score[1] if score else None,
        "score_includes_shootout": info.get("gameState") in {"penalty_shootout", "after_penalties"},
        "minutes": _integer(playing_time.get("playedMinutes")),
        "goals": _integer(goals.get("goalsScoredTotal")),
        "assists": _integer(goals.get("assists")),
        "position_id": _integer(general.get("positionId")),
        "started": playing_time.get("isStarting") if type(playing_time.get("isStarting")) is bool else None,
        "game_state": _text(info.get("gameState")),
    }


def category(info: dict, competition: dict | None = None) -> str:
    """Classify by provider types, keeping qualifiers out of final tournaments."""
    competition = competition or {}
    kind = info.get("competitionTypeId", competition.get("typeId"))
    identity = str(info.get("competitionId", competition.get("id", ""))).upper()
    national = _national(info) or kind in _NATIONAL_TYPES
    if national:
        if kind in {17, 20}:
            return "other"  # Youth teams are not the senior national team.
        name = (competition.get("name") or "").casefold()
        is_major_name = any(x in name for x in ("world cup", "uefa euro", "copa am", "africa cup of nations", "asian cup", "gold cup"))
        if kind != 19 and (identity in _MAJOR_IDS or (is_major_name and "qualif" not in name)):
            return "national_major"
        return "national_regular"
    if kind in {1, 2, 3, 4, 5, 6, 7, 18, 24}:
        return "domestic_league"
    if kind in {10, 13, 16}:
        return "continental"
    if kind in {8, 9, 14, 15, 23}:
        return "domestic_cup"
    return "other"


def historical_value(data: dict, cutoff: date) -> dict | None:
    """Last known EUR valuation at the cutoff; never substitute today's value."""
    candidates = []
    # Upstream may keep the last observation only under current. It is still a
    # dated observation and is eligible only when it precedes the cutoff.
    for entry in [*_array(data.get("history")), _object(data.get("current"))]:
        money = _object(_object(entry).get("marketValue"))
        observed = _date(money.get("determined"))
        amount = _integer(money.get("value"))
        # A dated zero is a real observation (notably after retirement). It
        # supersedes an older positive value; an undated zero remains unknown.
        if observed is not None and observed <= cutoff and amount is not None and money.get("currency") == "EUR":
            candidates.append((observed, amount))
    if not candidates:
        return None
    observed, amount = max(candidates, key=lambda item: item[0])
    return {"amount": amount, "currency": "EUR", "observed_on": observed.isoformat(), "as_of": cutoff.isoformat()}


def _selected(game: dict, season: Season, competitions: dict) -> bool:
    info = _object(game.get("gameInformation"))
    played_on = _date(_object(info.get("date")).get("dateTimeUTC"))
    if played_on is None or played_on > date.today() or info.get("isLiveGame") is True or info.get("isGamePostponed") is True:
        return False
    state = info.get("gameState")
    if state and state not in {"regularly_terminated", "extra_time", "penalty_shootout", "terminated", "finished", "after_extra_time", "after_penalties"}:
        return False
    kind = category(info, competitions.get(str(info.get("competitionId"))))
    if not _national(info) and not season.calendar:
        # Club seasons are the competition's actual season, not a fixed date
        # window. In particular, 2019/20 finished in July/August 2020, and some
        # summer club tournaments start before July 1.
        return str(info.get("seasonId")) == str(season.start_year)
    if (kind == "national_major" or str(info.get("competitionId")).upper() == "OLYM") and not season.calendar:
        national_end = getattr(season, "national_end", date(season.end.year, 8, 31))
        # Final tournaments use non-overlapping September–August windows in
        # both sources. Nominal edition IDs can predate delayed tournaments,
        # such as the 2023 Asian Cup actually played in January 2024.
        return date(season.start_year, 9, 1) <= played_on <= national_end
    return season.start <= played_on <= season.end


def aggregate_performances(games: list, season: Season, competitions: dict, clubs: dict) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    seen = set()
    for game in games:
        if not isinstance(game, dict) or not _selected(game, season, competitions):
            continue
        info = _object(game.get("gameInformation"))
        team = _object(_object(game.get("clubsInformation")).get("club"))
        game_id, team_id = str(info.get("gameId", "")), str(team.get("clubId", ""))
        competition_id = str(info.get("competitionId", ""))
        if not game_id or not team_id or not competition_id or (game_id, team_id) in seen:
            continue
        seen.add((game_id, team_id))
        grouped[(competition_id, team_id)].append(game)

    result = []
    for (competition_id, team_id), records in grouped.items():
        records.sort(key=lambda row: _object(_object(row.get("gameInformation")).get("date")).get("dateTimeUTC") or "")
        played = [row for row in records if _object(_object(row.get("statistics")).get("generalStatistics")).get("participationState") == "played"]
        if not played:
            continue
        competition = competitions.get(competition_id, {})
        club = clubs.get(team_id, {})
        latest = _object(records[-1].get("gameInformation"))

        def total(group: str, key: str) -> int | None:
            values = [_integer(_object(_object(row.get("statistics")).get(group)).get(key)) for row in played]
            return sum(values) if all(value is not None for value in values) else None

        result.append({
            "id": competition_id,
            "name": _text(competition.get("name")) or competition_id,
            "category": category(latest, competition),
            "competition_type_id": _integer(latest.get("competitionTypeId", competition.get("typeId"))),
            "competition_complete": None,
            "team": _text(club.get("name")) or team_id,
            "team_id": team_id,
            "season_id": str(latest.get("seasonId", season.start_year)),
            "season_ids": sorted({str(_object(row.get("gameInformation")).get("seasonId")) for row in records}),
            "games": len(played),
            "goals": total("goalStatistics", "goalsScoredTotal"),
            "assists": total("goalStatistics", "assists"),
            "minutes": total("playingTimeStatistics", "playedMinutes"),
            **resolve_final(records),
            "stage_history": list(dict.fromkeys(
                code for row in records if (code := _text(_object(row.get("gameInformation")).get("competitionGroupId")))
            )),
            "matches": [_match_details(row, competitions, clubs) for row in played],
        })
    return sorted(result, key=lambda row: (row["category"], row["name"], row["team"]))


class _ClientTransport(httpx.AsyncBaseTransport):
    """Let the SDK share the application's paced and testable HTTP client."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.client.send(request, follow_redirects=False)


class TransfermarktProvider:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def load(self, player_id: str, season: Season) -> dict:
        if not re.fullmatch(r"[1-9][0-9]{0,19}", player_id):
            raise ValueError("Некорректный ID игрока Transfermarkt.")
        sdk = TfmktClient(transport=_ClientTransport(self.client), max_concurrency=1)
        try:
            return await self._load(sdk, player_id, season)
        finally:
            await sdk.aclose()

    async def _load(self, sdk: TfmktClient, player_id: str, season: Season) -> dict:
        warnings = []
        player = await sdk.player(player_id)
        if not isinstance(player, dict) or str(player.get("id")) != player_id or not _text(player.get("name")):
            raise ValueError("Transfermarkt не вернул профиль этого игрока.")
        performance = await sdk.player_performance_games(player_id)
        if not isinstance(performance, dict) or str(performance.get("playerId")) != player_id or not isinstance(performance.get("performance"), list):
            raise ValueError("Transfermarkt вернул некорректную статистику игрока.")
        games = performance["performance"]
        # Fetch names only for plausible season records, rather than every club
        # and competition the player has encountered throughout his career.
        end = getattr(season, "national_end", date(season.end.year, 8, 31))
        nearby = []
        for row in games:
            if not isinstance(row, dict):
                continue
            info = _object(row.get("gameInformation"))
            day = _date(_object(info.get("date")).get("dateTimeUTC"))
            actual_club_season = (not season.calendar and not _national(info)
                                  and str(info.get("seasonId")) == str(season.start_year))
            if day is not None and (actual_club_season or season.start <= day <= end):
                nearby.append(row)
        competition_ids = [str(_object(row.get("gameInformation")).get("competitionId")) for row in nearby]
        competitions = await sdk.competitions(competition_ids)
        selected = [row for row in nearby if _selected(row, season, competitions)]
        club_ids = [str(_object(_object(row.get("clubsInformation")).get(side)).get("clubId"))
                    for row in selected for side in ("club", "opponent")
                    if _object(_object(row.get("clubsInformation")).get(side)).get("clubId") is not None]
        clubs = await sdk.clubs(club_ids)

        try:
            attributes = await sdk.attributes()
        except _OPTIONAL_ERRORS:
            attributes = {}
            warnings.append("Не удалось загрузить справочник гражданств Transfermarkt.")
        countries = {str(row.get("id")): row for row in _array(_object(attributes).get("countries")) if isinstance(row, dict)}
        nationality_ids = _object(_object(player.get("nationalityDetails")).get("nationalities"))
        country_records = [countries.get(str(nationality_ids.get(key)), {}) for key in ("nationalityId", "secondNationalityId")]
        position = _object(_object(player.get("attributes")).get("position"))
        life = _object(player.get("lifeDates"))
        totals = aggregate_performances(games, season, competitions, clubs)
        historical_clubs = []
        for club_id in dict.fromkeys(row["team_id"] for row in totals if row["category"] not in {"national_regular", "national_major"}):
            club = clubs.get(club_id, {})
            if _object(club.get("baseDetails")).get("isNationalTeam") is True:
                continue
            historical_clubs.append({"id": club_id, "name": club.get("name") or club_id, "logo_url": _https(club.get("crestUrl"))})
        profile = {
            "id": player_id,
            "name": player["name"],
            "birth_date": None if life.get("isDateOfBirthUnknown") else life.get("dateOfBirth"),
            "position": position.get("shortName") or position.get("name"),
            "nationalities": list(dict.fromkeys(row["name"] for row in country_records if row.get("name"))),
            "nationality_flags": [_https(row.get("flagUrl")) for row in country_records if row.get("name")],
            "photo_url": _https(player.get("portraitUrl")),
            "clubs": historical_clubs,
        }
        for total in totals:
            if total.get("stage_code") in {"FF", "FFR"} and total.get("final_outcome") is None:
                warnings.append(f"«{total['name']}»: по данным Transfermarkt нельзя достоверно определить победителя финала; показана стадия «Финал».")
            if total["category"] == "domestic_league":
                if len(total["season_ids"]) > 1:
                    total["place"] = None
                    warnings.append(f"«{total['name']}»: выбранный период включает несколько сезонов турнира; общее место команды не определено.")
                    continue
                try:
                    table = await sdk.competition_table(total["id"], total["season_id"])
                    rows = [row for group in _array(_object(table).get("tables")) for row in _array(_object(group).get("clubs"))]
                    standings = [row for row in rows if str(_object(row).get("clubId")) == total["team_id"]]
                    ranks = {_integer(_object(_object(row).get("ranking")).get("current")) for row in standings}
                    ranks.discard(None)
                    if len(ranks) == 1:
                        total["place"] = str(ranks.pop())
                    try:
                        seasons = await sdk.competition_seasons(total["id"])
                        total["competition_complete"] = league_completion(table, seasons, total["season_id"])
                        total["competition_complete_basis"] = "season_game_days_and_full_table" if total["competition_complete"] is not None else None
                    except _OPTIONAL_ERRORS:
                        warnings.append(f"Не удалось подтвердить завершение сезона {total['name']}.")
                except _OPTIONAL_ERRORS:
                    warnings.append(f"Не удалось загрузить место команды в {total['name']}.")
        cutoff = min(season.end, date.today())
        value = None
        try:
            history = await sdk.player_market_value_history(player_id)
            if isinstance(history, dict):
                value = historical_value(history, cutoff)
        except _OPTIONAL_ERRORS:
            warnings.append("Не удалось загрузить историю стоимости Transfermarkt.")
        if value is None:
            warnings.append("У Transfermarkt нет доступной оценки стоимости на выбранную дату.")
        if not totals:
            warnings.append("Transfermarkt не содержит сыгранных матчей в выбранном сезоне.")
        if any(row[key] is None for row in totals for key in ("goals", "assists", "minutes")):
            warnings.append("Часть статистики Transfermarkt отсутствует: неизвестные значения показаны прочерком.")
        return {"profile": profile, "competitions": apply_league_championship(totals), "market_value": value, "warnings": warnings}
