"""Saved Enzo Fernández appearances that need exact opponent aliases."""

from copy import deepcopy

import pytest

from season_ledger.domain import combine
from season_ledger.match_context import normalize_team
from season_ledger.weighting import calculate_weighted_rating


CASES = {
    "esperance": {
        "tm_competition": "KLUB", "tm_name": "FIFA Club World Cup", "fm_competition": "78",
        "fm_name": "FIFA Club World Cup", "tm_id": "4506852", "fm_id": "4685731",
        "kickoff": "2025-06-25T01:00:00+00:00", "official": "Esperance Tunis", "short": "Espérance",
        "tm_opponent_id": "3342", "fm_opponent_id": "8153", "stage_code": "D", "stage": "group",
        "rating": 8.5, "tm_minutes": 67, "fm_minutes": 68, "assists": 2, "position_id": 7,
        "is_home": False, "goals_for": 3, "goals_against": 0,
    },
    "noah": {
        "tm_competition": "UCOL", "tm_name": "UEFA Conference League", "fm_competition": "10216",
        "fm_name": "Conference League", "tm_id": "4445563", "fm_id": "4622186",
        "kickoff": "2024-11-07T20:00:00+00:00", "official": "FC Noah Yerevan", "short": "FC Noah",
        "tm_opponent_id": "60368", "fm_opponent_id": "866111", "stage_code": "GP", "stage": "league",
        "rating": 9.4, "tm_minutes": 45, "fm_minutes": 45, "assists": 3, "position_id": 6,
        "is_home": True, "goals_for": 8, "goals_against": 0,
    },
}


def enzo_sources(*kinds):
    """Use the fields and values saved for player 166, without a live DB dependency."""
    tm = {"profile": {"id": "648195", "name": "Enzo Fernández", "position": "CM"}, "competitions": []}
    fm = {"status": "matched", "competitions": []}
    for kind in kinds:
        case = CASES[kind]
        tm_match = {
            "id": case["tm_id"], "date": case["kickoff"], "team_id": "631", "team": "Chelsea FC",
            "opponent_id": case["tm_opponent_id"], "opponent": case["official"],
            "competition_id": case["tm_competition"], "competition": case["tm_name"],
            "competition_type_id": 10, "season_id": "2024", "stage_code": case["stage_code"],
            "venue": "home" if case["is_home"] else "away", "is_home": case["is_home"],
            "goals_for": case["goals_for"], "goals_against": case["goals_against"],
            "score_includes_shootout": False, "minutes": case["tm_minutes"], "goals": 0,
            "assists": case["assists"], "position_id": case["position_id"], "started": True,
            "game_state": "regularly_terminated",
        }
        fm_match = {
            "id": case["fm_id"], "date": case["kickoff"][:10], "kickoff": case["kickoff"],
            "source": "FotMob", "rating": case["rating"], "minutes": case["fm_minutes"],
            "goals": 0, "assists": case["assists"], "team_id": "8455", "team": "Chelsea",
            "opponent_id": case["fm_opponent_id"], "opponent": case["short"],
            "competition_id": case["fm_competition"], "competition": case["fm_name"],
            "category": "continental", "is_national": False, "is_home": case["is_home"],
            "home_score": case["goals_for"] if case["is_home"] else case["goals_against"],
            "away_score": case["goals_against"] if case["is_home"] else case["goals_for"], "stage": None,
        }
        tm["competitions"].append({
            "id": case["tm_competition"], "name": case["tm_name"], "category": "continental",
            "competition_type_id": 10, "team": "Chelsea FC", "games": 1,
            "season_id": "2024", "season_ids": ["2024"], "matches": [tm_match],
        })
        fm["competitions"].append({
            "id": case["fm_competition"], "name": case["fm_name"], "category": "continental",
            "team": "Chelsea", "games": 1, "rated_games": 1, "rating": case["rating"], "matches": [fm_match],
        })
    return tm, fm


@pytest.mark.parametrize("kind", CASES)
def test_saved_enzo_opponent_pair_resolves_stage_without_champions_league_addition(kind):
    case = CASES[kind]
    tm, fm = enzo_sources(kind)
    original = deepcopy((tm, fm))
    assert normalize_team(case["official"]) == normalize_team(case["short"])

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert not snapshot["competitions"][0]["is_champions_league"]
    assert not snapshot["competitions"][0]["is_supercup"]
    assert snapshot["competitions"][0]["matches"] == tm["competitions"][0]["matches"]
    assert snapshot["competitions"][0]["fotmob_matches"] == fm["competitions"][0]["matches"]
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 1
    assert weighted["unresolved_match_ids"] == []
    assert weighted["missing_source_samples"] == []
    assert weighted["total_weight"] == 1
    assert weighted["raw_rating"] == case["rating"]
    assert weighted["rating"] == pytest.approx(case["rating"] / 1.0021)
    match = weighted["matches"][0]
    assert match["id"] == case["fm_id"]
    assert match["rating"] == match["raw_rating"] == case["rating"]
    assert match["stage"] is None
    assert match["weight"] == 1
    assert {rule["key"] for rule in match["weight_rules"]} == {"base"}
    assert match["context"]["competition_id"] == case["tm_competition"]
    assert match["context"]["stage_code"] == case["stage_code"]
    assert match["context"]["stage"] == case["stage"]
    assert match["context"]["match_context_status"] == "matched"
    assert match["context"]["is_knockout"] is False
    assert match["context"]["is_national"] is False


def test_both_enzo_matches_are_retained_in_the_rating_arithmetic():
    tm, fm = enzo_sources("esperance", "noah")

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 2
    assert weighted["total_weight"] == 2
    assert weighted["raw_rating"] == pytest.approx((8.5 + 9.4) / 2)
    assert weighted["rating"] == pytest.approx((8.5 / 1.0021 + 9.4 / 1.0021) / 2)
    assert {match["id"] for match in weighted["matches"]} == {"4685731", "4622186"}


@pytest.mark.parametrize("kind", CASES)
@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_enzo_aliases_preserve_age_womens_and_reserve_team_identity(kind, suffix):
    tm, fm = enzo_sources(kind)
    tm["competitions"][0]["matches"][0]["opponent"] += suffix

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "incomplete_context"
    assert weighted["included_games"] == 0
    assert weighted["unresolved_match_ids"] == [CASES[kind]["fm_id"]]
    assert weighted["matches"][0]["context"]["match_context_status"] == "unmatched"
