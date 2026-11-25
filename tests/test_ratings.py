from copy import deepcopy
import math

import pytest

from season_ledger.ratings import (
    POSITION_COEFFICIENTS, adjusted_rating, apply_position_adjustment, position_group,
)


@pytest.mark.parametrize(("position", "group", "coefficient"), [
    ("GK", "GK", 0.9253), ("CB", "CB", 0.9664),
    ("LB", "LB/RB", 0.9811), ("RB", "LB/RB", 0.9811),
    ("DM", "DM/CM", 1.0021), ("CM", "DM/CM", 1.0021),
    ("AM", "AM", 1.0158), ("LM", "LM/RM/LW/RW", 1.0096),
    ("RM", "LM/RM/LW/RW", 1.0096), ("LW", "LM/RM/LW/RW", 1.0096),
    ("RW", "LM/RM/LW/RW", 1.0096), ("ST", "ST/SS", 1.0),
    ("SS", "ST/SS", 1.0), ("CF", "ST/SS", 1.0),
])
def test_position_mean_is_normalized_to_striker_reference(position, group, coefficient):
    assert position_group(position) == group
    assert POSITION_COEFFICIENTS[group] == coefficient
    raw_mean = 7.078 * coefficient
    result = apply_position_adjustment({"position": position, "total": {"rating": raw_mean}})
    assert result["total"]["adjusted_rating"] == pytest.approx(7.078)
    assert result["total"]["rating"] == raw_mean
    assert result["rating_adjustment"] == {"group": group, "coefficient": coefficient,
                                           "method": "divide", "position": position}


@pytest.mark.parametrize(("position", "group"), [
    ("Goalkeeper", "GK"), ("Centre-Back", "CB"), ("Center Back", "CB"),
    ("Left-Back", "LB/RB"), ("Right-Back", "LB/RB"),
    ("Defensive Midfield", "DM/CM"), ("Central Midfield", "DM/CM"),
    ("Attacking Midfield", "AM"), ("Left Midfield", "LM/RM/LW/RW"),
    ("Right Midfielder", "LM/RM/LW/RW"), ("Left Winger", "LM/RM/LW/RW"),
    ("Right Winger", "LM/RM/LW/RW"), ("Centre-Forward", "ST/SS"),
    ("Second Striker", "ST/SS"), ("  centre–forward  ", "ST/SS"), (" cb ", "CB"),
])
def test_transfermarkt_long_names_and_case_are_supported(position, group):
    assert position_group(position) == group


@pytest.mark.parametrize("position", [None, "", "SW", "Sweeper", "Defender", "Midfielder", "Forward",
                                      "CB/RB", "LB/RB", "ST, SS", "Centre-Forward / Left Winger", {}, True])
def test_unknown_or_ambiguous_position_has_no_adjusted_index(position):
    assert position_group(position) is None
    result = apply_position_adjustment({"position": position, "competitions": [{"rating": 7}],
                                        "groups": {"domestic_league": {"rating": 7}}, "total": {"rating": 7}})
    assert result["rating_adjustment"]["coefficient"] is None
    assert result["competitions"][0]["adjusted_rating"] is None
    assert result["groups"]["domestic_league"]["adjusted_rating"] is None
    assert result["total"]["adjusted_rating"] is None
    assert result["total"]["rating"] == 7


@pytest.mark.parametrize("raw", [None, 0, -1, 10.01, True, False, "7.5", math.nan, math.inf, -math.inf])
def test_invalid_or_unrated_source_is_unknown(raw):
    assert adjusted_rating(raw, 0.9253) is None


@pytest.mark.parametrize("coefficient", [None, 0, -1, True, "0.9253", math.nan, math.inf])
def test_invalid_coefficient_is_unknown(coefficient):
    assert adjusted_rating(7, coefficient) is None


def test_goalkeeper_adjustment_divides_and_does_not_clamp_index_at_ten():
    assert adjusted_rating(7, 0.9253) == pytest.approx(7 / 0.9253)
    assert adjusted_rating(10, 0.9253) > 10


def test_competitions_categories_and_total_use_the_same_main_position():
    snapshot = {"position": "CM", "competitions": [
        {"category": "domestic_league", "rating": 6, "rated_games": 2},
        {"category": "domestic_league", "rating": 8, "rated_games": 6},
        {"id": "CL", "name": "UEFA Champions League", "category": "continental", "rating": 9, "rated_games": 2}],
        "groups": {"domestic_league": {"rating": 7.5}, "continental": {"rating": 9},
                   "national_major": {"rating": None}}, "total": {"rating": 7.8}}
    result = apply_position_adjustment(snapshot)
    assert [row["adjusted_rating"] for row in result["competitions"]] == pytest.approx([6 / 1.0021, 8 / 1.0021, 9 / 1.0021])
    assert result["groups"]["domestic_league"]["adjusted_rating"] == pytest.approx(7.5 / 1.0021)
    assert result["groups"]["continental"]["adjusted_rating"] == pytest.approx(9 / 1.0021)
    assert result["groups"]["national_major"]["adjusted_rating"] is None
    assert result["total"]["adjusted_rating"] == pytest.approx(7.8 / 1.0021)


