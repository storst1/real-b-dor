from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.match_context import normalize_team, resolve_match_context
from season_ledger.ratings import apply_position_adjustment
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def lookman_sources():
    games = [
        ("4172098", "4315123", "2023-11-09", 7.1),
        ("4172103", "4315122", "2023-10-26", 7.0),
    ]
    tm = {"profile": {"id": "406040", "name": "Ademola Lookman", "position": "LW"}, "competitions": [{
        "id": "EL", "name": "UEFA Europa League", "category": "continental", "competition_type_id": 10,
        "team": "Atalanta BC", "games": 2, "place": "1", "stage_code": "FF", "final_outcome": "winner",
        "matches": [{"id": tm_id, "date": day, "team": "Atalanta BC", "opponent": "SK Sturm Graz", "stage_code": "D"}
                    for tm_id, _, day, _ in games],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": "73", "name": "Europa League", "category": "continental", "team": "Atalanta",
        "games": 2, "rated_games": 2, "rating": 7.05,
        "matches": [{"id": fm_id, "date": day, "team": "Atalanta", "opponent": "Sturm Graz",
                     "competition_id": "73", "competition": "Europa League", "category": "continental",
                     "is_national": False, "stage": None, "rating": rating}
                    for _, fm_id, day, rating in games],
    }]}
    return tm, fm


@pytest.mark.parametrize("legacy", [False, True])
def test_real_lookman_sturm_graz_group_matches_keep_weight_one_without_champions_league_rules(legacy):
    tm, fm = lookman_sources()
    if legacy:
        for match in fm["competitions"][0]["matches"]:
            match.pop("category")
            match.pop("is_national")
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(apply_position_adjustment(snapshot))

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == weighted["history_games"] == 2
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == 2
    assert weighted["raw_rating"] == pytest.approx(7.05)
    assert weighted["rating"] == pytest.approx(7.05 / 1.0096)
    assert {match["id"] for match in weighted["matches"]} == {"4315123", "4315122"}
    for match in weighted["matches"]:
        assert match["context"]["competition_id"] == "EL"
        assert match["context"]["stage_code"] == "D"
        assert match["context"]["stage"] == "group"
        assert match["context"]["is_knockout"] is False
        assert match["weight"] == 1
        assert [rule["key"] for rule in match["weight_rules"]] == ["base"]


@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_sturm_graz_alias_does_not_merge_youth_womens_or_reserve_teams(suffix):
    assert normalize_team("SK Sturm Graz") == normalize_team("Sturm Graz")
    assert normalize_team("SK Sturm Graz" + suffix) != normalize_team("Sturm Graz")
    tm, fm = lookman_sources()
    row = tm["competitions"][0]
    row["matches"][0]["opponent"] += suffix

    context = resolve_match_context(fm["competitions"][0]["matches"][0], [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


@pytest.mark.parametrize("stage", [None, "Unknown phase"])
def test_lookman_missing_match_stages_cannot_use_the_competition_final_as_a_substitute(stage):
    tm, fm = lookman_sources()
    for match in tm["competitions"][0]["matches"]:
        match["stage_code"] = stage

    weighted = calculate_weighted_rating(apply_position_adjustment(combine(tm, fm)))

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is weighted["raw_rating"] is None
    assert weighted["unresolved_match_ids"] == ["4315123", "4315122"]
    assert all(match["weight"] is None for match in weighted["matches"])


def test_conflicting_sturm_graz_opponent_blocks_only_that_rated_context():
    tm, fm = lookman_sources()
    tm["competitions"][0]["matches"][0]["opponent"] = "LASK"

    weighted = calculate_weighted_rating(apply_position_adjustment(combine(tm, fm)))

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is weighted["raw_rating"] is None
    assert weighted["unresolved_match_ids"] == ["4315123"]
    assert weighted["included_games"] == 1
    assert weighted["partial_rating"] == pytest.approx(7.0 / 1.0096)


def test_saved_lookman_recalculates_europa_and_afcon_bonuses_with_champions_league_penalty_without_reimport(tmp_path):
    tm, fm = lookman_sources()
    tm["competitions"][0]["games"] = 11
    fm["competitions"][0]["games"] = 11
    tm["competitions"].extend([
        {"id": "AFCN", "name": "Africa Cup of Nations", "category": "national_major", "team": "Nigeria",
         "games": 7, "place": "2", "stage_code": "FF", "final_outcome": "runner_up"},
        {"id": "CIT", "name": "Italy Cup", "category": "domestic_cup", "competition_type_id": 8,
         "team": "Atalanta BC", "games": 3, "place": "2", "final_outcome": "runner_up"},
        {"id": "IT1", "name": "Serie A", "category": "domestic_league", "team": "Atalanta BC",
         "games": 31, "place": "4", "competition_complete": True},
    ])
    snapshot = combine(tm, fm)
    original = deepcopy((tm, fm))
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "406040", snapshot, tm, fm)

    first = store.player(identity)
    repeated = store.player(identity)
    reopened = Store(store.path).player(identity)

    assert first["weighted_rating"]["status"] == "complete"
    assert repeated["weighted_rating"] == reopened["weighted_rating"] == first["weighted_rating"]
    assert repeated["updated_at"] == reopened["updated_at"] == first["updated_at"]
    assert first["season_points"]["status"] == "complete"
    assert first["season_points"]["bonus_points"] == 8
    assert {bonus["kind"]: bonus["points"] for bonus in first["season_points"]["bonuses"]} == {
        "europa_league": 25, "afcon": 8, "no_champions_league": -25,
    }
    assert first["season_points"]["total"] == pytest.approx(7.05 / 1.0096 * 100 + 8)
    assert store.raw(identity) == original
