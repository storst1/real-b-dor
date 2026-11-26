from copy import deepcopy

import pytest

from season_ledger.match_context import canonical_stage, resolve_match_context
from season_ledger.weighting import calculate_weighted_rating


@pytest.mark.parametrize("raw,expected", [
    ("GP", "league"), (" gp ", "league"), ("League phase", "league"),
    ("ZR", "playoff"), ("ZRH", "playoff"), ("ZRR", "playoff"), ("1/16", "playoff"),
    ("G", "group"), ("Group Stage", "group"), ("AFH", "round_of_16"),
    ("VFR", "quarterfinal"), ("HFH", "semifinal"), ("FF", "final"),
])
def test_new_uefa_phase_codes_preserve_old_group_and_knockout_contracts(raw, expected):
    assert canonical_stage(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", 8, "1", "8", "19", "Round 8", "Matchday 8", "League phase 8",
    "GPH", "GPR", "GPX", "ZRX", "ZRHH", "ZRRR", "Qualifying", "Unknown phase",
])
def test_new_phase_codes_do_not_turn_matchdays_or_unknown_suffixes_into_playoffs(raw):
    assert canonical_stage(raw) is None


def source_snapshot(tm_id, fm_id, day, team, tm_opponent, fm_opponent, rating, code="GP", fm_stage=None, identity="CL"):
    row = {
        "id": identity, "name": "UEFA Champions League" if identity == "CL" else "UEFA Champions League Qualifying",
        "category": "continental", "competition_type_id": 10, "team": team,
        "rating": rating, "rated_games": 1,
        "matches": [{"id": tm_id, "date": day, "team": team, "opponent": tm_opponent, "stage_code": code}],
        "fotmob_matches": [{"id": fm_id, "date": day, "team": team, "opponent": fm_opponent,
                            "competition_id": "42", "competition": "Champions League", "stage": fm_stage, "rating": rating}],
    }
    return {"position": "CF", "competitions": [row]}


@pytest.mark.parametrize("tm_id,fm_id,day,team,tm_opponent,fm_opponent,rating,expected_weight", [
    ("4445037", "4621548", "2024-09-17", "Real Madrid", "VfB Stuttgart", "VfB Stuttgart", 8.2, 1.5),
    ("4445032", "4621529", "2024-10-22", "Real Madrid", "Borussia Dortmund", "Borussia Dortmund", 7.7, 1.5),
    ("4445033", "4621544", "2024-11-05", "Real Madrid", "AC Milan", "Milan", 7.7, 2),
    ("4445047", "4621543", "2024-11-27", "Real Madrid", "Liverpool FC", "Liverpool", 5.8, 2),
    ("4445034", "4621546", "2025-01-22", "Real Madrid", "Red Bull Salzburg", "Salzburg", 7.9, 1.5),
    ("4445091", "4621606", "2024-12-11", "Arsenal", "AS Monaco", "Monaco", 9.5, 1.5),
    ("4445064", "4621509", "2024-10-23", "Barcelona", "FC Bayern Munich", "Bayern München", 7.9, 2),
    ("4445085", "4621537", "2025-01-21", "Barcelona", "SL Benfica", "Benfica", 8.2, 1.5),
])
def test_real_2024_25_league_phase_matches_use_tm_gp_without_a_knockout_addition(tm_id, fm_id, day, team, tm_opponent, fm_opponent, rating, expected_weight):
    snapshot = source_snapshot(tm_id, fm_id, day, team, tm_opponent, fm_opponent, rating)
    original = deepcopy(snapshot)

    weighted = calculate_weighted_rating(snapshot)

    assert snapshot == original
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 1
    assert weighted["total_weight"] == expected_weight
    assert weighted["rating"] == pytest.approx(rating)
    match = weighted["matches"][0]
    assert match["id"] == fm_id
    assert match["context"]["competition_id"] == "CL"
    assert match["context"]["stage_code"] == "GP"
    assert match["context"]["stage"] == "league"
    assert match["context"]["is_knockout"] is False
    rules = {rule["key"] for rule in match["weight_rules"]}
    assert "champions_league" in rules
    assert rules.isdisjoint({"champions_league_knockout", "semifinal", "final"})


