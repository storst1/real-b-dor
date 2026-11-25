import asyncio
from copy import deepcopy
from datetime import date, datetime, UTC
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from season_ledger.domain import Season
from season_ledger.providers.fotmob import FotMobProvider, _aggregate_matches, _previous_before


PROFILE = {"id": "132098", "name": "Harry Kane", "birth_date": "1993-07-28",
           "clubs": [{"id": "27", "name": "Bayern Munich"}]}
FOTMOB = {"id": 194165, "name": "Harry Kane", "birthDate": {"utcTime": "1993-07-28T00:00:00.000Z"},
          "primaryTeam": {"teamId": 9823, "teamName": "Bayern München"},
          "careerHistory": {"careerItems": {"national team": {
              "teamEntries": [{"teamId": 8491, "team": "England"}]}}}}


def match(identity, day, rating="8.0", *, national=False, league=54, league_name="Bundesliga", played=True, **extra):
    return {"id": identity, "matchDate": {"utcTime": f"{day}T19:00:00.000Z"},
            "teamId": 8491 if national else 9823, "teamName": "England" if national else "Bayern München",
            "leagueId": league, "leagueName": league_name, "playedInMatch": played,
            "minutesPlayed": 90 if played else 0, "goals": 1, "assists": 0,
            "ratingProps": {"rating": rating, "isTopRating": False}, **extra}


def run(handler, *, season=Season(2023), profile=PROFILE, player_id=None):
    async def load():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await FotMobProvider(client).load(profile, season, player_id)
    return asyncio.run(load())


def search_response(request):
    return httpx.Response(200, json=[{"title": {"key": "all", "value": "All"}, "suggestions": [
        {"type": "player", "id": "194165", "name": "Harry Kane", "teamName": "Bayern München"}
    ]}, {"suggestions": [{"type": "player", "id": "194165", "name": "Harry Kane"}]}])


def test_real_shape_pagination_deduplicates_and_preserves_rating_denominator():
    calls = []
    first = [match(1, "2024-07-14", "7.0", national=True, league=50, league_name="EURO"),
             match(2, "2024-07-21", "10"),  # next club season
             match(3, "2024-06-01", "9.0"),
             match(4, "2024-06-01", "0", played=False)]
    second = [match(3, "2024-06-01", "9.0"), match(5, "2024-05-01", "7.0"),
              match(6, "2023-08-18", "0"),
              match(7, "2023-07-14", "8.0", national=True, league=50, league_name="EURO"),
              match(8, "2023-08-01", "9.0", league=489, league_name="Club Friendlies"),
              match(9, "2023-06-20", "10")]

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("search/suggest"):
            return search_response(request)
        if request.url.path.endswith("playerData"):
            return httpx.Response(200, json=FOTMOB)
        assert request.url.path.endswith("playerMatches")
        url = urlsplit(request.url.params["url"])
        assert url.hostname == "pub.fotmob.com"
        assert url.path == "/prod/db/api/player/194165/matches"
        before = parse_qs(url.query)["before"][0]
        if before == "1725148800":
            return httpx.Response(200, json={"matches": first, "previous":
                "https://pub.fotmob.com/prod/db/api/player/194165/matches?before=1717200000&leagueId=&teamId="})
        assert before == "1717200000"
        return httpx.Response(200, json={"matches": second, "previous": None})

    result = run(handler)
    assert result["status"] == "matched"
    assert result["player_id"] == "194165"
    assert len(calls) == 4  # duplicate search suggestions hydrate only once
    league, euro = result["competitions"]
    assert league["category"] == "domestic_league"
    assert league["games"] == 3
    assert league["rating"] == 8
    assert league["rated_games"] == 2
    assert league["minutes"] == 270
    assert euro["category"] == "national_major"
    assert euro["games"] == 1
    assert euro["rating"] == 7


def test_same_name_wrong_date_of_birth_is_not_automatically_selected():
    def handler(request):
        if request.url.path.endswith("search/suggest"):
            return search_response(request)
        return httpx.Response(200, json={**FOTMOB, "birthDate": {"utcTime": "2001-07-28T00:00:00Z"}})
    result = run(handler)
    assert result["status"] == "ambiguous"
    assert result["player_id"] is None
    assert result["competitions"] == []


