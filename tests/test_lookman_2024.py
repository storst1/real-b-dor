import asyncio
from copy import deepcopy

import httpx
import pytest

from season_ledger.domain import Season
from season_ledger.points import calculate_season_points
from season_ledger.providers import transfermarkt


# Saved Lookman totals; isolate the optional standings lookup from match parsing.
SAVED_TOTALS = [
    {"id": "IT1", "name": "Serie A", "category": "domestic_league", "competition_type_id": 1,
     "team": "Atalanta BC", "team_id": "800", "season_id": "2024", "season_ids": ["2024"],
     "games": 31, "goals": 15, "assists": 5, "minutes": 2265, "place": None,
     "competition_complete": None, "stage_code": None, "stage_history": []},
    {"id": "CL", "name": "UEFA Champions League", "category": "continental", "competition_type_id": 10,
     "team": "Atalanta BC", "team_id": "800", "games": 7, "goals": 5, "assists": 2, "minutes": 482,
     "place": None, "stage_code": "ZRR", "stage_history": ["GP", "ZRH", "ZRR"]},
    {"id": "AFCQ", "name": "Africa Cup of Nations Qualification", "category": "national_regular",
     "competition_type_id": 19, "team": "Nigeria", "team_id": "3444", "games": 4,
     "goals": 2, "assists": 0, "minutes": 360, "place": None, "stage_code": "D", "stage_history": ["D"]},
]


def serie_a_table(played=38):
    # Actual IT1/2024 ranking IDs. Historical table.meta incorrectly names 2026.
    clubs = ["6195", "46", "800", "506", "12", "430", "398", "5", "1025", "1047",
             "416", "410", "252", "276", "1390", "130", "1005", "749", "607", "2919"]
    return {"competitionId": "IT1", "meta": {"seasonId": 2026}, "tables": [{"clubs": [
        {"clubId": identity, "ranking": {"current": rank}, "group": {"name": "", "division": ""},
         "game": {"totalCount": played, "isPlaying": False}}
        for rank, identity in enumerate(clubs, 1)
    ]}]}


class StandingsSdk:
    def __init__(self, *, fail=False, played=38):
        self.fail, self.played = fail, played
        self.table_requests = []

    async def player(self, identity):
        return {"id": identity, "name": "Ademola Lookman", "attributes": {"position": {"shortName": "LW"}}}

    async def player_performance_games(self, identity):
        return {"playerId": identity, "performance": []}

    async def competitions(self, identities):
        return {}

    async def clubs(self, identities):
        return {"800": {"name": "Atalanta BC"}}

    async def attributes(self):
        return {}

    async def competition_table(self, identity, season):
        self.table_requests.append((identity, season))
        if self.fail:
            raise httpx.ConnectError("Temporary standings request failure")
        return serie_a_table(self.played)

    async def competition_seasons(self, identity):
        assert identity == "IT1"
        return {"seasons": [{"seasonId": 2024, "gameDays": 38}, {"seasonId": 2026, "gameDays": 38}]}

    async def player_market_value_history(self, identity):
        return {"history": [{"marketValue": {"value": 60_000_000, "currency": "EUR", "determined": "2025-06-01"}}]}


def load_standings(monkeypatch, sdk):
    monkeypatch.setattr(transfermarkt, "aggregate_performances", lambda *args: deepcopy(SAVED_TOTALS))
    return asyncio.run(transfermarkt.TransfermarktProvider(None)._load(sdk, "406040", Season(2024)))


def test_lookman_historical_serie_a_table_confirms_third_place_without_a_championship_bonus(monkeypatch):
    sdk = StandingsSdk()

    result = load_standings(monkeypatch, sdk)
    points = calculate_season_points(result, weighted_rating=7.347809651201675)

    league = result["competitions"][0]
    assert sdk.table_requests == [("IT1", "2024")]
    assert league["games"] == 31
    assert league["place"] == "3"
    assert league["competition_complete"] is True
    assert league["competition_complete_basis"] == "season_game_days_and_full_table"
    assert result["warnings"] == []
    assert points["status"] == "complete"
    assert points["pending"] == points["bonuses"] == []
    assert points["total"] == pytest.approx(734.7809651201675)


def test_lookman_optional_table_failure_remains_unknown_and_recovers_on_refresh(monkeypatch):
    sdk = StandingsSdk(fail=True)
    failed = load_standings(monkeypatch, sdk)

    pending = calculate_season_points(failed, weighted_rating=7.347809651201675)

    assert failed["competitions"][0]["place"] is None
    assert failed["competitions"][0]["competition_complete"] is None
    assert failed["warnings"] == ["Не удалось загрузить место команды в Serie A."]
    assert pending["status"] == "awaiting_results"
    assert pending["total"] is None
    assert [row["competition_id"] for row in pending["pending"]] == ["IT1"]
    sdk.fail = False

    refreshed = load_standings(monkeypatch, sdk)

    assert refreshed["warnings"] == []
    assert calculate_season_points(refreshed, weighted_rating=7.347809651201675)["status"] == "complete"
    assert failed["competitions"][0]["competition_complete"] is None


def test_lookman_appearance_count_cannot_confirm_an_unfinished_serie_a_season(monkeypatch):
    result = load_standings(monkeypatch, StandingsSdk(played=31))

    points = calculate_season_points(result, weighted_rating=7.347809651201675)

    assert result["competitions"][0]["games"] == 31
    assert result["competitions"][0]["competition_complete"] is False
    assert points["status"] == "awaiting_results"
    assert points["total"] is None
