"""Messi's saved 2025/26 cups use TM season IDs and verified categories."""

from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.providers.fotmob import _aggregate_matches
from season_ledger.providers.transfermarkt import _selected
from season_ledger.weighting import calculate_weighted_rating


CUPS = {
    "CAMC": ("Campeones Cup", "9875", 13),
    "USMX": ("Leagues Cup", "10043", 10),
}
# TM cup/match, FM match, UTC kickoff, TM/FM opponent, TM stage, rating, minutes, goals, assists, score.
MATCHES = [
    ("CAMC", "5050184", "5976428", "2026-09-17T00:00:00+00:00", "CD Cruz Azul", "Cruz Azul", "FF", 8.7, 90, 1, 1, 2, 0),
    ("USMX", "4944194", "5844828", "2026-08-05T23:30:00+00:00", "Atlético de San Luis", "Atlético de San Luis", "GP", 9.3, 90, 2, 1, 4, 2),
    ("USMX", "4944230", "5844864", "2026-08-12T23:30:00+00:00", "Club León FC", "León", "GP", 7.4, 45, 0, 0, 2, 3),
]


def messi_sources():
    """Reproduce the relevant raw player-287 fields, including the old FM category."""
    tm = {"profile": {"id": "28003", "name": "Lionel Messi", "position": "RW"}, "competitions": []}
    fm = {"status": "matched", "competitions": []}
    for cup, (name, fm_id, type_id) in CUPS.items():
        tm_matches, fm_matches = [], []
        for row in [match for match in MATCHES if match[0] == cup]:
            _, tm_match, fm_match, kickoff, official, short, code, rating, minutes, goals, assists, scored, conceded = row
            tm_matches.append({
                "id": tm_match, "date": kickoff, "competition_id": cup, "competition": name,
                "competition_type_id": type_id, "season_id": "2025", "team_id": "69261",
                "team": "Inter Miami CF", "opponent": official, "stage_code": code,
                "is_home": True, "goals_for": scored, "goals_against": conceded,
                "minutes": minutes, "goals": goals, "assists": assists, "game_state": "regularly_terminated",
            })
            fm_matches.append({
                "id": fm_match, "date": kickoff[:10], "kickoff": kickoff, "source": "FotMob",
                "competition_id": fm_id, "competition": name, "team_id": "960720",
                "team": "Inter Miami CF", "opponent": short, "category": "domestic_cup",
                "is_national": False, "stage": None, "rating": rating, "minutes": minutes,
                "goals": goals, "assists": assists, "is_home": True, "home_score": scored, "away_score": conceded,
            })
        tm["competitions"].append({
            "id": cup, "name": name, "category": "continental", "competition_type_id": type_id,
            "team": "Inter Miami CF", "season_id": "2025", "season_ids": ["2025"],
            "games": len(tm_matches), "matches": tm_matches,
        })
        fm["competitions"].append({
            "id": fm_id, "name": name, "category": "domestic_cup", "team": "Inter Miami CF",
            "games": len(fm_matches), "rated_games": len(fm_matches),
            "rating": sum(match["rating"] for match in fm_matches) / len(fm_matches), "matches": fm_matches,
        })
    return tm, fm


def history_rows(fm):
    return [{
        "id": match["id"], "matchDate": {"utcTime": match["kickoff"]},
        "leagueId": match["competition_id"], "leagueName": match["competition"],
        "teamId": match["team_id"], "teamName": match["team"], "opponentTeamName": match["opponent"],
        "isHomeTeam": match["is_home"], "homeScore": match["home_score"], "awayScore": match["away_score"],
        "playedInMatch": True, "minutesPlayed": match["minutes"], "goals": match["goals"],
        "assists": match["assists"], "ratingProps": {"rating": match["rating"]}, "stage": match["stage"],
    } for cup in fm["competitions"] for match in cup["matches"]]