def test_two_players_with_same_name_and_birthday_remain_ambiguous():
    def handler(request):
        if request.url.path.endswith("search/suggest"):
            return httpx.Response(200, json=[{"suggestions": [
                {"type": "player", "id": "194165", "name": "Harry Kane"},
                {"type": "player", "id": "194166", "name": "Harry Kane"}]}])
        return httpx.Response(200, json={**FOTMOB, "id": int(request.url.params["id"])})
    assert run(handler)["status"] == "ambiguous"


def test_manual_selection_rejects_different_birthday():
    result = run(lambda _: httpx.Response(200, json={**FOTMOB, "birthDate": {"utcTime": "2001-07-28"}}),
                 player_id="194165")
    assert result["status"] == "not_found"
    assert result["competitions"] == []
    assert "Дата рождения" in result["warnings"][0]


def test_missing_birthday_requires_manual_selection_without_corroborating_club():
    def handler(request):
        return search_response(request) if request.url.path.endswith("search/suggest") else httpx.Response(200, json=FOTMOB)
    result = run(handler, profile={**PROFILE, "birth_date": None, "clubs": []})
    assert result["status"] == "ambiguous"


@pytest.mark.parametrize("rating", ["0", "NaN", "Infinity", "-2", "11", None, True])
def test_invalid_or_unrated_appearance_does_not_enter_rating_average(rating):
    rows = [match(1, "2024-04-01", "8.3"), match(2, "2024-04-02", rating)]
    item = _aggregate_matches(rows, FOTMOB, Season(2023))[0]
    assert item["games"] == 2
    assert item["rating"] == 8.3
    assert item["rated_games"] == 1


def test_missing_stat_is_unknown_instead_of_zero():
    rows = [match(1, "2024-04-01", goals=None), match(2, "2024-04-02")]
    item = _aggregate_matches(rows, FOTMOB, Season(2023))[0]
    assert item["goals"] is None
    assert item["assists"] == 0


def test_regular_national_qualification_filtered_by_actual_match_date():
    rows = [match(1, "2023-06-19", national=True, league=10607, league_name="EURO Qualification"),
            match(2, "2023-11-20", "7.8", national=True, league=10607, league_name="EURO Qualification")]
    item = _aggregate_matches(rows, FOTMOB, Season(2023))[0]
    assert item["category"] == "national_regular"
    assert item["games"] == 1
    assert item["rating"] == 7.8


def test_calendar_season_does_not_shift_summer_finals():
    rows = [match(1, "2024-07-14", national=True, league=50, league_name="EURO"),
            match(2, "2023-11-20", national=True)]
    assert _aggregate_matches(rows, FOTMOB, Season(2024, calendar=True))[0]["games"] == 1


def test_failed_pagination_does_not_publish_a_partial_season_average():
    def handler(request):
        if request.url.path.endswith("playerData"):
            return httpx.Response(200, json=FOTMOB)
        if "1725148800" in request.url.params["url"]:
            return httpx.Response(200, json={"matches": [match(1, "2024-05-01")], "previous":
                "https://pub.fotmob.com/prod/db/api/player/194165/matches?before=1714521600"})
        return httpx.Response(429)
    result = run(handler, player_id="194165")
    assert result["status"] == "unavailable"
    assert result["competitions"] == []
    assert "ограничил" in result["warnings"][0]


def test_upstream_previous_url_never_redirects_requests_to_another_host():
    with pytest.raises(ValueError):
        _previous_before("https://evil.example/prod/db/api/player/194165/matches?before=123", "194165")
    with pytest.raises(ValueError):
        _previous_before("https://pub.fotmob.com/prod/db/api/player/999/matches?before=123", "194165")


def test_player_id_mismatch_is_invalid_profile():
    result = run(lambda _: httpx.Response(200, json={**FOTMOB, "id": 123}), player_id="194165")
    assert result["status"] == "unavailable"
    assert result["competitions"] == []


def test_delayed_club_season_uses_transfermarkt_match_dates_not_june_cutoff():
    competitions = [{"id": "CL", "name": "UEFA Champions League", "category": "continental",
                     "team": "Bayern Munich", "matches": [{"id": "tm-1", "date": "2020-08-23T19:00:00Z"}]}]
    rows = [match(1, "2020-08-23", "9", league=42, league_name="Champions League"),
            match(2, "2020-08-29", "5", league=42, league_name="Champions League"),
            match(3, "2020-06-01", "6")]
    item = _aggregate_matches(rows, FOTMOB, Season(2019), competitions)[0]
    assert item["name"] == "Champions League"
    assert item["games"] == 1
    assert item["rating"] == 9
    assert [{key: row[key] for key in ("id", "date")} for row in item["matches"]] == [{"id": "1", "date": "2020-08-23"}]


