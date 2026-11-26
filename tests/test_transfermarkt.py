import asyncio
from datetime import date, timedelta

import httpx
import pytest

from season_ledger.domain import Season
from season_ledger.providers.transfermarkt import TransfermarktProvider, aggregate_performances, category, historical_value, league_completion, resolve_final


def game(identity, day, competition="L1", kind=1, *, season=2023, goals=0, assists=0, minutes=90,
         state="played", national=False, game_state="regularly_terminated", team="27"):
    return {
        "gameInformation": {"gameId": str(identity), "date": {"dateTimeUTC": f"{day}T19:00:00+00:00"},
                            "competitionId": competition, "competitionTypeId": kind, "seasonId": season,
                            "isNationalGame": national, "gameState": game_state},
        "clubsInformation": {"club": {"clubId": team}},
        "statistics": {"generalStatistics": {"participationState": state},
                       "goalStatistics": {"goalsScoredTotal": goals, "assists": assists},
                       "playingTimeStatistics": {"playedMinutes": minutes}},
    }


def test_afcon_identity_classifies_the_final_tournament_even_without_a_competition_name():
    assert category({"competitionId": "AFCN", "competitionTypeId": 11, "isNationalGame": True}) == "national_major"
    assert category({"competitionId": "AFCQ", "competitionTypeId": 19, "isNationalGame": True}) == "national_regular"


def test_valuation_uses_last_observation_before_cutoff_never_current_future():
    data = {"history": [
        {"marketValue": {"value": 110_000_000, "currency": "EUR", "determined": "2023-12-14"}},
        {"marketValue": {"value": 100_000_000, "currency": "EUR", "determined": "2024-05-29"}},
        {"marketValue": {"value": 90_000_000, "currency": "EUR", "determined": "2024-12-20"}},
    ], "current": {"marketValue": {"value": 60_000_000, "currency": "EUR", "determined": "2026-05-27"}}}
    assert historical_value(data, date(2024, 6, 30)) == {
        "amount": 100_000_000, "currency": "EUR", "observed_on": "2024-05-29", "as_of": "2024-06-30"}
    assert historical_value(data, date(2020, 6, 30)) is None


def test_dated_current_is_eligible_but_wrong_currency_is_not():
    assert historical_value({"current": {"marketValue": {"value": 1_000_000, "currency": "EUR", "determined": "2024-06-30"}}}, date(2024, 6, 30))["amount"] == 1_000_000
    assert historical_value({"history": [{"marketValue": {"value": 100, "currency": "GBP", "determined": "2024-01-01"}}]}, date(2024, 6, 30)) is None


def test_dated_retirement_zero_supersedes_old_positive_value():
    # Real raw API shape: Kroos retired on July 7, 2024 with a dated EUR0
    # history observation after the last EUR10m update on June 7.
    data = {"history": [
        {"marketValue": {"value": 10_000_000, "currency": "EUR", "determined": "2024-06-07"}},
        {"marketValue": {"value": 0, "currency": "EUR", "determined": "2024-07-07"}},
    ]}
    assert historical_value(data, date(2024, 6, 30))["amount"] == 10_000_000
    assert historical_value(data, date(2025, 6, 30)) == {
        "amount": 0, "currency": "EUR", "observed_on": "2024-07-07", "as_of": "2025-06-30"}
    assert historical_value({"current": {"marketValue": {"value": 0, "currency": "EUR"}}}, date(2025, 6, 30)) is None
    assert historical_value({"current": {"marketValue": {"value": 0, "currency": "EUR", "determined": "2024-07-07"}}}, date(2025, 6, 30))["amount"] == 0


def test_season_boundary_full_summer_euro_and_no_double_count_next_season():
    games = [game(1, "2024-05-01", goals=2), game(2, "2024-07-01", goals=7, season=2024),
             game(3, "2024-06-30", "EURO", 11, national=True, goals=1, team="3299", game_state="extra_time"),
             game(4, "2024-07-06", "EURO", 11, national=True, team="3299", game_state="penalty_shootout"),
             game(5, "2024-07-14", "EURO", 11, national=True, team="3299"),
             game(6, "2024-07-20", "FS", 11, national=True, team="3299", season=2024)]
    rows = aggregate_performances(games, Season(2023), {}, {})
    assert next(row for row in rows if row["id"] == "L1")["games"] == 1
    euro = next(row for row in rows if row["id"] == "EURO")
    assert euro["games"] == 3
    assert euro["category"] == "national_major"
    assert euro["goals"] == 1
    assert not any(row["id"] == "FS" for row in rows)
    next_season = aggregate_performances(games, Season(2024), {}, {})
    assert not any(row["id"] == "EURO" for row in next_season)


