from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.providers.fotmob import _aggregate_matches
from season_ledger.providers.transfermarkt import aggregate_performances
from season_ledger.weighting import calculate_weighted_rating


def messi_mls_sources():
    # Each saved TM kickoff is on the preceding UTC day, within 90 minutes.
    games = [
        ("4802971", "5071068", "2026-04-04T23:30:00+00:00", "2026-04-05T00:00:00+00:00", "Austin FC", "Austin FC", 90, 1, 0, 8.7),
        ("4864997", "5071249", "2026-08-01T23:30:00+00:00", "2026-08-02T00:10:00+00:00", "Columbus Crew", "Columbus Crew", 37, 0, 0, 6.2),
        ("4865072", "5071325", "2026-09-05T23:30:00+00:00", "2026-09-06T01:00:00+00:00", "Atlanta United FC", "Atlanta United", 90, 0, 1, 7.7),
    ]
    tm = {"profile": {"id": "28003", "name": "Lionel Messi", "position": "RW"}, "competitions": [{
        "id": "MLS1", "name": "Major League Soccer", "category": "domestic_league", "competition_type_id": 1,
        "team": "Inter Miami CF", "team_id": "69261", "season_id": "2025", "games": 3,
        "matches": [{"id": tm_id, "date": tm_kickoff, "team": "Inter Miami CF", "opponent": official,
                     "competition_id": "MLS1", "competition_type_id": 1, "season_id": "2025", "is_home": True,
                     "goals_for": 2, "goals_against": 2, "minutes": minutes, "goals": goals, "assists": assists,
                     "game_state": "regularly_terminated"}
                    for tm_id, _, tm_kickoff, _, official, _, minutes, goals, assists, _ in games],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": "130", "name": "MLS", "category": "domestic_league", "team": "Inter Miami CF", "team_id": "960720",
        "games": 3, "rated_games": 3, "rating": (8.7 + 6.2 + 7.7) / 3,
        "matches": [{"id": fm_id, "date": fm_kickoff[:10], "kickoff": fm_kickoff,
                     "team": "Inter Miami CF", "team_id": "960720", "opponent": short,
                     "competition_id": "130", "competition": "MLS", "category": "domestic_league", "is_national": False,
                     "is_home": True, "home_score": 2, "away_score": 2,
                     "minutes": minutes, "goals": goals, "assists": assists, "rating": rating, "stage": None}
                    for _, fm_id, _, fm_kickoff, _, short, minutes, goals, assists, rating in games],
    }]}
    return tm, fm


def player_history_rows(fm):
    return [{
        "id": int(match["id"]), "matchDate": {"utcTime": match["kickoff"]},
        "leagueId": 130, "leagueName": "MLS", "teamId": 960720, "teamName": match["team"],
        "opponentTeamName": match["opponent"], "playedInMatch": True, "isHomeTeam": match["is_home"],
        "homeScore": match["home_score"], "awayScore": match["away_score"],
        "minutesPlayed": match["minutes"], "goals": match["goals"], "assists": match["assists"],
        "ratingProps": {"rating": match["rating"]},
    } for match in fm["competitions"][0]["matches"]]


