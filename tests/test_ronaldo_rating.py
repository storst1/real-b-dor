from copy import deepcopy

import pytest

from season_ledger.domain import Season
from season_ledger.match_context import normalize_team, resolve_match_context
from season_ledger.providers.fotmob import _aggregate_matches
from season_ledger.weighting import calculate_weighted_rating


def ronaldo_al_taee_match():
    return {
        "teamId": 101918, "teamName": "Al Nassr FC", "opponentTeamId": 101917,
        "opponentTeamName": "Al Taee", "isHomeTeam": True, "id": 4256089,
        "matchDate": {"utcTime": "2024-03-30T19:00:00.000Z"},
        "leagueId": 536, "leagueName": "Saudi Pro League", "stage": None,
        "homeScore": 5, "awayScore": 1, "minutesPlayed": 90, "goals": 3, "assists": 0,
        "ratingProps": {"rating": "9.7"}, "playedInMatch": True,
    }


def ronaldo_saudi_competition():
    return {
        "id": "SA1", "name": "Saudi Pro League", "category": "domestic_league",
        "team": "Al-Nassr FC", "team_id": "18544", "competition_type_id": 1,
        "season_id": "2023", "games": 1,
        "matches": [{
            "id": "4138334", "date": "2024-03-29T23:00:00+00:00", "team_id": "18544",
            "team": "Al-Nassr FC", "opponent_id": "40079", "opponent": "Al-Tai FC",
            "competition_id": "SA1", "competition_type_id": 1, "season_id": "2023",
            "stage_code": None, "is_home": True, "goals_for": 5, "goals_against": 1,
            "minutes": 90, "goals": 3, "assists": 0, "game_state": "regularly_terminated",
        }],
    }


@pytest.mark.parametrize("competition_id", ["536", None])
def test_saudi_regular_league_weights_use_sufficient_fotmob_context_without_tm(competition_id):
    match = {
        "id": "4256089", "date": "2024-03-30", "team": "Al Nassr FC", "opponent": "Al Taee",
        "competition_id": competition_id, "competition": "Saudi Pro League", "stage": None, "rating": 9.7,
    }
    parent = {"id": "SA1", "name": "Saudi Pro League", "category": "domestic_league",
              "rated_games": 1, "fotmob_matches": [match], "matches": []}

    result = calculate_weighted_rating({"position": "CF", "competitions": [parent]})

    assert result["status"] == "complete"
    assert result["rating"] == 9.7
    assert result["total_weight"] == 1
    context = result["matches"][0]["context"]
    assert context["competition_provider"] == "fotmob"
    assert context["competition_kind"] == "league"
    assert context["stage"] is None


def ronaldo_istiklol_competition():
    return {
        "id": "AFCL", "name": "AFC Champions League (- 2024)", "category": "continental",
        "team": "Al-Nassr FC", "team_id": "18544", "competition_type_id": 10,
        "games": 1, "rated_games": 1,
        "matches": [{"id": "4166325", "date": "2023-10-02", "team": "Al-Nassr FC",
                     "opponent": "Istiqlol Dushanbe", "stage_code": "E"}],
        "fotmob_matches": [{"id": "4309083", "date": "2023-10-02", "team": "Al Nassr FC",
                            "opponent": "FC Istiklol", "competition_id": "525",
                            "competition": "AFC Champions League Elite", "stage": None,
                            "category": "continental", "is_national": False, "rating": 8.5}],
    }


def test_real_istiklol_name_variant_recovers_tm_group_without_uefa_weights():
    parent = ronaldo_istiklol_competition()
    original = deepcopy(parent)

    result = calculate_weighted_rating({"position": "CF", "competitions": [parent]})

    assert parent == original
    assert result["status"] == "complete"
    assert result["rating"] == 8.5
    assert result["total_weight"] == 1
    match = result["matches"][0]
    assert match["context"]["competition_id"] == "AFCL"
    assert match["context"]["stage_code"] == "E"
    assert match["context"]["stage"] == "group"
    assert [rule["key"] for rule in match["weight_rules"]] == ["base"]


def test_missing_istiklol_round_in_both_sources_is_not_assumed_to_be_group():
    parent = ronaldo_istiklol_competition()
    parent["matches"][0].pop("stage_code")

    result = calculate_weighted_rating({"position": "CF", "competitions": [parent]})

    assert result["status"] == "incomplete_context"
    assert result["rating"] is None
    assert result["unresolved_match_ids"] == ["4309083"]


@pytest.mark.parametrize(("official", "short"), [
    ("Istiqlol Dushanbe", "FC Istiklol"), ("Al-Tai FC", "Al Taee"),
])
@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_ronaldo_aliases_preserve_distinct_team_identity(official, short, suffix):
    assert normalize_team(official) == normalize_team(short)
    assert normalize_team(official + suffix) != normalize_team(short)


def test_real_twenty_hour_source_disagreement_retains_ronaldo_hat_trick_rating():
    match, competition = ronaldo_al_taee_match(), ronaldo_saudi_competition()
    original = deepcopy((match, competition))

    rows = _aggregate_matches([match], {}, Season(2023), [competition])

    assert (match, competition) == original
    assert len(rows) == 1
    assert rows[0]["games"] == rows[0]["rated_games"] == 1
    assert rows[0]["rating"] == 9.7
    assert rows[0]["matches"][0]["id"] == "4256089"
    assert rows[0]["matches"][0]["date"] == "2024-03-30"


@pytest.mark.parametrize("change", [
    "too_far", "different_team", "different_opponent", "different_score", "missing_score",
    "unplayed", "ambiguous_tm_records", "different_minutes", "different_goals",
    "different_assists", "missing_participation_stats", "missing_kickoff", "more_than_24_hours",
])
def test_nearby_date_fallback_requires_unambiguous_identical_match_evidence(change):
    match, competition = ronaldo_al_taee_match(), ronaldo_saudi_competition()
    if change == "too_far":
        competition["matches"][0]["date"] = "2024-03-28T23:00:00+00:00"
    elif change == "different_team":
        match["teamName"] = "Different Club"
    elif change == "different_opponent":
        match["opponentTeamName"] = "Al Hilal"
    elif change == "different_score":
        match["homeScore"] = 4
    elif change == "missing_score":
        match.pop("awayScore")
    elif change == "unplayed":
        match["playedInMatch"] = False
    elif change == "ambiguous_tm_records":
        other = deepcopy(competition["matches"][0])
        other["id"] = "other-tm-match"
        competition["matches"].append(other)
    elif change == "different_minutes":
        match["minutesPlayed"] = 45
    elif change == "different_goals":
        match["goals"] = 2
    elif change == "different_assists":
        match["assists"] = 1
    elif change == "missing_participation_stats":
        match.pop("minutesPlayed")
    elif change == "missing_kickoff":
        competition["matches"][0]["date"] = "2024-03-29"
    elif change == "more_than_24_hours":
        competition["matches"][0]["date"] = "2024-03-29T10:00:00+00:00"

    assert _aggregate_matches([match], {}, Season(2023), [competition]) == []
