from copy import deepcopy
import math

import pytest

from season_ledger.domain import Season, combine
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def qualification_sources():
    # One FotMob campaign spans Transfermarkt's group and playoff tournaments.
    games = [
        ("3941481", "4044726", "2023-09-07", "Faroe Islands", "E", None, 9.3),
        ("3941484", "4044729", "2023-09-10", "Albania", "E", None, 5.8),
        ("3941479", "4044734", "2023-11-17", "Czechia", "E", None, 6.7),
        ("4231217", "4369679", "2024-03-21", "Estonia", "HF", "1/2", 8.3),
        ("4317095", "4369680", "2024-03-26", "Wales", "FF", "final", 6.6),
    ]
    tm = {"profile": {"id": "38253", "name": "Robert Lewandowski", "position": "CF"}, "competitions": []}
    for identity, name, rows in [
        ("EMQ", "European Qualifiers", games[:3]),
        ("POEM", "European Qualifiers Play-offs", games[3:]),
    ]:
        tm["competitions"].append({
            "id": identity, "name": name, "category": "national_regular", "competition_type_id": 19,
            "team": "Poland", "games": len(rows),
            "matches": [{"id": tm_id, "date": day, "team": "Poland", "opponent": opponent, "stage_code": code}
                        for tm_id, _, day, opponent, code, _, _ in rows],
        })
    tm["competitions"][1].update(place="1", final_outcome="winner")
    fm = {"status": "matched", "competitions": [{
        "id": "10607", "name": "EURO Qualification", "category": "national_regular", "team": "Poland",
        "games": 5, "rated_games": 5, "rating": math.fsum(game[6] for game in games) / 5,
        "matches": [{"id": fm_id, "date": day, "team": "Poland", "opponent": opponent,
                     "competition_id": "10607", "competition": "EURO Qualification", "category": "national_regular",
                     "is_national": True, "stage": stage, "rating": rating}
                    for _, fm_id, day, opponent, _, stage, rating in games],
    }]}
    return tm, fm


@pytest.mark.parametrize("legacy", [False, True])
def test_lewandowski_qualifying_group_and_playoffs_keep_all_five_ratings(legacy):
    tm, fm = qualification_sources()
    if legacy:
        for match in fm["competitions"][0]["matches"]:
            match.pop("category")
            match.pop("is_national")
    original_tm, original_fm = deepcopy(tm), deepcopy(fm)

    snapshot = combine(tm, fm)
    result = calculate_weighted_rating(snapshot)

    assert (tm, fm) == (original_tm, original_fm)
    assert snapshot["warnings"] == []
    assert snapshot["unmatched_fotmob_competitions"] == []
    rows = {row["id"]: row for row in snapshot["competitions"]}
    assert rows["EMQ"]["rated_games"] == 3
    assert rows["POEM"]["rated_games"] == 2
    assert rows["EMQ"]["rating"] == pytest.approx((9.3 + 5.8 + 6.7) / 3)
    assert rows["POEM"]["rating"] == pytest.approx((8.3 + 6.6) / 2)
    assert result["status"] == "complete"
    assert result["rated_games"] == result["included_games"] == result["history_games"] == 5
    assert result["duplicate_games"] == 0
    assert result["total_weight"] == 8
    assert result["rating"] == pytest.approx((9.3 + 5.8 + 6.7 + 8.3 * 2 + 6.6 * 3) / 8)
    matches = {match["id"]: match for match in result["matches"]}
    assert set(matches) == {match["id"] for match in fm["competitions"][0]["matches"]}
    assert matches["4369679"]["weight"] == 2
    assert matches["4369680"]["weight"] == 3
    assert {rule["key"] for rule in matches["4369679"]["weight_rules"]} == {"base", "semifinal"}
    assert {rule["key"] for rule in matches["4369680"]["weight_rules"]} == {"base", "final"}
    assert all(match["context"]["category"] == "national_regular" for match in matches.values())


@pytest.mark.parametrize("problem", ["missing_history", "ambiguous_day", "unmatched_day"])
def test_qualifier_partition_keeps_missing_or_ambiguous_source_ratings_incomplete(problem):
    tm, fm = qualification_sources()
    if problem == "missing_history":
        fm["competitions"][0]["matches"].pop()
    elif problem == "ambiguous_day":
        tm["competitions"][0]["matches"].append(deepcopy(tm["competitions"][1]["matches"][1]))
    else:
        fm["competitions"][0]["matches"][-1]["date"] = "2024-03-27"

    result = calculate_weighted_rating(combine(tm, fm))

    assert result["status"] == "missing_history"
    assert result["rating"] is result["raw_rating"] is None
    assert result["missing_source_samples"]
    assert result["duplicate_games"] == 0


def test_known_qualifier_without_playoffs_keeps_the_existing_three_match_sample():
    tm, fm = qualification_sources()
    tm["competitions"].pop()
    source = fm["competitions"][0]
    source["matches"] = source["matches"][:3]
    source.update(games=3, rated_games=3, rating=(9.3 + 5.8 + 6.7) / 3)

    snapshot = combine(tm, fm)
    result = calculate_weighted_rating(snapshot)

    assert snapshot["warnings"] == []
    assert result["status"] == "complete"
    assert result["rated_games"] == 3
    assert result["total_weight"] == 3
    assert result["rating"] == pytest.approx(source["rating"])


def test_qualifying_final_without_either_source_stage_is_not_assumed_to_be_a_group_game():
    tm, fm = qualification_sources()
    tm["competitions"][1]["matches"][1].pop("stage_code")
    fm["competitions"][0]["matches"][-1]["stage"] = None

    result = calculate_weighted_rating(combine(tm, fm))

    assert result["status"] == "incomplete_context"
    assert result["rating"] is None
    assert result["unresolved_match_ids"] == ["4369680"]


def test_store_recovers_saved_combined_qualifiers_without_import_or_a_euro_title_bonus(tmp_path):
    tm, fm = qualification_sources()
    tm["competitions"].append({
        "id": "CL", "name": "UEFA Champions League", "category": "continental",
        "team": "FC Barcelona", "games": 9, "stage_code": "VFR",
    })
    # Reproduce the old snapshot, which omitted the combined FotMob campaign.
    saved = combine(tm, {"status": "matched", "competitions": []})
    saved["warnings"] = ["EURO Qualification was not matched."]
    original_tm, original_fm = deepcopy(tm), deepcopy(fm)
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "38253", saved, tm, fm)

    first = store.player(identity)
    repeated = store.player(identity)

    assert first["warnings"] == []
    assert first["weighted_rating"]["status"] == "complete"
    assert first["weighted_rating"]["rated_games"] == 5
    assert first["weighted_rating"]["rating"] == pytest.approx(7.275)
    assert first["season_points"]["status"] == "complete"
    assert {row["kind"]: row["points"] for row in first["season_points"]["bonuses"]} == {"champions_league": 12}
    assert first["season_points"]["total"] == pytest.approx(739.5)
    assert repeated["weighted_rating"] == first["weighted_rating"]
    assert repeated["updated_at"] == first["updated_at"]
    assert store.raw(identity) == (original_tm, original_fm)