def test_unknown_values_deduplication_bench_and_transfer_clubs():
    first = game(1, "2023-09-01", goals=2)
    rows = aggregate_performances([first, first, game(2, "2023-09-10", goals=None),
                                  game(3, "2023-09-15", goals=99, state="in squad"),
                                  game(4, "2024-02-10", goals=3, team="148")], Season(2023), {}, {})
    assert len(rows) == 2
    original = next(row for row in rows if row["team_id"] == "27")
    assert original["games"] == 2
    assert original["goals"] is None
    assert original["assists"] == 0
    assert original["minutes"] == 180


def test_calendar_season_uses_dates_instead_of_provider_season_ids():
    rows = aggregate_performances([game(1, "2023-02-01", season=2022), game(2, "2023-09-01"), game(3, "2024-01-01")], Season(2023, calendar=True), {}, {})
    assert rows[0]["games"] == 2
    assert rows[0]["season_ids"] == ["2022", "2023"]


def test_club_season_keeps_delayed_covid_matches_and_excludes_next_season():
    games = [game(1, "2020-07-19", "GB1", season=2019),
             game(2, "2020-08-23", "CL", 10, season=2019),
             game(3, "2020-09-05", "CL", 10, season=2019),
             game(4, "2020-08-12", "GB1", season=2020),
             game(5, "2020-09-01", "CL", 10, season=2020)]
    rows = aggregate_performances(games, Season(2019), {}, {})
    assert next(row for row in rows if row["id"] == "GB1")["games"] == 1
    assert next(row for row in rows if row["id"] == "CL")["games"] == 2
    assert {row["season_id"] for row in rows} == {"2019"}
    next_rows = aggregate_performances(games, Season(2020), {}, {})
    assert next(row for row in next_rows if row["id"] == "GB1")["games"] == 1
    assert next(row for row in next_rows if row["id"] == "CL")["games"] == 1


def test_club_summer_tournament_before_july_uses_provider_season_identity():
    records = [game(1, "2023-06-20", "KLUB", 10, season=2023),
               game(2, "2023-07-01", "KLUB", 10, season=2022)]
    rows = aggregate_performances(records, Season(2023), {}, {})
    assert rows[0]["games"] == 1
    assert rows[0]["matches"][0]["id"] == "1"


def test_future_records_are_not_counted_even_if_marked_finished():
    future = date.today() + timedelta(days=10)
    season = Season(future.year, calendar=True)
    assert aggregate_performances([game(1, future.isoformat(), season=future.year)], season, {}, {}) == []


def test_knockout_stage_recognizes_two_leg_codes():
    record = game(1, "2024-05-08", "CL", 10)
    record["gameInformation"]["competitionGroupId"] = "HFR"
    rows = aggregate_performances([record], Season(2023), {}, {})
    assert rows[0]["place"] == "Полуфинал"


def final_game(identity, day, scored, conceded, *, stage="FF", match_state="regularly_terminated", participation="played", season=2023, opponent="13"):
    record = game(identity, day, "CL", 10, game_state=match_state, state=participation, season=season)
    record["gameInformation"]["competitionGroupId"] = stage
    record["clubsInformation"]["club"].update(goalsTotal=scored, opponentGoalsTotal=conceded)
    record["clubsInformation"]["opponent"] = {"clubId": opponent, "goalsTotal": conceded, "opponentGoalsTotal": scored}
    return record


@pytest.mark.parametrize(("scored", "conceded", "match_state", "expected"), [
    (2, 0, "regularly_terminated", "1"),
    (1, 2, "regularly_terminated", "2"),
    (5, 4, "extra_time", "1"),
    (1, 2, "extra_time", "2"),
    # Verified raw performance totals include the shootout on top of goals:
    # Ramos CL2016 = 6:4; Kane EURO2021 = 3:4.
    (6, 4, "penalty_shootout", "1"),
    (3, 4, "penalty_shootout", "2"),
])
def test_single_final_resolves_regulation_extra_time_and_shootout(scored, conceded, match_state, expected):
    record = final_game(1, "2024-06-01", scored, conceded, match_state=match_state)
    row = aggregate_performances([record], Season(2023), {}, {})[0]
    assert row["place"] == expected
    assert row["final_outcome"] == ("winner" if expected == "1" else "runner_up")
    assert row["stage_code"] == "FF"
    assert row["final_result"]["basis"] == "single_match_score"
    assert row["final_result"]["includes_shootout"] == (match_state == "penalty_shootout")


