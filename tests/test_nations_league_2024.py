from copy import deepcopy
import math

import pytest

from season_ledger.domain import Season, combine
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def spain_sources():
    # Actual Yamal appearances: one FotMob league contains TM's two phases.
    games = [
        ("4284267", "4422026", "2024-09-05", "Serbia", "4", None, 7.9),
        ("4284264", "4422028", "2024-09-08", "Switzerland", "4", None, 8.0),
        ("4284258", "4422030", "2024-10-12", "Denmark", "4", None, 7.9),
        ("4498075", "4679441", "2025-03-20", "Netherlands", "VFH", "1/4", 6.8),
        ("4498079", "4679442", "2025-03-23", "Netherlands", "VFR", "1/4", 8.0),
        ("4498088", "4679452", "2025-06-05", "France", "HF", "1/2", 9.5),
        ("4498090", "4679454", "2025-06-08", "Portugal", "FF", "final", 7.3),
    ]
    tm = {"profile": {"id": "937958", "name": "Lamine Yamal", "position": "RW"}, "competitions": []}
    for identity, name, records in [
        ("UNLA", "UEFA Nations League A", games[:3]),
        ("UNFI", "UEFA Nations League Finals", games[3:]),
    ]:
        tm["competitions"].append({
            "id": identity, "name": name, "category": "national_regular", "competition_type_id": 11,
            "team": "Spain", "games": len(records), "stage_code": "4" if identity == "UNLA" else "FF",
            "stage_history": ["4"] if identity == "UNLA" else ["VFH", "VFR", "HF", "FF"],
            "matches": [{"id": tm_id, "date": day, "team": "Spain", "opponent": opponent, "stage_code": code}
                        for tm_id, _, day, opponent, code, _, _ in records],
        })
    tm["competitions"][1].update(place="2", final_outcome="runner_up")
    fm = {"status": "matched", "competitions": [{
        "id": "9806", "name": "UEFA Nations League A", "category": "national_regular", "team": "Spain",
        "games": 7, "rated_games": 7, "rating": math.fsum(game[6] for game in games) / 7,
        "matches": [{"id": fm_id, "date": day, "team": "Spain", "opponent": opponent,
                     "competition_id": "9806", "competition": "UEFA Nations League A", "category": "national_regular",
                     "is_national": True, "stage": stage, "rating": rating}
                    for _, fm_id, day, opponent, _, stage, rating in games],
    }]}
    return tm, fm


@pytest.mark.parametrize("legacy", [False, True])
def test_real_nations_league_a_feed_splits_group_and_finals_without_extra_major_weights(legacy):
    tm, fm = spain_sources()
    if legacy:
        for match in fm["competitions"][0]["matches"]:
            match.pop("category")
            match.pop("is_national")
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == snapshot["unmatched_fotmob_competitions"] == []
    rows = {row["id"]: row for row in snapshot["competitions"]}
    assert rows["UNLA"]["rated_games"] == 3
    assert rows["UNFI"]["rated_games"] == 4
    assert rows["UNLA"]["rating"] == pytest.approx((7.9 + 8.0 + 7.9) / 3)
    assert rows["UNFI"]["rating"] == pytest.approx((6.8 + 8.0 + 9.5 + 7.3) / 4)
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == weighted["history_games"] == 7
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == 10
    assert weighted["raw_rating"] == pytest.approx(7.95)
    assert weighted["rating"] == pytest.approx(7.95 / 1.0096)
    matches = {match["id"]: match for match in weighted["matches"]}
    assert set(matches) == {match["id"] for match in fm["competitions"][0]["matches"]}
    assert [match["weight"] for match in weighted["matches"]] == [1, 1, 1, 1, 1, 2, 3]
    for match in weighted["matches"]:
        assert match["context"]["category"] == "national_regular"
        assert {rule["key"] for rule in match["weight_rules"]}.issubset({"base", "semifinal", "final"})
    assert [matches[identity]["context"]["stage"] for identity in ["4422026", "4422028", "4422030"]] == ["group"] * 3


