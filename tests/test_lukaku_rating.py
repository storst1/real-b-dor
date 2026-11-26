from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.match_context import normalize_team, resolve_match_context
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def lukaku_sources():
    games = [
        ("4172134", "4315115", "2023-12-14", "FC Sheriff Tiraspol", "FC Sheriff", 8.7),
        ("4172136", "4315112", "2023-11-09", "SK Slavia Prague", "Slavia Prague", 5.6),
        ("4172133", "4315109", "2023-10-26", "SK Slavia Prague", "Slavia Prague", 7.6),
        ("4172139", "4315105", "2023-09-21", "FC Sheriff Tiraspol", "FC Sheriff", 8.2),
    ]
    tm = {"profile": {"id": "96341", "name": "Romelu Lukaku", "position": "CF"}, "competitions": [{
        "id": "EL", "name": "UEFA Europa League", "category": "continental", "competition_type_id": 10,
        "team": "AS Roma", "games": 4, "stage_code": "HFR", "stage_history": ["G", "HFH", "HFR"],
        "matches": [{"id": tm_id, "date": day, "team": "AS Roma", "opponent": official, "stage_code": "G"}
                    for tm_id, _, day, official, _, _ in games],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": "73", "name": "Europa League", "category": "continental", "team": "Roma",
        "games": 4, "rated_games": 4, "rating": 7.525,
        "matches": [{"id": fm_id, "date": day, "team": "Roma", "opponent": short,
                     "competition_id": "73", "competition": "Europa League", "category": "continental",
                     "is_national": False, "stage": None, "rating": rating}
                    for _, fm_id, day, _, short, rating in games],
    }]}
    return tm, fm


@pytest.mark.parametrize("legacy", [False, True])
def test_all_four_real_lukaku_sheriff_and_slavia_group_matches_keep_weight_one(legacy):
    tm, fm = lukaku_sources()
    if legacy:
        for match in fm["competitions"][0]["matches"]:
            match.pop("category")
            match.pop("is_national")
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == weighted["history_games"] == 4
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == 4
    assert weighted["raw_rating"] == pytest.approx(7.525)
    assert weighted["rating"] == pytest.approx(7.525)
    assert {match["id"] for match in weighted["matches"]} == {"4315115", "4315112", "4315109", "4315105"}
    for match in weighted["matches"]:
        assert match["context"]["competition_id"] == "EL"
        assert match["context"]["stage_code"] == "G"
        assert match["context"]["stage"] == "group"
        assert match["context"]["is_knockout"] is False
        assert match["weight"] == 1
        assert [rule["key"] for rule in match["weight_rules"]] == ["base"]


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_sheriff_and_slavia_aliases_do_not_merge_youth_womens_or_reserve_teams(index, suffix):
    tm, fm = lukaku_sources()
    row = tm["competitions"][0]
    tm_match = row["matches"][index]
    fm_match = fm["competitions"][0]["matches"][index]
    assert normalize_team(tm_match["opponent"]) == normalize_team(fm_match["opponent"])
    tm_match["opponent"] += suffix

    context = resolve_match_context(fm_match, [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


@pytest.mark.parametrize("stage", [None, "Unknown phase"])
def test_lukaku_unknown_match_rounds_cannot_be_filled_with_parent_semifinal_history(stage):
    tm, fm = lukaku_sources()
    for match in tm["competitions"][0]["matches"]:
        match["stage_code"] = stage

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is weighted["raw_rating"] is None
    assert set(weighted["unresolved_match_ids"]) == {"4315115", "4315112", "4315109", "4315105"}
    assert all(match["weight"] is None for match in weighted["matches"])


@pytest.mark.parametrize("index", [0, 1])
def test_conflicting_sheriff_or_slavia_opponent_still_blocks_the_match(index):
    tm, fm = lukaku_sources()
    row = tm["competitions"][0]
    row["matches"][index]["opponent"] = "Different Club"

    context = resolve_match_context(fm["competitions"][0]["matches"][index], [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


def test_saved_lukaku_recalculates_without_reimport_and_retains_only_no_champions_league_penalty(tmp_path):
    tm, fm = lukaku_sources()
    # Preserve the actual season's participation/results alongside the compact
    # four-match source rating sample; Belgium appearances avoid a second penalty.
    tm["competitions"][0]["games"] = 13
    fm["competitions"][0]["games"] = 13
    tm["competitions"].extend([
        {"id": "IT1", "name": "Serie A", "category": "domestic_league", "team": "AS Roma",
         "games": 32, "place": "6", "competition_complete": True},
        {"id": "EURO", "name": "UEFA Euro", "category": "national_major", "competition_type_id": 11,
         "team": "Belgium", "games": 4, "stage_code": "AF"},
    ])
    original = deepcopy((tm, fm))
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "96341", combine(tm, fm), tm, fm)

    first = store.player(identity)
    repeated = store.player(identity)
    reopened = Store(store.path).player(identity)

    assert first["weighted_rating"]["status"] == "complete"
    assert first["weighted_rating"]["rated_games"] == first["weighted_rating"]["history_games"] == 4
    assert repeated["weighted_rating"] == reopened["weighted_rating"] == first["weighted_rating"]
    assert repeated["updated_at"] == reopened["updated_at"] == first["updated_at"]
    assert first["season_points"]["status"] == "complete"
    assert {row["kind"]: row["points"] for row in first["season_points"]["bonuses"]} == {"no_champions_league": -25}
    assert first["season_points"]["bonus_points"] == -25
    assert first["season_points"]["total"] == pytest.approx(7.525 * 100 - 25)
    assert store.raw(identity) == original