def test_team_final_outcome_uses_final_even_when_player_did_not_appear():
    semifinal = final_game(1, "2024-05-08", 2, 0, stage="HFR")
    final = final_game(2, "2024-06-01", 2, 0, participation="in squad")
    row = aggregate_performances([semifinal, final], Season(2023), {}, {})[0]
    assert row["games"] == 1
    assert [{k: item[k] for k in ("id", "date")} for item in row["matches"]] == [{"id": "1", "date": "2024-05-08T19:00:00+00:00"}]
    assert row["stage_history"] == ["HFR", "FF"]
    assert row["place"] == "1"
    assert row["final_result"]["game_ids"] == ["2"]


@pytest.mark.parametrize(("first", "second", "expected"), [
    ((3, 0), (0, 1), "1"),  # Lost the second leg, won the final.
    ((0, 4), (1, 1), "2"),  # The draw alone says nothing about the tie.
    ((0, 3), (1, 0), "2"),  # Won the second leg, lost the final.
])
def test_two_legged_final_uses_both_legs(first, second, expected):
    records = [final_game(1, "2024-05-01", *first, stage="FFH"),
               final_game(2, "2024-05-08", *second, stage="FFR")]
    result = resolve_final(records)
    assert result["place"] == expected
    assert result["final_result"]["basis"] == "aggregate_score"
    assert result["final_result"]["game_ids"] == ["1", "2"]


def test_final_keeps_unknown_for_missing_scores_or_tied_shootout_feed():
    missing = final_game(1, "2024-06-01", None, None)
    tied = final_game(2, "2024-06-01", 1, 1, match_state="penalty_shootout")
    conflicting = final_game(3, "2024-06-01", 2, 1)
    conflicting["clubsInformation"]["opponent"]["goalsTotal"] = 3
    for record in (missing, tied, conflicting):
        result = resolve_final([record])
        assert result["place"] == "Финал"
        assert result["final_outcome"] is None


def test_two_legged_final_does_not_guess_on_incomplete_legs_or_away_goals_tie():
    first = final_game(1, "2024-05-01", 2, 3, stage="FFH")
    second = final_game(2, "2024-05-08", 2, 1, stage="FFR")
    # 4:4 could be decided by an away-goals rule, which is not present here.
    for records in ([first], [second], [first, second]):
        assert resolve_final(records)["place"] == "Финал"
        assert resolve_final(records)["final_outcome"] is None
    mismatched = final_game(3, "2024-05-01", 3, 0, stage="FFH", opponent="999")
    assert resolve_final([mismatched, second])["final_outcome"] is None


def test_qualifiers_and_nations_league_remain_regular():
    assert category({"competitionId": "WMQ6", "competitionTypeId": 19}, {"name": "World Cup qualification"}) == "national_regular"
    assert category({"competitionId": "UNLA", "competitionTypeId": 11}) == "national_regular"
    assert category({"competitionId": "20WC", "competitionTypeId": 17}) == "other"


def test_copa_america_main_and_centenario_ids_are_senior_major_tournaments():
    for identity in ("COPA", "CA16"):
        assert category({"competitionId": identity, "competitionTypeId": 11}) == "national_major"


def league_table(count=6, *, playing=False):
    return {"meta": {"seasonId": 2026}, "tables": [{"clubs": [
        {"clubId": str(index), "ranking": {"current": index}, "group": {"name": "", "division": ""},
         "game": {"totalCount": count, "isPlaying": playing}}
        for index in range(1, 5)
    ]}]}


def test_league_completion_uses_requested_season_schedule_and_complete_table():
    seasons = {"seasons": [{"seasonId": 2023, "gameDays": 6}, {"seasonId": 2026, "gameDays": 10}]}
    # Upstream table.meta names today's season even for historical responses.
    assert league_completion(league_table(6), seasons, "2023") is True
    assert league_completion(league_table(4), seasons, "2023") is False
    assert league_completion(league_table(6, playing=True), seasons, "2023") is False
    assert league_completion(league_table(6), seasons, "2026") is False


def test_league_completion_does_not_guess_for_missing_schedule_or_complex_table():
    seasons = {"seasons": [{"seasonId": 2023, "gameDays": 6}]}
    assert league_completion(league_table(), {}, "2023") is None
    assert league_completion(league_table(), {"seasons": [{"seasonId": 2023, "gameDays": 0}]}, "2023") is None
    assert league_completion(league_table(7), seasons, "2023") is None
    grouped = league_table()
    grouped["tables"][0]["clubs"][0]["group"]["name"] = "Championship Round"
    assert league_completion(grouped, seasons, "2023") is None
    multi = league_table()
    multi["tables"].append(league_table()["tables"][0])
    assert league_completion(multi, seasons, "2023") is None
    unknown_live = league_table()
    unknown_live["tables"][0]["clubs"][0]["game"].pop("isPlaying")
    assert league_completion(unknown_live, seasons, "2023") is None