@pytest.mark.parametrize("tm_id,fm_id,day,team,tm_opponent,fm_opponent,rating,code,expected_weight", [
    ("4547617", "4723900", "2025-02-11", "Real Madrid", "Manchester City", "Manchester City", 8.1, "ZRH", 4.5),
    ("4547623", "4723901", "2025-02-19", "Real Madrid", "Manchester City", "Manchester City", 9.7, "ZRR", 4.5),
    ("4547621", "4723902", "2025-02-12", "Bayern München", "Celtic FC", "Celtic", 6.6, "ZRH", 4),
    ("4547627", "4723903", "2025-02-18", "Bayern München", "Celtic FC", "Celtic", 6.7, "ZRR", 4),
    ("4547599", "4723908", "2025-02-11", "Paris Saint-Germain", "Stade Brestois 29", "Brest", 8.6, "ZRH", 4),
    ("4547601", "4723909", "2025-02-19", "Paris Saint-Germain", "Stade Brestois 29", "Brest", 8.4, "ZRR", 4),
])
def test_real_main_cl_knockout_playoff_legs_use_dated_tm_zr_codes(tm_id, fm_id, day, team, tm_opponent, fm_opponent, rating, code, expected_weight):
    snapshot = source_snapshot(tm_id, fm_id, day, team, tm_opponent, fm_opponent, rating, code=code, fm_stage="1/16")

    weighted = calculate_weighted_rating(snapshot)

    assert weighted["status"] == "complete"
    assert weighted["total_weight"] == expected_weight
    match = weighted["matches"][0]
    assert match["id"] == fm_id
    assert match["context"]["competition_id"] == "CL"
    assert match["context"]["stage_code"] == code
    assert match["context"]["stage"] == "playoff"
    assert match["context"]["is_knockout"] is True
    rules = {rule["key"] for rule in match["weight_rules"]}
    assert {"champions_league", "champions_league_knockout"} <= rules
    assert rules.isdisjoint({"semifinal", "final"})


@pytest.mark.parametrize("stage", ["1/16", "playoff", "knockout round play-offs"])
def test_generic_fotmob_playoff_cannot_identify_main_cl_without_transfermarkt(stage):
    match = {"id": "4723900", "date": "2025-02-11", "team": "Real Madrid", "opponent": "Manchester City",
             "competition_id": "42", "competition": "Champions League", "stage": stage, "rating": 8.1}

    context = resolve_match_context(match, [])

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


def test_same_fotmob_playoff_label_on_a_qualifier_does_not_receive_main_cl_additions():
    snapshot = source_snapshot("qualifier-tm", "qualifier-fm", "2024-08-20", "Player Club", "Neutral Club", "Neutral Club",
                               8, code="POH", fm_stage="1/16", identity="CLQ")

    weighted = calculate_weighted_rating(snapshot)

    assert weighted["status"] == "complete"
    assert weighted["total_weight"] == 1
    match = weighted["matches"][0]
    assert match["context"]["competition_id"] == "CLQ"
    assert [rule["key"] for rule in match["weight_rules"]] == ["base"]


@pytest.mark.parametrize("code", [None, "1", "8", "Round 8", "UNKNOWN"])
def test_missing_cl_phase_is_not_inferred_from_a_generic_matchday(code):
    snapshot = source_snapshot("4445037", "4621548", "2024-09-17", "Real Madrid", "VfB Stuttgart", "VfB Stuttgart", 8.2, code=code)

    weighted = calculate_weighted_rating(snapshot)

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is weighted["raw_rating"] is None
    assert weighted["unresolved_match_ids"] == ["4621548"]


def test_old_group_code_stays_non_knockout_while_old_knockout_legs_keep_their_bonus():
    group = source_snapshot("old-group-tm", "old-group-fm", "2023-09-19", "Player Club", "Neutral Club", "Neutral Club", 8, code="G")
    knockout = source_snapshot("old-knockout-tm", "old-knockout-fm", "2024-02-13", "Player Club", "Neutral Club", "Neutral Club", 8, code="AFH")

    old_group = calculate_weighted_rating(group)
    old_knockout = calculate_weighted_rating(knockout)

    assert old_group["status"] == old_knockout["status"] == "complete"
    assert old_group["total_weight"] == 1.5
    assert old_group["matches"][0]["context"]["stage"] == "group"
    assert old_knockout["total_weight"] == 4
    assert old_knockout["matches"][0]["context"]["stage"] == "round_of_16"


@pytest.mark.parametrize("phase,expected", [("League phase", "league"), ("League stage", "league"), ("Group Stage", "group")])
def test_explicit_fotmob_non_knockout_phase_remains_sufficient_without_tm(phase, expected):
    match = {"id": "source-match", "competition_id": "42", "competition": "Champions League",
             "opponent": "Neutral Club", "stage": phase}

    context = resolve_match_context(match, [])

    assert context["competition_provider"] == "fotmob"
    assert context["competition_id"] == "42"
    assert context["stage"] == expected
    assert context["is_knockout"] is False
