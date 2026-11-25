import asyncio
from copy import deepcopy
from urllib.parse import urlsplit

import httpx
import pytest

from season_ledger.domain import Season
from season_ledger.providers.fotmob import FotMobProvider


TM_PROFILE = {
    "id": "105470", "name": "Alisson", "birth_date": "1992-10-02",
    "clubs": [{"id": "31", "name": "Liverpool FC"}],
}
FM_PROFILE = {
    "id": 319784, "name": "Alisson Becker", "birthDate": {"utcTime": "1992-10-02T00:00:00Z"},
    "primaryTeam": {"teamId": 8650, "teamName": "Liverpool"},
}
FM_MATCH = {
    "id": 4193617, "matchDate": {"utcTime": "2024-04-01T19:00:00Z"},
    "teamId": 8650, "teamName": "Liverpool", "opponentTeamId": 9825, "opponentTeamName": "Arsenal",
    "leagueId": 47, "leagueName": "Premier League", "playedInMatch": True,
    "minutesPlayed": 90, "goals": 0, "assists": 0, "ratingProps": {"rating": "7.5"},
}


def load_identity(*, profile=None, candidates=None, player_id=None):
    tm = deepcopy(TM_PROFILE if profile is None else profile)
    players = deepcopy([FM_PROFILE] if candidates is None else candidates)
    history_ids = []

    def handler(request):
        if request.url.path.endswith("search/suggest"):
            return httpx.Response(200, json=[{"suggestions": [
                {"type": "player", "id": str(row["id"]), "name": row["name"]} for row in players
            ]}])
        if request.url.path.endswith("playerData"):
            return httpx.Response(200, json=next(row for row in players if str(row["id"]) == request.url.params["id"]))
        assert request.url.path.endswith("playerMatches")
        history_ids.append(urlsplit(request.url.params["url"]).path.split("/")[-2])
        return httpx.Response(200, json={"matches": [FM_MATCH], "previous": None})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await FotMobProvider(client).load(tm, Season(2023), player_id=player_id)

    return asyncio.run(run()), history_ids


def test_real_alisson_alias_selects_confirmed_liverpool_player_and_loads_ratings():
    other = {**FM_PROFILE, "id": 449684, "name": "Alisson", "birthDate": {"utcTime": "1993-06-25"}}
    result, loaded = load_identity(candidates=[other, FM_PROFILE])

    assert result["status"] == "matched"
    assert result["player_id"] == "319784"
    assert result["player_name"] == "Alisson Becker"
    assert result["warnings"] == []
    assert loaded == ["319784"]
    assert result["competitions"][0]["rated_games"] == 1
    assert result["competitions"][0]["rating"] == 7.5


def test_verified_alias_is_symmetric_and_can_use_a_historical_club():
    profile = {**TM_PROFILE, "name": "Alisson Becker"}
    candidate = {**FM_PROFILE, "name": "Alisson", "primaryTeam": {"teamName": "Roma"},
                 "careerHistory": {"careerItems": {"senior": {"teamEntries": [{"team": "Liverpool"}]}}}}

    result, loaded = load_identity(profile=profile, candidates=[candidate])

    assert result["status"] == "matched"
    assert loaded == ["319784"]


@pytest.mark.parametrize("profile", [
    {**TM_PROFILE, "birth_date": None},
    {**TM_PROFILE, "birth_date": "1993-10-02"},
    {**TM_PROFILE, "clubs": []},
    {**TM_PROFILE, "clubs": [{"name": "Arsenal"}]},
])
def test_alisson_alias_requires_birthday_and_corroborating_club(profile):
    result, loaded = load_identity(profile=profile)

    assert result["status"] == "ambiguous"
    assert result["player_id"] is None
    assert result["competitions"] == []
    assert loaded == []


def test_missing_upstream_birthday_cannot_confirm_alisson_alias():
    result, loaded = load_identity(candidates=[{**FM_PROFILE, "birthDate": None}])

    assert result["status"] == "ambiguous"
    assert loaded == []


def test_matching_birthday_and_club_do_not_allow_arbitrary_shared_given_names():
    result, loaded = load_identity(candidates=[{**FM_PROFILE, "name": "Alisson Fernandes"}])

    assert result["status"] == "ambiguous"
    assert loaded == []


def test_two_corroborated_alisson_candidates_stay_ambiguous():
    result, loaded = load_identity(candidates=[FM_PROFILE, {**FM_PROFILE, "id": 319785}])

    assert result["status"] == "ambiguous"
    assert result["player_id"] is None
    assert loaded == []


def test_manual_selection_accepts_the_same_confirmed_alias():
    result, loaded = load_identity(player_id="319784")

    assert result["status"] == "matched"
    assert loaded == ["319784"]


@pytest.mark.parametrize("profile", [
    {**TM_PROFILE, "birth_date": None},
    {**TM_PROFILE, "birth_date": "1993-10-02"},
    {**TM_PROFILE, "clubs": []},
    {**TM_PROFILE, "clubs": [{"name": "Arsenal"}]},
])
def test_manual_selection_does_not_bypass_alias_identity_evidence(profile):
    result, loaded = load_identity(profile=profile, player_id="319784")

    assert result["status"] == "not_found"
    assert result["player_id"] is None
    assert loaded == []
