"""Verified Basel/Necaxa source pairs from the saved 2025/26 season."""

from copy import deepcopy

import pytest

from season_ledger.domain import combine
from season_ledger.weighting import calculate_weighted_rating


CASES = {
    "rogers": {
        "profile": {"id": "503743", "name": "Morgan Rogers", "position": "LW"},
        "team": "Aston Villa", "tm_comp": "EL", "tm_name": "UEFA Europa League",
        "fm_comp": "73", "fm_name": "Europa League", "fm_category": "continental",
        "tm_id": "4715991", "fm_id": "4947848", "kickoff": "2025-12-11T20:00:00+00:00",
        "official": "FC Basel 1893", "short": "Basel", "rating": 6.1, "minutes": 25,
        "is_home": False, "home_score": 1, "away_score": 2, "coefficient": 1.0096,
    },
    "lewandowski": {
        "profile": {"id": "38253", "name": "Robert Lewandowski", "position": "CF"},
        "team": "Chicago Fire FC", "tm_comp": "USMX", "tm_name": "Leagues Cup",
        "fm_comp": "10043", "fm_name": "Leagues Cup", "fm_category": "domestic_cup",
        "tm_id": "4944202", "fm_id": "5844836", "kickoff": "2026-08-07T00:30:00+00:00",
        "official": "Club Necaxa", "short": "Necaxa", "rating": 6.7, "minutes": 64,
        "is_home": True, "home_score": 2, "away_score": 0, "coefficient": 1,
    },
}


def saved_sources(kind):
    case = CASES[kind]
    scored, conceded = ((case["home_score"], case["away_score"]) if case["is_home"]
                        else (case["away_score"], case["home_score"]))
    tm = {"profile": deepcopy(case["profile"]), "competitions": [{
        "id": case["tm_comp"], "name": case["tm_name"], "category": "continental",
        "competition_type_id": 10, "season_id": "2025", "season_ids": ["2025"],
        "team": case["team"], "games": 1, "matches": [{
            "id": case["tm_id"], "date": case["kickoff"], "team": case["team"],
            "competition_id": case["tm_comp"], "competition_type_id": 10, "season_id": "2025",
            "opponent": case["official"], "stage_code": "GP", "is_home": case["is_home"],
            "goals_for": scored, "goals_against": conceded, "minutes": case["minutes"], "goals": 0, "assists": 0,
        }],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": case["fm_comp"], "name": case["fm_name"], "category": case["fm_category"],
        "team": case["team"], "games": 1, "rated_games": 1, "rating": case["rating"], "matches": [{
            "id": case["fm_id"], "date": case["kickoff"][:10], "kickoff": case["kickoff"],
            "competition_id": case["fm_comp"], "competition": case["fm_name"], "category": case["fm_category"],
            "team": case["team"], "opponent": case["short"], "stage": None, "is_national": False,
            "rating": case["rating"], "minutes": case["minutes"], "goals": 0, "assists": 0,
            "is_home": case["is_home"], "home_score": case["home_score"], "away_score": case["away_score"],
        }],
    }]}
    return tm, fm


@pytest.mark.parametrize("kind", CASES)
def test_real_source_pair_is_included_with_weight_one_and_unchanged_score(kind):
    case = CASES[kind]
    tm, fm = saved_sources(kind)
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert snapshot["competitions"][0]["matches"] == tm["competitions"][0]["matches"]
    assert snapshot["competitions"][0]["category"] == "continental"
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 1
    assert weighted["total_weight"] == 1
    assert weighted["raw_rating"] == case["rating"]
    assert weighted["rating"] == pytest.approx(case["rating"] / case["coefficient"])
    match = weighted["matches"][0]
    assert match["id"] == case["fm_id"]
    assert match["date"] == case["kickoff"][:10]
    assert match["kickoff"] == case["kickoff"]
    assert match["rating"] == match["raw_rating"] == case["rating"]
    assert (match["home_score"], match["away_score"]) == (case["home_score"], case["away_score"])
    assert match["context"]["competition_id"] == case["tm_comp"]
    assert match["context"]["stage_code"] == "GP"
    assert match["context"]["stage"] == "league"
    assert match["context"]["is_knockout"] is False
    assert match["context"]["is_national"] is False
    assert match["weight"] == 1
    assert {rule["key"] for rule in match["weight_rules"]} == {"base"}


@pytest.mark.parametrize("kind", CASES)
def test_opponent_alias_cannot_supply_a_missing_stage(kind):
    tm, fm = saved_sources(kind)
    tm["competitions"][0]["matches"][0]["stage_code"] = "unknown stage"

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "incomplete_context"
    assert weighted["included_games"] == 0
    assert weighted["unresolved_match_ids"] == [CASES[kind]["fm_id"]]
    assert weighted["matches"][0]["context"]["stage"] is None


@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_verified_opponent_alias_preserves_age_womens_and_reserve_team_identity(suffix):
    tm, fm = saved_sources("rogers")
    tm["competitions"][0]["matches"][0]["opponent"] += suffix

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "incomplete_context"
    assert weighted["included_games"] == 0
    assert weighted["unresolved_match_ids"] == ["4947848"]
    assert weighted["matches"][0]["context"]["match_context_status"] == "unmatched"
