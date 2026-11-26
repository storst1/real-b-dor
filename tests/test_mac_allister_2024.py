from copy import deepcopy

import pytest

from season_ledger.match_context import normalize_team
from season_ledger.weighting import calculate_weighted_rating


def liverpool_bologna_snapshot():
    return {"position": "CM", "competitions": [{
        "id": "CL", "name": "UEFA Champions League", "category": "continental", "competition_type_id": 10,
        "team": "Liverpool FC", "rating": 8.3, "rated_games": 1,
        "matches": [{
            "id": "4445050", "date": "2024-10-02T19:00:00+00:00", "team": "Liverpool FC",
            "opponent": "Bologna FC 1909", "stage_code": "GP",
        }],
        "fotmob_matches": [{
            "id": "4621555", "date": "2024-10-02", "kickoff": "2024-10-02T19:00:00+00:00",
            "team": "Liverpool", "opponent": "Bologna", "competition_id": "42", "competition": "Champions League",
            "stage": None, "rating": 8.3, "minutes": 90,
        }],
    }]}


def test_real_mac_allister_bologna_match_resolves_the_uefa_league_phase():
    snapshot = liverpool_bologna_snapshot()
    original = deepcopy(snapshot)

    weighted = calculate_weighted_rating(snapshot)

    assert snapshot == original
    assert normalize_team("Bologna FC 1909") == normalize_team("Bologna")
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 1
    assert weighted["total_weight"] == 1.5
    assert weighted["raw_rating"] == pytest.approx(8.3)
    assert weighted["rating"] == pytest.approx(8.3 / 1.0021)
    match = weighted["matches"][0]
    assert match["id"] == "4621555"
    assert match["context"]["competition_id"] == "CL"
    assert match["context"]["stage_code"] == "GP"
    assert match["context"]["stage"] == "league"
    assert match["context"]["is_knockout"] is False
    assert {rule["key"] for rule in match["weight_rules"]} == {"base", "champions_league"}


@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_bologna_official_name_alias_keeps_youth_womens_and_reserve_teams_distinct(suffix):
    snapshot = liverpool_bologna_snapshot()
    snapshot["competitions"][0]["matches"][0]["opponent"] += suffix

    weighted = calculate_weighted_rating(snapshot)

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is None
    assert weighted["unresolved_match_ids"] == ["4621555"]


def test_bologna_alias_does_not_fill_a_missing_league_phase():
    snapshot = liverpool_bologna_snapshot()
    snapshot["competitions"][0]["matches"][0].pop("stage_code")

    weighted = calculate_weighted_rating(snapshot)

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is None
    assert weighted["matches"][0]["weight"] is None
