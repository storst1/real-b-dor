from copy import deepcopy
import math

import pytest

from season_ledger.domain import Season, combine
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def bailey_sources():
    games = [
        ("4169264", "4258229", "2023-09-09", "Honduras", "B", None, 6.6),
        ("4169269", "4258228", "2023-09-13", "Haiti", "B", None, 7.4),
        ("4169276", "4258223", "2023-10-12", "Grenada", "B", None, 6.0),
        ("4169277", "4258222", "2023-10-16", "Haiti", "B", None, 8.3),
        ("4227795", "4349588", "2023-11-18", "Canada", "VFH", "1/4", 6.9),
        ("4227799", "4349589", "2023-11-22", "Canada", "VFR", "1/4", 7.1),
    ]
    tm = {"profile": {"id": "387626", "name": "Leon Bailey", "position": "RW"}, "competitions": []}
    for identity, name, records in [
        ("CNLA", "CONCACAF Nations League A", games[:4]),
        ("CNNF", "CONCACAF Nations League Finals", games[4:]),
    ]:
        tm["competitions"].append({
            "id": identity, "name": name, "category": "national_regular", "competition_type_id": 11,
            "team": "Jamaica", "games": len(records),
            "matches": [{"id": tm_id, "date": day, "team": "Jamaica", "opponent": opponent, "stage_code": code}
                        for tm_id, _, day, opponent, code, _, _ in records],
        })
    # These are the team's later rounds; Bailey's actual appearances stop at
    # the two Canada quarterfinal legs above.
    tm["competitions"][1].update(stage_code="PD", stage_history=["VFH", "VFR", "HF", "PD"])
    fm = {"status": "matched", "competitions": [{
        "id": "9821", "name": "Concacaf Nations League", "category": "national_regular", "team": "Jamaica",
        "games": 6, "rated_games": 6, "rating": math.fsum(record[6] for record in games) / 6,
        "matches": [{"id": fm_id, "date": day, "team": "Jamaica", "opponent": opponent,
                     "competition_id": "9821", "competition": "Concacaf Nations League", "category": "national_regular",
                     "is_national": True, "stage": stage, "rating": rating}
                    for _, fm_id, day, opponent, _, stage, rating in games],
    }]}
    return tm, fm


@pytest.mark.parametrize("legacy", [False, True])
def test_bailey_combined_nations_league_keeps_all_six_real_matches_and_quarterfinal_weights(legacy):
    tm, fm = bailey_sources()
    if legacy:
        for match in fm["competitions"][0]["matches"]:
            match.pop("category")
            match.pop("is_national")
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert snapshot["unmatched_fotmob_competitions"] == []
    rows = {row["id"]: row for row in snapshot["competitions"]}
    assert rows["CNLA"]["rated_games"] == 4
    assert rows["CNNF"]["rated_games"] == 2
    assert rows["CNLA"]["rating"] == pytest.approx((6.6 + 7.4 + 6.0 + 8.3) / 4)
    assert rows["CNNF"]["rating"] == 7
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == weighted["history_games"] == 6
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == 6
    assert weighted["raw_rating"] == pytest.approx(7.05)
    assert weighted["rating"] == pytest.approx(7.05 / 1.0096)
    assert {match["id"] for match in weighted["matches"]} == {
        "4258229", "4258228", "4258223", "4258222", "4349588", "4349589",
    }
    assert [match["context"]["stage"] for match in weighted["matches"]] == [
        "group", "group", "group", "group", "quarterfinal", "quarterfinal",
    ]
    for match in weighted["matches"]:
        assert match["context"]["category"] == "national_regular"
        assert match["weight"] == 1
        assert [rule["key"] for rule in match["weight_rules"]] == ["base"]


@pytest.mark.parametrize("problem", ["truncated_history", "ambiguous_day", "unmatched_day"])
def test_bailey_incomplete_or_ambiguous_source_partition_cannot_publish_a_final_mean(problem):
    tm, fm = bailey_sources()
    if problem == "truncated_history":
        fm["competitions"][0]["matches"].pop()
    elif problem == "ambiguous_day":
        tm["competitions"][0]["matches"].append(deepcopy(tm["competitions"][1]["matches"][1]))
    else:
        fm["competitions"][0]["matches"][-1]["date"] = "2023-11-23"
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["unmatched_fotmob_competitions"]
    assert weighted["status"] == "missing_history"
    assert weighted["rating"] is weighted["raw_rating"] is None
    assert weighted["missing_source_samples"]
    assert weighted["duplicate_games"] == 0


def test_bailey_source_team_must_match_before_the_feed_can_be_partitioned():
    tm, fm = bailey_sources()
    fm["competitions"][0]["team"] = "Jamaica U23"

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "missing_history"
    assert weighted["rating"] is None
    assert weighted["missing_source_samples"]


def test_bailey_team_semifinal_and_third_place_history_cannot_fill_an_unknown_match_round():
    tm, fm = bailey_sources()
    tm["competitions"][1]["matches"][1].pop("stage_code")
    fm["competitions"][0]["matches"][-1]["stage"] = None

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is None
    assert weighted["unresolved_match_ids"] == ["4349589"]
    unknown = next(match for match in weighted["matches"] if match["id"] == "4349589")
    assert unknown["context"]["stage"] is None
    assert unknown["weight"] is None


def test_saved_bailey_restores_the_combined_feed_without_import_or_nations_league_bonus(tmp_path):
    tm, fm = bailey_sources()
    tm["competitions"].extend([
        {"id": "UCOL", "name": "UEFA Conference League", "category": "continental", "team": "Aston Villa",
         "games": 11, "stage_code": "HFR"},
        {"id": "GB1", "name": "Premier League", "category": "domestic_league", "team": "Aston Villa",
         "games": 35, "place": "4", "competition_complete": True},
    ])
    saved = combine(tm, {"status": "matched", "competitions": []})
    saved["warnings"] = ["Concacaf Nations League was not matched."]
    original = deepcopy((tm, fm))
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "387626", saved, tm, fm)

    first = store.player(identity)
    repeated = store.player(identity)
    reopened = Store(store.path).player(identity)

    assert first["warnings"] == []
    assert first["weighted_rating"]["status"] == "complete"
    assert first["weighted_rating"]["rated_games"] == first["weighted_rating"]["history_games"] == 6
    assert first["weighted_rating"]["rating"] == pytest.approx(7.05 / 1.0096)
    assert repeated["weighted_rating"] == reopened["weighted_rating"] == first["weighted_rating"]
    assert repeated["updated_at"] == reopened["updated_at"] == first["updated_at"]
    assert first["season_points"]["status"] == "complete"
    assert first["season_points"]["bonus_points"] == -25
    assert {bonus["kind"]: bonus["points"] for bonus in first["season_points"]["bonuses"]} == {"no_champions_league": -25}
    assert first["season_points"]["total"] == pytest.approx(7.05 / 1.0096 * 100 - 25)
    assert store.raw(identity) == original