def test_match_details_preserve_actual_metadata_without_changing_season_totals():
    record = final_game(1, "2024-06-01", 6, 4, match_state="penalty_shootout")
    record["clubsInformation"]["club"]["venue"] = "neutral"
    record["statistics"]["goalStatistics"].update(goalsScoredTotal=1, assists=2)
    record["statistics"]["generalStatistics"]["positionId"] = 14
    record["statistics"]["playingTimeStatistics"].update(playedMinutes=120, isStarting=True)
    competitions = {"CL": {"name": "UEFA Champions League", "typeId": 10}}
    clubs = {"27": {"name": "Test Team"}, "13": {"name": "Opponent Team"}}
    row = aggregate_performances([record], Season(2023), competitions, clubs)[0]
    detail = row["matches"][0]
    assert (row["games"], row["goals"], row["assists"], row["minutes"]) == (1, 1, 2, 120)
    assert row["competition_type_id"] == 10
    assert detail == {
        "id": "1", "date": "2024-06-01T19:00:00+00:00", "team_id": "27", "team": "Test Team",
        "opponent_id": "13", "opponent": "Opponent Team", "competition_id": "CL", "competition": "UEFA Champions League",
        "competition_type_id": 10, "season_id": "2023", "stage_code": "FF", "venue": "neutral", "is_home": None,
        "goals_for": 6, "goals_against": 4, "score_includes_shootout": True,
        "minutes": 120, "goals": 1, "assists": 2, "position_id": 14, "started": True, "game_state": "penalty_shootout",
    }


def test_stage_history_retains_semifinal_before_third_place_and_bench_games():
    semi = final_game(1, "2024-05-01", 0, 1, stage="HF", participation="in squad")
    bronze = final_game(2, "2024-05-08", 2, 0, stage="SPIEL3")
    row = aggregate_performances([semi, bronze], Season(2023), {}, {})[0]
    assert row["stage_history"] == ["HF", "SPIEL3"]
    assert row["games"] == 1
    assert row["place"] is None


@pytest.mark.parametrize(("season_year", "match_day"), [(2023, "2024-05-01"), (2019, "2020-09-05"), (2019, "2019-06-20")])
def test_provider_uses_sdk_shared_client_and_season_parameter(season_year, match_day):
    paths = []

    def respond(request):
        paths.append(str(request.url))
        path = request.url.path
        if path == "/player/132098":
            data = {"id": "132098", "name": "Harry Kane", "lifeDates": {"dateOfBirth": "1993-07-28"},
                    "nationalityDetails": {"nationalities": {"nationalityId": 189}},
                    "attributes": {"position": {"shortName": "CF"}}}
        elif path.endswith("performance-game"):
            data = {"playerId": "132098", "performance": [game(1, match_day, goals=2, season=season_year)]}
        elif path == "/competitions":
            data = [{"id": "L1", "name": "Bundesliga", "typeId": 1}]
        elif path == "/clubs":
            data = [{"id": "27", "name": "FC Bayern Munich", "baseDetails": {"isNationalTeam": False}}]
        elif path == "/attributes":
            data = {"countries": [{"id": 189, "name": "England"}]}
        elif path.endswith("/table"):
            assert request.url.params["season"] == str(season_year)
            data = {"tables": [{"clubs": [{"clubId": "27", "ranking": {"current": 3}}]}]}
        elif path.endswith("/season"):
            data = {"seasons": [{"seasonId": season_year, "gameDays": 34}]}
        elif path.endswith("market-value-history"):
            data = {"history": [{"marketValue": {"value": 100_000_000, "currency": "EUR", "determined": f"{season_year + 1}-05-29"}}]}
        else:
            raise AssertionError(path)
        return httpx.Response(200, json={"success": True, "data": data})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await TransfermarktProvider(client).load("132098", Season(season_year))
            assert not client.is_closed
            return result

    result = asyncio.run(run())
    assert result["profile"]["nationalities"] == ["England"]
    assert result["profile"]["clubs"][0]["id"] == "27"
    assert result["competitions"][0]["place"] == "3"
    assert result["market_value"]["amount"] == 100_000_000
    assert all("tmapi.transfermarkt.technology" in path for path in paths)
