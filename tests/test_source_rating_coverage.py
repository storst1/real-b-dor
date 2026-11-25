from copy import deepcopy

import pytest

from season_ledger.domain import combine
from season_ledger.weighting import calculate_weighted_rating


def source_pair():
    tm = {"profile": {"id": "1", "name": "Player", "position": "CF"}, "competitions": [{
        "id": "L1", "name": "Bundesliga", "category": "domestic_league", "competition_type_id": 1,
        "team": "Bayern Munich", "games": 1,
        "matches": [{"id": "tm-1", "date": "2024-01-01", "team": "Bayern Munich", "opponent": "Neutral Club"}],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": "54", "name": "Bundesliga", "category": "domestic_league", "team": "Bayern Munich",
        "games": 1, "rated_games": 1, "rating": 7,
        "matches": [{"id": "1", "date": "2024-01-01", "competition_id": "54", "competition": "Bundesliga",
                     "team": "Bayern Munich", "opponent": "Neutral Club", "rating": 7, "stage": None}],
    }]}
    return tm, fm


@pytest.mark.parametrize("summary", [8, None])
def test_unmatched_rated_source_blocks_final_mean_even_without_a_summary(summary):
    tm, fm = source_pair()
    fm["competitions"].append({
        "id": "999999", "name": "Unmapped Cup", "category": "continental", "team": "Bayern Munich",
        "games": 1, "rated_games": 1, "rating": summary,
        "matches": [{"id": "2", "date": "2024-02-01", "competition_id": "999999", "competition": "Unmapped Cup",
                     "opponent": "Other Club", "rating": 8, "stage": "final"}],
    })
    original_tm, original_fm = deepcopy(tm), deepcopy(fm)

    snapshot = combine(tm, fm)
    result = calculate_weighted_rating(snapshot)

    assert (tm, fm) == (original_tm, original_fm)
    assert result["status"] == "missing_history"
    assert result["rating"] is result["raw_rating"] is None
    assert result["partial_rating"] == 7
    assert result["included_games"] == 1
    assert result["duplicate_games"] == 0
    assert len(result["missing_source_samples"]) == 1
    assert result["missing_source_samples"][0]["competition_id"] == "999999"
    assert calculate_weighted_rating(combine(tm, fm)) == result


def test_unmatched_rated_sample_count_alone_still_blocks_a_complete_season():
    tm, fm = source_pair()
    fm["competitions"].append({
        "id": "999999", "name": "Legacy Cup", "category": "continental",
        "team": "Bayern Munich", "rated_games": 3, "rating": None,
    })

    result = calculate_weighted_rating(combine(tm, fm))

    assert result["status"] == "missing_history"
    assert result["rating"] is None
    assert result["missing_source_samples"][0]["expected_rated_games"] == 3


def test_unmatched_explicitly_unrated_source_does_not_block_the_known_rated_sample():
    tm, fm = source_pair()
    fm["competitions"].append({
        "id": "999999", "name": "Unrated Cup", "category": "continental", "team": "Bayern Munich",
        "games": 1, "rated_games": 0, "rating": None,
        "matches": [{"id": "2", "date": "2024-02-01", "rating": None}],
    })

    result = calculate_weighted_rating(combine(tm, fm))

    assert result["status"] == "complete"
    assert result["rating"] == 7


def mls_pair():
    tm = {"profile": {"id": "1", "name": "Player", "position": "CF"}, "competitions": [
        {"id": "MLS1", "name": "MLS", "category": "domestic_league", "team": "Inter Miami CF",
         "competition_type_id": 1, "games": 1,
         "matches": [{"id": "tm-regular", "date": "2024-04-01", "team": "Inter Miami CF", "opponent": "Neutral Club"}]},
        {"id": "POUS", "name": "MLS Cup Playoffs", "category": "other", "team": "Inter Miami CF",
         "competition_type_id": 12, "games": 1,
         "matches": [{"id": "tm-playoff", "date": "2024-11-01", "team": "Inter Miami CF", "opponent": "Other Club", "stage_code": "AF"}]},
    ]}
    fm = {"status": "matched", "competitions": [{
        "id": "130", "name": "MLS", "category": "domestic_league", "team": "Inter Miami CF",
        "games": 2, "rated_games": 2, "rating": 8,
        "matches": [
            {"id": "1", "date": "2024-04-01", "competition_id": "130", "competition": "MLS",
             "team": "Inter Miami CF", "opponent": "Neutral Club", "rating": 7, "stage": None},
            {"id": "2", "date": "2024-11-01", "competition_id": "130", "competition": "MLS",
             "team": "Inter Miami CF", "opponent": "Other Club", "rating": 9, "stage": "1/8"},
        ],
    }]}
    return tm, fm


def test_mls_partition_keeps_every_source_match_once_without_mutating_inputs():
    tm, fm = mls_pair()
    original_tm, original_fm = deepcopy(tm), deepcopy(fm)

    snapshot = combine(tm, fm)
    result = calculate_weighted_rating(snapshot)

    assert (tm, fm) == (original_tm, original_fm)
    assert snapshot["unmatched_fotmob_competitions"] == []
    assert {row["id"]: [match["id"] for match in row["fotmob_matches"]] for row in snapshot["competitions"]} == {
        "MLS1": ["1"], "POUS": ["2"],
    }
    assert result["status"] == "complete"
    assert result["rating"] == 8
    assert result["rated_games"] == result["included_games"] == 2
    assert result["duplicate_games"] == 0
    assert calculate_weighted_rating(combine(tm, fm)) == result


@pytest.mark.parametrize("stage,expected_weight", [("HF", 2), ("FF", 3), (None, None)])
def test_mls_playoff_without_fotmob_round_uses_tm_instead_of_assuming_regular_season(stage, expected_weight):
    tm, fm = mls_pair()
    tm["competitions"][1]["matches"][0]["stage_code"] = stage
    fm["competitions"][0]["matches"][1]["stage"] = None

    result = calculate_weighted_rating(combine(tm, fm))
    playoff = next(match for match in result["matches"] if match["id"] == "2")

    assert playoff["weight"] == expected_weight
    if expected_weight is None:
        assert result["status"] == "incomplete_context"
        assert result["rating"] is None
    else:
        assert result["status"] == "complete"
        assert result["total_weight"] == 1 + expected_weight
        assert result["rating"] == pytest.approx((7 + 9 * expected_weight) / (1 + expected_weight))


@pytest.mark.parametrize("problem", ["missing_match", "ambiguous_day", "unmatched_day"])
def test_incomplete_or_ambiguous_mls_partition_cannot_publish_a_final_rating(problem):
    tm, fm = mls_pair()
    if problem == "missing_match":
        fm["competitions"][0]["matches"].pop()
    elif problem == "ambiguous_day":
        tm["competitions"][0]["matches"].append(deepcopy(tm["competitions"][1]["matches"][0]))
    else:
        fm["competitions"][0]["matches"][1]["date"] = "2024-11-02"
    original_tm, original_fm = deepcopy(tm), deepcopy(fm)

    snapshot = combine(tm, fm)
    result = calculate_weighted_rating(snapshot)

    assert (tm, fm) == (original_tm, original_fm)
    assert snapshot["unmatched_fotmob_competitions"]
    assert result["status"] == "missing_history"
    assert result["rating"] is result["raw_rating"] is None
    assert result["included_games"] == 1
    assert result["partial_rating"] == 7
    assert result["duplicate_games"] == 0