def test_summary_adjustment_uses_unrounded_competitions_without_double_rounding():
    snapshot = {"position": "RW", "competitions": [
        {"category": "domestic_league", "rating": 7.10496, "rated_games": 1},
        {"category": "domestic_league", "rating": 7.12496, "rated_games": 1}],
        "groups": {"domestic_league": {"rating": 7.11}}, "total": {"rating": 7.11}}
    result = apply_position_adjustment(snapshot)
    assert round(result["total"]["adjusted_rating"], 2) == 7.05
    assert round(7.11 / 1.0096, 2) == 7.04
    assert result["total"]["adjusted_rating"] == pytest.approx(7.11496 / 1.0096)
    assert result["groups"]["domestic_league"]["adjusted_rating"] == pytest.approx(7.11496 / 1.0096)
    assert result["total"]["rating"] == result["groups"]["domestic_league"]["rating"] == 7.11


def test_goalkeeper_precision_boundary_uses_competition_rating():
    result = apply_position_adjustment({"position": "GK", "competitions": [
        {"category": "domestic_league", "rating": 6.5003, "rated_games": 2}],
        "groups": {"domestic_league": {"rating": 6.50}}, "total": {"rating": 6.50}})
    assert round(result["total"]["adjusted_rating"], 2) == 7.03
    assert round(6.50 / 0.9253, 2) == 7.02


def test_absent_competition_data_can_adjust_legacy_summaries():
    result = apply_position_adjustment({"position": "CB", "groups": {"continental": {"rating": 7.3}}, "total": {"rating": 7.3}})
    assert result["groups"]["continental"]["adjusted_rating"] == pytest.approx(7.3 / 0.9664)
    assert result["total"]["adjusted_rating"] == pytest.approx(7.3 / 0.9664)


@pytest.mark.parametrize("competitions", [[], [{"rating": None}], None])
def test_present_but_unrated_competitions_do_not_use_stale_summary_rating(competitions):
    result = apply_position_adjustment({"position": "CB", "competitions": competitions,
        "groups": {"domestic_league": {"rating": 7.3}}, "total": {"rating": 7.3}})
    assert result["groups"]["domestic_league"]["adjusted_rating"] is None
    assert result["total"]["adjusted_rating"] is None


def test_single_unknown_sample_count_is_usable_without_appearance_weighting():
    result = apply_position_adjustment({"position": "CB", "competitions": [
        {"category": "domestic_league", "rating": 7, "rated_games": None, "games": 2},
        {"category": "continental", "rating": None, "rated_games": 0, "games": 1000}],
        "groups": {"domestic_league": {"rating": 7}}, "total": {"rating": 7}})
    assert result["total"]["adjusted_rating"] == pytest.approx(7 / 0.9664)
    assert result["groups"]["domestic_league"]["adjusted_rating"] == pytest.approx(7 / 0.9664)


def test_multiple_ratings_with_unknown_sample_count_cannot_be_appearance_weighted():
    result = apply_position_adjustment({"position": "CB", "competitions": [
        {"category": "domestic_league", "rating": 7, "rated_games": None, "games": 100},
        {"category": "domestic_league", "rating": 8, "rated_games": 2, "games": 2}],
        "groups": {"domestic_league": {"rating": 7.5}}, "total": {"rating": 7.5}})
    assert result["total"]["adjusted_rating"] is None
    assert result["groups"]["domestic_league"]["adjusted_rating"] is None


def test_transformation_preserves_source_and_does_not_mutate_nested_inputs_or_compound():
    snapshot = {"position": "RB", "competitions": [
        {"category": "domestic_league", "rating": 7.38123, "rated_games": 3, "sources": ["FotMob"]}],
        "groups": {"domestic_league": {"rating": 7.38}}, "total": {"rating": 7.38},
        "warnings": ["Existing source warning"]}
    before = deepcopy(snapshot)
    result = apply_position_adjustment(snapshot)
    assert snapshot == before
    assert apply_position_adjustment(result) == result
    assert result["competitions"][0]["rating"] == 7.38123
    assert result["total"]["rating"] == 7.38
    assert result["competitions"] is not snapshot["competitions"]
    assert result["groups"]["domestic_league"] is not snapshot["groups"]["domestic_league"]
    result["competitions"][0]["sources"].append("Changed")
    result["warnings"].append("Changed")
    assert snapshot == before


def test_missing_optional_snapshot_fields_are_preserved():
    result = apply_position_adjustment({"name": "Unknown player"})
    assert result == {"name": "Unknown player", "rating_adjustment": {
        "group": None, "coefficient": None, "method": "divide", "position": None}}