def test_calendar_club_season_keeps_date_bounds_even_with_source_competitions():
    competitions = [{"category": "continental", "matches": [{"id": "tm-1", "date": "2020-08-23"}]}]
    rows = [match(1, "2020-08-23"), match(2, "2020-05-01"), match(3, "2019-12-01")]
    item = _aggregate_matches(rows, FOTMOB, Season(2020, calendar=True), competitions)[0]
    assert item["games"] == 2


def test_history_bounds_expand_to_source_match_dates_before_and_after_nominal_season():
    competitions = [{"category": "continental", "matches": [
        {"id": "tm-1", "date": "2019-06-20T19:00:00Z"},
        {"id": "tm-2", "date": "2020-09-10T19:00:00Z"}]}]
    before = int(datetime(2020, 9, 11, tzinfo=UTC).timestamp())
    cursors = []

    def handler(request):
        if request.url.path.endswith("playerData"):
            return httpx.Response(200, json=FOTMOB)
        cursor = int(parse_qs(urlsplit(request.url.params["url"]).query)["before"][0])
        cursors.append(cursor)
        if cursor == before:
            return httpx.Response(200, json={"matches": [match(1, "2020-09-10"), match(2, "2019-06-25")],
                "previous": "https://pub.fotmob.com/prod/db/api/player/194165/matches?before=1561420800"})
        return httpx.Response(200, json={"matches": [match(3, "2019-06-20"), match(4, "2019-06-01")], "previous": None})

    result = run(handler, season=Season(2019), profile={**PROFILE, "season_competitions": competitions}, player_id="194165")
    assert result["status"] == "matched"
    assert len(cursors) == 2
    assert cursors[0] == before
    assert result["competitions"][0]["games"] == 2


def identity_result(profile, data, *, player_id=None):
    def handler(request):
        if request.url.path.endswith("search/suggest"):
            return httpx.Response(200, json=[{"suggestions": [
                {"type": "player", "id": str(data["id"]), "name": data["name"]}]}])
        if request.url.path.endswith("playerData"):
            return httpx.Response(200, json=data)
        return httpx.Response(200, json={"matches": [], "previous": None})
    return run(handler, profile=profile, player_id=player_id)


@pytest.mark.parametrize(("tm_name", "fm_name"), [("Daniel Carvajal", "Dani Carvajal"), ("Bukayo Saka", "Saka")])
def test_unique_name_alias_uses_same_birthday_surname_and_club(tm_name, fm_name):
    profile = {**PROFILE, "name": tm_name, "clubs": [{"name": "Real Madrid CF"}]}
    data = {**FOTMOB, "name": fm_name, "primaryTeam": {"teamName": "Real Madrid"}}
    result = identity_result(profile, data)
    assert result["status"] == "matched"
    assert result["player_name"] == fm_name


def test_auto_alias_can_corroborate_historical_club():
    profile = {**PROFILE, "name": "Francesc Fabregas", "clubs": [{"name": "FC Arsenal"}]}
    data = {**FOTMOB, "name": "Cesc Fabregas", "primaryTeam": {"teamName": "Como"},
            "careerHistory": {"careerItems": {"senior": {"teamEntries": [{"team": "Arsenal"}]}}}}
    assert identity_result(profile, data)["status"] == "matched"


def test_auto_alias_without_corroborating_club_stays_ambiguous():
    profile = {**PROFILE, "name": "Daniel Carvajal", "clubs": [{"name": "Real Madrid"}]}
    data = {**FOTMOB, "name": "Dani Carvajal"}
    assert identity_result(profile, data)["status"] == "ambiguous"


def test_manual_selection_rejects_unrelated_name_despite_same_birthday():
    result = identity_result(PROFILE, {**FOTMOB, "name": "John Smith"}, player_id="194165")
    assert result["status"] == "not_found"
    assert result["player_id"] is None


def test_manual_alias_with_matching_birthday_and_surname_is_accepted():
    profile = {**PROFILE, "name": "Daniel Carvajal"}
    result = identity_result(profile, {**FOTMOB, "name": "Dani Carvajal"}, player_id="194165")
    assert result["status"] == "matched"


def test_manual_alias_with_missing_birthday_is_not_sufficient():
    profile = {**PROFILE, "name": "Daniel Carvajal"}
    result = identity_result(profile, {**FOTMOB, "name": "Dani Carvajal", "birthDate": None}, player_id="194165")
    assert result["status"] == "not_found"


