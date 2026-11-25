import asyncio
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from season_ledger.domain import Season, combine
from season_ledger.importer import Importer
from season_ledger.providers.fotmob import FotMobProvider, _aggregate_matches
from season_ledger.store import Store


PROFILE = {"id": "344695", "name": "Dayot Upamecano", "birth_date": "1998-10-27",
           "position": "CB", "clubs": [{"id": "27", "name": "FC Bayern Munich"}]}
FOTMOB = {"id": 658554, "name": "Dayot Upamecano", "birthDate": {"utcTime": "1998-10-27T00:00:00Z"},
          "primaryTeam": {"teamId": 9823, "teamName": "Bayern München"},
          "careerHistory": {"careerItems": {"national team": {"teamEntries": [{"teamId": 6723, "team": "France"}]}}}}


def source_competitions():
    return [{"id": "L1", "name": "Bundesliga", "category": "domestic_league", "competition_type_id": 1,
             "team": "FC Bayern Munich", "team_id": "27", "games": 1,
             "goals": 0, "assists": 0, "minutes": 90, "place": "3", "competition_complete": True,
             "matches": [{"id": "tm-hoffenheim", "date": "2024-05-18T13:30:00Z",
                          "team": "FC Bayern Munich", "opponent": "TSG Hoffenheim"}]}]


def history_match(identity="4222385", day="2024-05-18", rating="5.8", *, league=54, name="Bundesliga", national=False, stage=None):
    return {"id": int(identity), "matchDate": {"utcTime": day + "T19:00:00Z"},
            "teamId": 6723 if national else 9823, "teamName": "France" if national else "Bayern München",
            "opponentTeamId": 8226, "opponentTeamName": "Hoffenheim",
            "leagueId": league, "leagueName": name, "stage": stage,
            "playedInMatch": True, "minutesPlayed": 90, "goals": 0, "assists": 0,
            "ratingProps": {"rating": rating}}


def run_history(handler, competitions=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await FotMobProvider(client)._matches("658554", Season(2023), competitions)
    return asyncio.run(run())


def test_empty_initial_history_retries_the_same_verified_range_once_and_recovers_real_matches():
    requested = []

    def handler(request):
        requested.append(request.url.params["url"])
        if len(requested) == 1:
            return httpx.Response(200, json={"matches": [], "previous": None})
        return httpx.Response(200, json={"matches": [history_match()], "previous": None})

    rows = run_history(handler, source_competitions())

    assert len(requested) == 2
    assert requested[0] == requested[1]
    assert parse_qs(urlsplit(requested[0]).query)["before"] == ["1725148800"]
    assert rows[0]["id"] == 4222385
    aggregated = _aggregate_matches(rows, FOTMOB, Season(2023), source_competitions())
    assert aggregated[0]["games"] == aggregated[0]["rated_games"] == 1
    assert aggregated[0]["rating"] == 5.8


def test_persistently_empty_response_with_dated_tm_appearances_is_a_bounded_failure():
    calls = []

    def handler(request):
        calls.append(request.url.params["url"])
        return httpx.Response(200, json={"matches": [], "previous": None})

    with pytest.raises(ValueError, match="не вернул историю матчей"):
        run_history(handler, source_competitions())
    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_history_only_before_the_target_season_is_not_successful_empty_coverage():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"matches": [history_match(day="2023-06-19")], "previous": None})

    with pytest.raises(ValueError, match="не вернул историю матчей"):
        run_history(handler, source_competitions())
    assert len(calls) == 1


@pytest.mark.parametrize("competitions", [None, []])
def test_empty_history_without_any_source_appearances_remains_a_valid_empty_result(competitions):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"matches": [], "previous": None})

    assert run_history(handler, competitions) == []
    assert len(calls) == 1


def test_paginated_upamecano_source_keeps_unrated_appearances_and_closes_only_after_lower_bound():
    # Real source IDs from four separate portions of his 2023/24 history;
    # the minute-long Arsenal appearance has no published rating.
    page_one = [history_match("4043983", "2024-07-09", "6.4", league=50, name="EURO", national=True, stage="1/2")]
    page_two = [history_match("4446280", "2024-04-17", None, league=42, name="Champions League", stage="1/4")]
    page_three = [history_match("4315006", "2023-12-12", "7.9", league=42, name="Champions League")]
    page_four = [history_match("4185612", "2023-08-12", "6.9", league=8924, name="Super Cup", stage="final"),
                 history_match("1", "2023-06-19", "7.0", league=10607, name="EURO Qualification", national=True)]
    pages = [page_one, page_two, page_three, page_four]
    cursors = [1725148800, 1720551600, 1713380400, 1702411200]
    requested = []
    competitions = [{"id": "CL", "category": "continental", "matches": [
        {"id": "tm-arsenal", "date": "2024-04-17"}, {"id": "tm-united", "date": "2023-12-12"},
    ]}, {"id": "DFL", "category": "domestic_cup", "matches": [{"id": "tm-supercup", "date": "2023-08-12"}]}]

    def handler(request):
        before = int(parse_qs(urlsplit(request.url.params["url"]).query)["before"][0])
        requested.append(before)
        index = cursors.index(before)
        previous = (f"https://pub.fotmob.com/prod/db/api/player/658554/matches?before={cursors[index + 1]}"
                    if index < 3 else None)
        return httpx.Response(200, json={"matches": pages[index], "previous": previous})

    rows = run_history(handler, competitions)
    aggregated = _aggregate_matches(rows, FOTMOB, Season(2023), competitions)

    assert requested == cursors
    assert len(rows) == 5
    assert sum(row["games"] for row in aggregated) == 4
    assert sum(row["rated_games"] for row in aggregated) == 3
    cl = next(row for row in aggregated if row["id"] == "42")
    assert cl["games"] == 2 and cl["rated_games"] == 1
    assert cl["rating"] == 7.9


def test_empty_source_refresh_preserves_the_last_confirmed_fotmob_snapshot(tmp_path):
    store = Store(tmp_path / "history.sqlite3")
    store.add_season(Season(2023))
    tm = {"profile": PROFILE, "competitions": source_competitions()}
    old_matches = _aggregate_matches([history_match()], FOTMOB, Season(2023), tm["competitions"])
    old_fm = {"status": "matched", "player_id": "658554", "player_name": "Dayot Upamecano",
              "competitions": old_matches, "warnings": []}
    identity = store.save_player("2023-24", "344695", combine(tm, old_fm), tm, old_fm)
    original = deepcopy(store.raw(identity)[1])
    history_calls = []

    class TM:
        async def load(self, *args):
            return deepcopy(tm)

    def handler(request):
        if request.url.path.endswith("playerData"):
            return httpx.Response(200, json=FOTMOB)
        history_calls.append(request.url.params["url"])
        return httpx.Response(200, json={"matches": [], "previous": None})

    async def refresh():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            importer = Importer(store, client, tm=TM(), fm=FotMobProvider(client))
            job = store.create_job("2023-24", "344695", player_id=identity)
            await importer.process(job)
            return store.job(job["id"])

    job = asyncio.run(refresh())
    saved = store.raw(identity)[1]
    player = store.player(identity)
    assert job["status"] == "completed"
    assert len(history_calls) == 2
    assert saved["status"] == "matched"
    assert saved["competitions"] == original["competitions"]
    assert any("сохранены оценки предыдущей успешной загрузки" in warning for warning in saved["warnings"])
    assert player["weighted_rating"]["status"] == "complete"
    assert player["weighted_rating"]["rated_games"] == 1