def test_all_three_real_messi_mls_date_discrepancies_join_without_rewriting_source_dates():
    tm, fm = messi_mls_sources()
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert snapshot["unmatched_fotmob_competitions"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == weighted["history_games"] == 3
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == 3
    assert weighted["rating"] == pytest.approx((8.7 + 6.2 + 7.7) / 3 / 1.0096)
    assert {match["id"] for match in weighted["matches"]} == {"5071068", "5071249", "5071325"}
    source_dates = {match["id"]: (match["date"], match["kickoff"]) for match in fm["competitions"][0]["matches"]}
    assert {match["id"]: (match["date"], match["kickoff"]) for match in weighted["matches"]} == source_dates


@pytest.mark.parametrize("problem", ["score", "minutes", "opponent", "home", "goals", "assists", "ambiguous", "unknown_kickoff", "unfinished"])
def test_mls_adjacent_date_requires_unique_complete_corroboration(problem):
    tm, fm = messi_mls_sources()
    match = fm["competitions"][0]["matches"][0]
    tm_matches = tm["competitions"][0]["matches"]
    if problem == "score":
        match["home_score"] = 3
    elif problem == "minutes":
        match["minutes"] = 89
    elif problem == "opponent":
        match["opponent"] = "Different Club"
    elif problem == "home":
        match["is_home"] = False
    elif problem == "goals":
        match["goals"] = 0
    elif problem == "assists":
        match["assists"] = 1
    elif problem == "ambiguous":
        duplicate = deepcopy(tm_matches[0])
        duplicate["id"] = "different-source-game"
        tm_matches.append(duplicate)
    elif problem == "unknown_kickoff":
        match["kickoff"] = None
    else:
        tm_matches[0]["game_state"] = "scheduled"
    original = deepcopy((tm, fm))

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert (tm, fm) == original
    assert weighted["status"] == "missing_history"
    assert weighted["rating"] is weighted["raw_rating"] is None
    assert weighted["missing_source_samples"]
    assert weighted["included_games"] == 2


def test_mls_ingest_uses_selected_tm_edition_instead_of_fixed_june_cutoff():
    tm, fm = messi_mls_sources()
    history = player_history_rows(fm)
    # This is a real previous-edition playoff inside the 2025/26 date window:
    # TM4777678 has season_id2024 and belongs to Messi's 2024/25 collection.
    history.append({
        "id": 5025528, "matchDate": {"utcTime": "2025-12-06T19:30:00+00:00"},
        "leagueId": 130, "leagueName": "MLS", "teamId": 960720, "teamName": "Inter Miami CF",
        "opponentTeamName": "Vancouver Whitecaps", "playedInMatch": True, "minutesPlayed": 90,
        "isHomeTeam": True, "homeScore": 3, "awayScore": 1, "goals": 0, "assists": 2,
        "ratingProps": {"rating": 8.9}, "stage": "final",
    })
    original = deepcopy(history)

    imported = _aggregate_matches(history, {}, Season(2025), tm["competitions"])

    assert history == original
    assert {match["id"] for row in imported for match in row["matches"]} == {"5071068", "5071249", "5071325"}
    assert sum(row["rated_games"] for row in imported) == 3
    # Both late-summer 2026 games remain in the selected club edition.
    assert {match["date"] for row in imported for match in row["matches"]} == {"2026-04-05", "2026-08-02", "2026-09-06"}


def test_transfermarkt_season_id_controls_mls_even_when_calendar_dates_overlap():
    tm, _ = messi_mls_sources()
    records = []
    for match in tm["competitions"][0]["matches"]:
        records.append({
            "gameInformation": {"gameId": match["id"], "date": {"dateTimeUTC": match["date"]},
                                "competitionId": "MLS1", "competitionTypeId": 1, "seasonId": "2025",
                                "isNationalGame": False, "gameState": "regularly_terminated"},
            "clubsInformation": {"club": {"clubId": "69261"}, "opponent": {"clubId": "1"}},
            "statistics": {"generalStatistics": {"participationState": "played"},
                           "playingTimeStatistics": {"playedMinutes": match["minutes"]},
                           "goalStatistics": {"goalsScoredTotal": match["goals"], "assists": match["assists"]}},
        })
    old_edition = deepcopy(records[0])
    old_edition["gameInformation"].update(gameId="4777678", competitionId="POUS", competitionTypeId=12,
                                         seasonId="2024", competitionGroupId="FF", date={"dateTimeUTC": "2025-12-06T19:30:00+00:00"})
    records.append(old_edition)

    rows = aggregate_performances(records, Season(2025),
                                  {"MLS1": {"name": "Major League Soccer", "typeId": 1}, "POUS": {"name": "MLS Cup Playoffs", "typeId": 12}},
                                  {"69261": {"name": "Inter Miami CF"}, "1": {"name": "Opponent"}})

    assert sum(row["games"] for row in rows) == 3
    assert {match["id"] for row in rows for match in row["matches"]} == {"4802971", "4864997", "4865072"}
    assert {row["season_id"] for row in rows} == {"2025"}