def test_manual_exact_name_can_be_confirmed_when_birthday_is_missing():
    result = identity_result(PROFILE, {**FOTMOB, "birthDate": None}, player_id="194165")
    assert result["status"] == "matched"


def test_shared_middle_given_name_does_not_establish_alias():
    profile = {**PROFILE, "name": "Harry Edward Kane"}
    result = identity_result(profile, {**FOTMOB, "name": "John Edward Smith"}, player_id="194165")
    assert result["status"] == "not_found"


def test_saved_history_keeps_verified_match_context_without_changing_season_aggregate():
    row = match(4446289, "2024-04-30", "8.3", league=42, league_name="Champions League",
                opponentTeamId=8633, opponentTeamName="Real Madrid", isHomeTeam=True,
                homeScore=2, awayScore=2, stage="1/2",
                matchPageUrl="/matches/real-madrid-vs-bayern-munchen/2tes97#4446289")
    before = deepcopy(row)
    item = _aggregate_matches([row], FOTMOB, Season(2023))[0]
    assert item["games"] == item["rated_games"] == 1
    assert item["rating"] == 8.3
    assert item["goals"] == 1
    assert item["assists"] == 0
    assert item["minutes"] == 90
    assert item["matches"] == [{
        "id": "4446289", "date": "2024-04-30", "kickoff": "2024-04-30T19:00:00+00:00",
        "source": "FotMob", "rating": 8.3, "minutes": 90, "goals": 1, "assists": 0,
        "team_id": "9823", "team": "Bayern München", "opponent_id": "8633", "opponent": "Real Madrid",
        "competition_id": "42", "competition": "Champions League", "is_home": True,
        "home_score": 2, "away_score": 2, "stage": "1/2",
        "url": "https://www.fotmob.com/matches/real-madrid-vs-bayern-munchen/2tes97#4446289",
    }]
    assert row == before


def test_missing_or_invalid_optional_history_context_does_not_remove_an_appearance():
    row = match(1, "2024-04-01", "0", minutesPlayed=None, goals=None, assists=None,
                opponentTeamId=True, opponentTeamName=" ", isHomeTeam="false",
                homeScore="?", awayScore=None, stage=12, matchPageUrl="https://evil.example/match")
    item = _aggregate_matches([row], FOTMOB, Season(2023))[0]
    saved = item["matches"][0]
    assert item["games"] == 1
    assert item["rated_games"] == 0
    assert item["rating"] is None
    for key in ("rating", "minutes", "goals", "assists", "opponent_id", "opponent", "is_home",
                "home_score", "away_score", "stage", "url"):
        assert saved[key] is None


def test_history_stage_metadata_does_not_enable_any_match_weighting():
    rows = [match(1, "2024-04-01", "6.0", stage="final"),
            match(2, "2024-04-02", "8.0", stage=None)]
    item = _aggregate_matches(rows, FOTMOB, Season(2023))[0]
    assert item["games"] == item["rated_games"] == len(item["matches"]) == 2
    assert item["rating"] == 7
    assert [row["stage"] for row in item["matches"]] == ["final", None]
    assert [row["rating"] for row in item["matches"]] == [6.0, 8.0]


def test_saved_history_keeps_away_side_and_deduplicates_source_matches():
    row = match(1, "2024-04-01", "8.0", isHomeTeam=False, homeScore=2, awayScore=1,
                opponentTeamId=6720, opponentTeamName="Spain")
    duplicate = {**row, "ratingProps": {"rating": "1.0"}, "isHomeTeam": True}
    item = _aggregate_matches([row, duplicate], FOTMOB, Season(2023))[0]
    assert item["games"] == 1
    assert item["rating"] == 8
    assert len(item["matches"]) == 1
    assert item["matches"][0]["is_home"] is False
    assert item["matches"][0]["home_score"] == 2
    assert item["matches"][0]["away_score"] == 1


def test_saved_history_preserves_unrated_appearances_without_an_invented_zero_rating():
    rows = [match(1, "2024-04-01", "7.2"), match(2, "2024-04-02", "0")]
    item = _aggregate_matches(rows, FOTMOB, Season(2023))[0]
    assert item["games"] == 2
    assert item["rated_games"] == 1
    assert item["rating"] == 7.2
    assert [row["rating"] for row in item["matches"]] == [7.2, None]