def test_saved_messi_cups_repair_categories_without_adding_final_or_cl_weights():
    tm, fm = messi_sources()
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert snapshot["unmatched_fotmob_competitions"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 3
    assert weighted["total_weight"] == 3
    assert weighted["raw_rating"] == pytest.approx((8.7 + 9.3 + 7.4) / 3)
    assert weighted["rating"] == pytest.approx((8.7 + 9.3 + 7.4) / 3 / 1.0096)
    for cup in snapshot["competitions"]:
        assert cup["category"] == "continental"
        assert cup["is_supercup"] is (cup["id"] == "CAMC")
        assert cup["is_champions_league"] is False
        assert {m["id"] for m in cup["matches"]} == {row[1] for row in MATCHES if row[0] == cup["id"]}
    expected = {row[2]: row for row in MATCHES}
    for match in weighted["matches"]:
        source = expected[match["id"]]
        assert match["rating"] == match["raw_rating"] == source[7]
        assert match["context"]["competition_id"] == source[0]
        assert match["context"]["stage"] == ("final" if source[0] == "CAMC" else "league")
        assert match["weight"] == 1
        assert {rule["key"] for rule in match["weight_rules"]} == {"base"}


def test_live_cup_history_keeps_selected_tm_season_and_rejects_the_previous_edition():
    tm, fm = messi_sources()
    rows = history_rows(fm)
    # Real raw player-280 game: TM4715083 belongs to seasonId2024, despite
    # this final's date falling inside the ordinary 2025/26 calendar window.
    rows.append({
        "id": "4936819", "matchDate": {"utcTime": "2025-09-01T00:00:00+00:00"},
        "leagueId": "10043", "leagueName": "Leagues Cup", "teamId": "960720",
        "teamName": "Inter Miami CF", "opponentTeamName": "Seattle Sounders FC",
        "isHomeTeam": False, "homeScore": 3, "awayScore": 0, "playedInMatch": True,
        "minutesPlayed": 90, "goals": 0, "assists": 0, "ratingProps": {"rating": 6.1}, "stage": "final",
    })

    cups = _aggregate_matches(rows, {}, Season(2025), tm["competitions"])

    assert {cup["category"] for cup in cups} == {"continental"}
    assert {match["id"] for cup in cups for match in cup["matches"]} == {row[2] for row in MATCHES}
    assert sum(cup["games"] for cup in cups) == sum(cup["rated_games"] for cup in cups) == 3


def test_cup_selection_uses_actual_tm_season_ids_instead_of_calendar_dates():
    tm, _ = messi_sources()
    competitions = {cup["id"]: {"name": cup["name"], "typeId": cup["competition_type_id"]}
                    for cup in tm["competitions"]}
    for cup in tm["competitions"]:
        for match in cup["matches"]:
            game = {"gameInformation": {
                "gameId": match["id"], "competitionId": cup["id"], "competitionTypeId": cup["competition_type_id"],
                "seasonId": 2025, "date": {"dateTimeUTC": match["date"]}, "gameState": "regularly_terminated",
            }}
            assert _selected(game, Season(2025), competitions)
            assert not _selected(game, Season(2026), competitions)
    previous = {"gameInformation": {
        "gameId": "4715083", "competitionId": "USMX", "competitionTypeId": 10, "seasonId": 2024,
        "date": {"dateTimeUTC": "2025-09-01T00:00:00+00:00"}, "gameState": "regularly_terminated",
    }}
    assert not _selected(previous, Season(2025), competitions)
    assert _selected(previous, Season(2024), competitions)


@pytest.mark.parametrize("cup_id,match_index,fm_id", [("CAMC", 0, "5976428"), ("USMX", 1, "5844864")])
@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_messi_cup_aliases_preserve_age_womens_and_reserve_team_identity(cup_id, match_index, fm_id, suffix):
    tm, fm = messi_sources()
    cup = next(cup for cup in tm["competitions"] if cup["id"] == cup_id)
    cup["matches"][match_index]["opponent"] += suffix

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "incomplete_context"
    assert weighted["unresolved_match_ids"] == [fm_id]
    assert weighted["included_games"] == 2