@pytest.mark.parametrize("problem", ["truncated", "ambiguous", "unmatched"])
def test_nations_league_phase_partition_cannot_hide_incomplete_or_ambiguous_ratings(problem):
    tm, fm = spain_sources()
    if problem == "truncated":
        fm["competitions"][0]["matches"].pop()
    elif problem == "ambiguous":
        tm["competitions"][0]["matches"].append(deepcopy(tm["competitions"][1]["matches"][-1]))
    else:
        fm["competitions"][0]["matches"][-1]["date"] = "2025-06-09"
    original = deepcopy((tm, fm))

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert (tm, fm) == original
    assert weighted["status"] == "missing_history"
    assert weighted["rating"] is weighted["raw_rating"] is None
    assert weighted["missing_source_samples"]
    assert weighted["duplicate_games"] == 0


@pytest.mark.parametrize("tm_id,fm_id,name,group", [
    ("UNLA", "9806", "UEFA Nations League A", "4"),
    ("UNLB", "9807", "UEFA Nations League B", "3"),
    ("UNLC", "9808", "UEFA Nations League C", "1"),
    ("UNLD", "9809", "UEFA Nations League D", "2"),
])
def test_single_league_phase_does_not_require_a_finals_row(tm_id, fm_id, name, group):
    # These are format-contract fixtures; FotMob IDs A/B/C/D are verified.
    tm = {"profile": {"id": "test-player", "position": "CF"}, "competitions": [{
        "id": tm_id, "name": name, "category": "national_regular", "competition_type_id": 11,
        "team": "Nation", "games": 1,
        "matches": [{"id": "tm-group", "date": "2024-09-08", "team": "Nation", "opponent": "Opponent", "stage_code": group}],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": fm_id, "name": name, "category": "national_regular", "team": "Nation", "games": 1,
        "rated_games": 1, "rating": 8,
        "matches": [{"id": "fm-group", "date": "2024-09-08", "team": "Nation", "opponent": "Opponent",
                     "competition_id": fm_id, "competition": name, "category": "national_regular", "stage": None, "rating": 8}],
    }]}

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert snapshot["warnings"] == []
    assert snapshot["competitions"][0]["rated_games"] == 1
    assert weighted["status"] == "complete"
    assert weighted["rating"] == 8
    assert weighted["total_weight"] == 1
    assert weighted["matches"][0]["context"]["stage"] == "group"


def test_different_national_team_cannot_be_used_to_partition_the_combined_a_feed():
    tm, fm = spain_sources()
    fm["competitions"][0]["team"] = "Spain U21"

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "missing_history"
    assert weighted["rating"] is None
    assert weighted["missing_source_samples"]


def test_team_final_history_does_not_supply_an_unknown_individual_quarterfinal_stage():
    tm, fm = spain_sources()
    tm["competitions"][1]["matches"][0].pop("stage_code")
    fm["competitions"][0]["matches"][3]["stage"] = None

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is None
    assert weighted["unresolved_match_ids"] == ["4679441"]


def test_saved_nations_league_sources_rebuild_both_phases_without_import_or_euro_bonus(tmp_path):
    tm, fm = spain_sources()
    stale = combine(tm, {"status": "matched", "competitions": []})
    stale["warnings"] = ["Old merged Nations League sample."]
    original = deepcopy((tm, fm))
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2024))
    identity = store.save_player("2024-25", "937958", stale, tm, fm)

    first = store.player(identity)
    repeated = store.player(identity)
    reopened = Store(store.path).player(identity)

    assert first["warnings"] == []
    assert first["weighted_rating"]["status"] == "complete"
    assert first["weighted_rating"]["rated_games"] == 7
    assert repeated["weighted_rating"] == reopened["weighted_rating"] == first["weighted_rating"]
    assert repeated["updated_at"] == reopened["updated_at"] == first["updated_at"]
    points = first["season_points"]
    assert points["status"] == "complete"
    assert {bonus["kind"]: bonus["points"] for bonus in points["bonuses"]} == {"no_champions_league": -25}
    assert points["total"] == pytest.approx(7.95 / 1.0096 * 100 - 25)
    assert store.raw(identity) == original
