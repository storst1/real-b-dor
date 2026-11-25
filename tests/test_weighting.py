from copy import deepcopy
import math

import pytest

from season_ledger.weighting import calculate_weighted_rating
import season_ledger.weighting as weighting


def game(identity=1, *, rating=8.0, stage="group", opponent="Neutral Club", day="2024-04-01", **extra):
    return {"id": str(identity), "date": day, "rating": rating, "stage": stage,
            "team": "Player Club", "opponent": opponent, "minutes": 90, "source": "FotMob", **extra}


def competition(identity="CL", *, category="continental", kind=10, match=None, name=None, **extra):
    return {"id": identity, "name": name or {"CL": "UEFA Champions League", "FIWC": "World Cup", "EURO": "European Championship",
                                              "COPA": "Copa America", "CA16": "Copa America Centenario", "FS": "Friendlies"}.get(identity, identity),
            "category": category, "competition_type_id": kind, "team": "Player Club", "matches": [],
            "fotmob_matches": [game() if match is None else match], **extra}


def season(*competitions, coefficient=1.0):
    return {"position": "CF", "rating_adjustment": {"coefficient": coefficient, "method": "divide"},
            "competitions": list(competitions), "total": {"rating": 9.99, "adjusted_rating": 9.99}}


def result_for(entry, **kwargs):
    return calculate_weighted_rating(season(entry), **kwargs)


@pytest.mark.parametrize(("entry", "expected"), [
    (competition(match=game(stage="1/2", opponent="Real Madrid")), 5.5),
    (competition(match=game(stage="final", opponent="Inter")), 6.5),
    (competition("EURO", category="national_major", kind=11, match=game(stage="final", opponent="Spain")), 4.5),
    (competition("FIWC", category="national_major", kind=11, match=game(stage="final", opponent="Argentina")), 5.5),
    (competition("DFB", category="domestic_cup", kind=8, match=game(stage="1/2", opponent="Liverpool")), 1.5),
    (competition("DFB", category="domestic_cup", kind=8, match=game(stage="final", opponent="Liverpool")), 3.5),
    (competition("USC", category="continental", kind=13, match=game(stage="final", opponent="Bayern Munich")), 1.5),
])
def test_requested_examples_add_every_matching_rule(entry, expected):
    result = result_for(entry)
    assert result["status"] == "complete"
    assert result["rating"] == result["raw_rating"] == 8
    assert result["total_weight"] == expected
    match = result["matches"][0]
    assert match["weight"] == expected
    assert math.fsum(rule["addition"] for rule in match["weight_rules"]) == expected


def test_cl_semifinal_rules_are_additions_not_multiplied_bonuses():
    match = result_for(competition(match=game(stage="1/2", opponent="Real Madrid")))["matches"][0]
    assert {rule["key"]: rule["addition"] for rule in match["weight_rules"]} == {
        "base": 1, "champions_league": .5, "champions_league_knockout": 2.5,
        "semifinal": 1, "grand_club_opponent": .5,
    }


@pytest.mark.parametrize(("stage", "expected"), [("group", 1.5), ("league phase", 1.5), ("1/8", 4), ("1/4", 4), ("1/2", 5), ("final", 6)])
def test_main_cl_weights_include_knockout_semifinal_and_final_additions(stage, expected):
    assert result_for(competition(match=game(stage=stage)))["total_weight"] == expected


@pytest.mark.parametrize("identity", ["CLQ", "EL", "ECL", "KLUB", "ACL", "CAFCL"])
def test_other_international_competitions_have_generic_round_weights_but_no_cl_additions(identity):
    result = result_for(competition(identity, match=game(stage="1/2", opponent="Real Madrid")))
    assert result["status"] == "complete"
    assert result["total_weight"] == 2.5
    assert all(not rule["key"].startswith("champions_league") for rule in result["matches"][0]["weight_rules"])


@pytest.mark.parametrize("kind", [8, 14, 15])
def test_ordinary_domestic_cup_semifinals_are_excluded_but_finals_are_not(kind):
    semifinal = competition("CUP", category="domestic_cup", kind=kind, match=game(stage="1/2"))
    final = competition("CUP", category="domestic_cup", kind=kind, match=game(stage="final"))
    assert result_for(semifinal)["total_weight"] == 1
    assert result_for(final)["total_weight"] == 3


@pytest.mark.parametrize("kind", [9, 13, 23])
@pytest.mark.parametrize("stage", ["1/2", "final", None])
def test_all_supercup_types_exclude_semifinal_and_final_additions(kind, stage):
    entry = competition("SC", category="domestic_cup" if kind != 13 else "continental", kind=kind,
                        match=game(stage=stage, opponent="Bayern München"))
    result = result_for(entry)
    assert result["status"] == "complete"
    assert result["total_weight"] == 1.5


@pytest.mark.parametrize("name", ["UEFA Super Cup", "DFL-Supercup", "Community Shield", "Supercoppa Italiana"])
def test_known_supercup_name_also_excludes_round_additions_when_type_is_missing(name):
    entry = competition("SC", name=name, category="domestic_cup", kind=None, match=game(stage="final", opponent="FC Bayern Munich"))
    assert result_for(entry)["total_weight"] == 1.5


@pytest.mark.parametrize("opponent", [
    "Manchester City", "Man City", "Manchester United", "Man United", "Man Utd", "Liverpool FC", "Chelsea", "FC Arsenal",
    "Bayern Munich", "Bayern München", "FC Bayern München", "FC Barcelona", "Real Madrid CF", "Atlético Madrid",
    "Atletico de Madrid", "Club Atletico de Madrid", "Paris Saint-Germain", "PSG", "Inter Milan", "Internazionale",
    "AC Milan", "Milan", "Juventus FC", "MC", "MU",
])
def test_complete_grand_club_list_uses_explicit_canonical_aliases(opponent):
    entry = competition("L1", category="domestic_league", kind=1, match=game(stage=None, opponent=opponent))
    assert result_for(entry)["total_weight"] == 1.5


@pytest.mark.parametrize("opponent", [
    "City", "United", "Madrid", "Leicester City", "Manchester City U21", "Real Madrid Castilla", "Inter Miami",
    "Milan Futuro", "Liverpool Women", "Bayern Munich II", "Atletico San Luis", "Chelsea U19",
])
def test_partial_and_reserve_team_names_do_not_match_grand_clubs(opponent):
    entry = competition("L1", category="domestic_league", kind=1, match=game(stage=None, opponent=opponent))
    assert result_for(entry)["total_weight"] == 1


@pytest.mark.parametrize("opponent", ["Spain", "Argentina", "Brazil", "Portugal", "France", "England", "Italy", "Netherlands"])
@pytest.mark.parametrize("identity", ["FIWC", "EURO", "COPA", "CA16"])
def test_grand_national_opponents_apply_only_to_main_world_cup_euro_or_copa(identity, opponent):
    entry = competition(identity, category="national_major", kind=11, match=game(stage="group", opponent=opponent))
    expected = 3 if identity == "FIWC" else 2
    assert result_for(entry)["total_weight"] == expected


@pytest.mark.parametrize(("identity", "category", "kind", "stage"), [
    ("WMQ", "national_regular", 19, "group"), ("EMQ", "national_regular", 19, "group"),
    ("FS", "national_regular", 17, None), ("AFCON", "national_major", 11, "group"),
])
def test_france_in_qualifiers_friendlies_and_other_major_tournaments_is_not_a_grand_national_addition(identity, category, kind, stage):
    entry = competition(identity, category=category, kind=kind, match=game(stage=stage, opponent="France"))
    assert result_for(entry)["total_weight"] == 1


def test_grand_club_opponent_still_applies_in_a_cup_qualifier_or_friendly():
    entry = competition("FS", category="national_regular", kind=17, match=game(stage=None, opponent="Liverpool"))
    assert result_for(entry)["total_weight"] == 1.5


def test_verified_source_ids_can_identify_real_madrid_or_spain_when_name_is_missing():
    club = competition("L1", category="domestic_league", kind=1, match=game(stage=None, opponent=None, opponent_id="8633"))
    country = competition("EURO", category="national_major", kind=11, match=game(stage="group", opponent=None, opponent_id="6720"))
    assert result_for(club)["total_weight"] == 1.5
    assert result_for(country)["total_weight"] == 2


def test_weight_three_is_three_copies_of_a_rating_and_minutes_do_not_change_weights():
    normal = competition("L1", category="domestic_league", kind=1, match=game(1, rating=6, stage=None, minutes=90))
    final = competition("DFB", category="domestic_cup", kind=8, match=game(2, rating=8, stage="final", minutes=1))
    result = calculate_weighted_rating(season(normal, final))
    assert result["status"] == "complete"
    assert result["total_weight"] == 4
    assert result["rated_games"] == result["included_games"] == 2
    assert result["rating"] == result["raw_rating"] == (6 + 8 + 8 + 8) / 4


def test_fractional_weights_use_their_sum_as_denominator_without_rounding():
    first = competition("L1", category="domestic_league", kind=1, match=game(1, rating=6.123456, stage=None))
    second = competition("DFB", category="domestic_cup", kind=8, match=game(2, rating=8.654321, stage="final", opponent="Liverpool"))
    result = calculate_weighted_rating(season(first, second, coefficient=.9664))
    raw = (6.123456 + 8.654321 * 3.5) / 4.5
    assert result["raw_rating"] == pytest.approx(raw)
    assert result["rating"] == pytest.approx(raw / .9664)
    assert result["total_weight"] == 4.5


def test_position_cap_is_applied_to_each_match_before_averaging():
    first = competition("L1", category="domestic_league", kind=1, match=game(1, rating=10, stage=None))
    second = competition("L1", category="domestic_league", kind=1, match=game(2, rating=6, stage=None))
    result = calculate_weighted_rating(season(first, second, coefficient=.9253))
    assert result["raw_rating"] == 8
    assert result["rating"] == pytest.approx((10 + 6 / .9253) / 2)
    assert result["uncapped_rating"] == pytest.approx(8 / .9253)
    assert result["rating"] != pytest.approx(min(10, 8 / .9253))
    assert [match["adjusted_rating"] for match in result["matches"]] == pytest.approx([10, 6 / .9253])
    assert result["cap_applied"] is True
    assert result["matches"][0]["cap_applied"] is True


def test_uncapped_mode_is_explicit_diagnostic_and_default_mode_caps_at_ten():
    saved = season(competition(match=game(rating=10)), coefficient=.9253)
    capped = calculate_weighted_rating(saved)
    uncapped = calculate_weighted_rating(saved, cap_at_ten=False)
    assert capped["rating"] == 10
    assert capped["cap_applied"] is True
    assert uncapped["rating"] == pytest.approx(10 / .9253)
    assert uncapped["cap_applied"] is False


@pytest.mark.parametrize("raw", [None, 0, -1, True, "8.0", math.nan, math.inf, 10.1])
def test_unrated_or_invalid_source_match_is_excluded_from_both_numerator_and_denominator(raw):
    valid = competition("L1", category="domestic_league", kind=1, match=game(1, rating=7, stage=None))
    unrated = competition(match=game(2, rating=raw, stage="final", opponent="Real Madrid"))
    result = calculate_weighted_rating(season(valid, unrated))
    assert result["status"] == "complete"
    assert result["rating"] == 7
    assert result["rated_games"] == 1
    assert result["unrated_games"] == 1
    assert result["total_weight"] == 1
    assert result["matches"][1]["included"] is False


def test_duplicate_fotmob_match_id_is_counted_once_across_parent_competitions():
    first = competition(match=game(123, stage="1/2"))
    duplicate = deepcopy(first)
    duplicate["fotmob_matches"][0]["id"] = 123
    result = calculate_weighted_rating(season(first, duplicate))
    assert result["rated_games"] == result["included_games"] == result["history_games"] == 1
    assert result["duplicate_games"] == 1
    assert result["total_weight"] == 5


def test_aggregate_rating_is_never_used_as_a_substitute_for_missing_match_history():
    saved = season({"id": "CL", "category": "continental", "rating": 9.9, "rated_games": 20,
                    "matches": [{"id": "tm-only", "date": "2024-04-01"}]})
    result = calculate_weighted_rating(saved)
    assert result["status"] == "missing_history"
    assert result["rating"] is result["raw_rating"] is None
    assert result["total_weight"] == result["rated_games"] == 0


def test_unknown_position_keeps_raw_weighted_mean_without_an_adjusted_rating():
    result = calculate_weighted_rating(season(competition(), coefficient=None))
    assert result["status"] == "missing_position"
    assert result["rating"] is result["uncapped_rating"] is None
    assert result["raw_rating"] == 8
    assert result["position_coefficient"] is None


@pytest.mark.parametrize("stage", [None, "19", "Round 19", "Unknown phase"])
def test_unknown_cl_stage_is_not_silently_assumed_to_be_a_group_game(stage):
    result = result_for(competition(match=game(stage=stage)))
    assert result["status"] == "incomplete_context"
    assert result["rating"] is result["raw_rating"] is None
    assert result["unresolved_match_ids"] == ["1"]
    assert result["matches"][0]["weight"] is None
    assert result["matches"][0]["included"] is False
    assert result["warnings"]


def test_unknown_rated_context_exposes_only_partial_diagnostics_never_a_final_rating():
    known = competition("L1", category="domestic_league", kind=1, match=game(1, rating=6, stage=None))
    unknown = competition(match=game(2, rating=9, stage=None))
    result = calculate_weighted_rating(season(known, unknown))
    assert result["status"] == "incomplete_context"
    assert result["rated_games"] == 2
    assert result["included_games"] == 1
    assert result["rating"] is result["raw_rating"] is None
    assert result["partial_rating"] == result["partial_raw_rating"] == 6
    assert result["unresolved_match_ids"] == ["2"]


@pytest.mark.parametrize("kind", [1, 2, 3, 4, 5, 6, 7])
def test_known_regular_league_format_allows_absent_stage_without_inventing_a_playoff(kind):
    entry = competition("L1", category="domestic_league", kind=kind, match=game(stage=None))
    assert result_for(entry)["status"] == "complete"
    assert result_for(entry)["total_weight"] == 1


def test_explicit_league_playoff_semifinal_still_receives_the_any_semifinal_rule():
    entry = competition("L1", category="domestic_league", kind=1, match=game(stage="1/2"))
    assert result_for(entry)["total_weight"] == 2


def test_missing_cup_stage_is_incomplete_even_though_cup_semifinal_bonus_is_excluded():
    entry = competition("DFB", category="domestic_cup", kind=8, match=game(stage=None))
    assert result_for(entry)["status"] == "incomplete_context"


def test_source_tm_group_code_fills_missing_fotmob_group_stage():
    entry = competition(match=game(stage=None), matches=[{"id": "tm-1", "date": "2024-04-01", "stage_code": "A"}])
    result = result_for(entry)
    assert result["status"] == "complete"
    assert result["matches"][0]["context"]["stage"] == "group"
    assert result["total_weight"] == 1.5


def test_dated_qualifying_match_overrides_incorrect_main_cl_parent():
    qualifier = competition("CLQ", match=game(2, rating=None), matches=[
        {"id": "tm-q", "date": "2024-04-01", "team": "Player Club", "opponent": "Neutral Club", "stage_code": "AF"}])
    main = competition(match=game(1, stage="1/8"), matches=[
        {"id": "tm-cl", "date": "2024-05-01", "team": "Player Club", "stage_code": "HF"}])
    result = calculate_weighted_rating(season(main, qualifier))
    assert result["status"] == "complete"
    assert result["matches"][0]["context"]["competition_id"] == "CLQ"
    assert result["matches"][0]["weight"] == 1


def test_bronze_match_does_not_receive_semifinal_or_final_additions():
    entry = competition("FIWC", category="national_major", kind=11, match=game(stage=None, opponent="Argentina"),
                        matches=[{"id": "tm-1", "date": "2024-04-01", "stage_code": "PD"}])
    result = result_for(entry)
    assert result["status"] == "complete"
    assert result["matches"][0]["context"]["stage"] == "third_place"
    assert result["total_weight"] == 3
    assert not {"semifinal", "final", "major_final"}.intersection(rule["key"] for rule in result["matches"][0]["weight_rules"])


def test_unrated_unknown_stage_is_visible_but_does_not_block_the_rated_match_mean():
    valid = competition("L1", category="domestic_league", kind=1, match=game(1, rating=7, stage=None))
    unrated = competition(match=game(2, rating=None, stage=None))
    result = calculate_weighted_rating(season(valid, unrated))
    assert result["status"] == "complete"
    assert result["rating"] == 7
    assert result["matches"][1]["context_issues"]
    assert result["matches"][1]["included"] is False


def test_source_snapshot_and_raw_match_statistics_are_unchanged_and_repeated_calls_do_not_compound():
    saved = season(competition(match=game(stage="1/2", opponent="Real Madrid", rating=9.8)), coefficient=.9253)
    before = deepcopy(saved)
    result = calculate_weighted_rating(saved)
    assert saved == before
    assert calculate_weighted_rating(saved) == result
    assert result["matches"][0]["rating"] == result["matches"][0]["raw_rating"] == 9.8
    assert result["matches"][0]["adjusted_rating"] == 10
    result["matches"][0]["context"]["stage"] = "Changed"
    assert saved == before


def test_explicit_cl_playin_is_knockout_but_neither_semifinal_nor_final():
    result = result_for(competition(match=game(stage="playoff")))
    assert result["status"] == "complete"
    assert result["total_weight"] == 4
    assert not {"semifinal", "final"}.intersection(rule["key"] for rule in result["matches"][0]["weight_rules"])


def test_missing_opponent_with_unverified_id_is_not_assumed_to_be_a_non_grand():
    entry = competition("L1", category="domestic_league", kind=1, match=game(stage=None, opponent=None, opponent_id="999999"))
    result = result_for(entry)
    assert result["status"] == "incomplete_context"
    assert result["rating"] is None
    assert result["unresolved_match_ids"] == ["1"]


def test_missing_fotmob_opponent_can_use_the_matching_tm_match_name():
    entry = competition("L1", category="domestic_league", kind=1, match=game(stage=None, opponent=None),
                        matches=[{"id": "tm-1", "date": "2024-04-01", "opponent": "Liverpool"}])
    result = result_for(entry)
    assert result["status"] == "complete"
    assert result["total_weight"] == 1.5
    assert result["matches"][0]["context"]["opponent"] == "Liverpool"


@pytest.mark.parametrize("missing_history", [None, [], [{"id": "missing", "date": "2024-05-01"}]])
def test_missing_rated_source_competition_prevents_a_complete_rating_from_the_remaining_subset(missing_history):
    available = competition("L1", category="domestic_league", kind=1, match=game(1, rating=7, stage=None), rated_games=1)
    missing = {"id": "CL", "name": "Champions League", "category": "continental", "rating": 8.5,
               "rated_games": 5, "fotmob_matches": missing_history}
    result = calculate_weighted_rating(season(available, missing))
    assert result["status"] == "missing_history"
    assert result["rating"] is result["raw_rating"] is None
    assert result["partial_rating"] == result["partial_raw_rating"] == 7
    assert result["missing_source_samples"][0]["code"] == "missing_source_sample"
    assert result["missing_source_samples"][0]["competition_id"] == "CL"


def test_partially_truncated_history_is_detected_from_real_rated_sample_count():
    entry = competition("L1", category="domestic_league", kind=1, match=game(stage=None), rated_games=2)
    result = result_for(entry)
    assert result["status"] == "missing_history"
    assert result["rating"] is None
    assert result["missing_source_samples"][0]["expected_rated_games"] == 2
    assert result["missing_source_samples"][0]["available_rated_games"] == 1


def test_explicit_unrated_history_is_complete_when_it_matches_the_real_rated_sample_count():
    entry = competition("L1", category="domestic_league", kind=1, rated_games=1,
                        fotmob_matches=[game(1, rating=7, stage=None), game(2, rating=None, stage=None)])
    result = result_for(entry)
    assert result["status"] == "complete"
    assert result["rating"] == 7
    assert result["unrated_games"] == 1
    assert result["missing_source_samples"] == []


def fotmob_context(kind="league", *, identity="87", stage=None, opponent="Unfamiliar Source Club", **extra):
    categories = {
        "league": "domestic_league", "domestic_cup": "domestic_cup", "supercup": "domestic_cup",
        "champions_league": "continental", "continental": "continental", "world_cup": "national_major",
        "euro_copa": "national_major", "national_regular": "national_regular", "other_major": "national_major",
        "friendly": "national_regular",
    }
    return {
        "competition_provider": "fotmob", "competition_id": identity, "competition": "Source competition",
        "competition_kind": kind, "category": categories.get(kind), "competition_type_id": None,
        "stage_code": None, "stage": stage,
        "is_knockout": stage in {"round_of_16", "quarterfinal", "semifinal", "final", "playoff", "third_place"} if stage is not None else None,
        "opponent": opponent, "opponent_id": "999999",
        "is_national": kind in {"world_cup", "euro_copa", "national_regular", "other_major", "friendly"},
        "match_context_status": "matched", **extra,
    }


@pytest.mark.parametrize(("kind", "identity", "stage", "opponent", "expected"), [
    ("league", "87", None, "Unfamiliar Source Club", 1),
    ("friendly", "114", None, "France", 1),
    ("domestic_cup", "132", "semifinal", "Liverpool", 1.5),
    ("domestic_cup", "132", "final", "Liverpool", 3.5),
    ("supercup", "57", "final", "Manchester City", 1.5),
    ("supercup", "57", None, "Manchester City", 1.5),
    ("champions_league", "42", "semifinal", "Real Madrid", 5.5),
    ("champions_league", "42", "final", "Inter", 6.5),
    ("world_cup", "77", "final", "Argentina", 5.5),
    ("euro_copa", "50", "semifinal", "France", 3),
    ("euro_copa", "44", "final", "Brazil", 4.5),
    ("other_major", "289", "semifinal", "France", 2),
    ("continental", "73", "semifinal", "Real Madrid", 2.5),
])
def test_fotmob_context_contract_selects_rules_without_transfermarkt_types(monkeypatch, kind, identity, stage, opponent, expected):
    context = fotmob_context(kind, identity=identity, stage=stage, opponent=opponent)
    monkeypatch.setattr(weighting, "resolve_match_context", lambda *args, **kwargs: context)
    result = result_for(competition())
    assert result["status"] == "complete"
    assert result["rating"] == 8
    assert result["total_weight"] == expected
    assert result["matches"][0]["context"]["competition_type_id"] is None
    assert result["matches"][0]["context"]["competition_id"] == identity


@pytest.mark.parametrize(("kind", "identity", "stage"), [
    ("champions_league", "42", None), ("world_cup", "77", None), ("euro_copa", "50", None),
    ("domestic_cup", "132", None), ("national_regular", "9806", None),
    (None, "999999", "final"), ("unknown", "999999", "group"),
    ("champions_league", "999999", "group"),
])
def test_incomplete_fotmob_context_is_not_an_optimistic_default(monkeypatch, kind, identity, stage):
    context = fotmob_context(kind, identity=identity, stage=stage)
    monkeypatch.setattr(weighting, "resolve_match_context", lambda *args, **kwargs: context)
    result = result_for(competition())
    assert result["status"] == "incomplete_context"
    assert result["rating"] is None
    assert result["matches"][0]["weight"] is None


def test_fotmob_non_main_international_competition_never_gets_cl_additions(monkeypatch):
    context = fotmob_context("continental", identity="42", stage="semifinal")
    monkeypatch.setattr(weighting, "resolve_match_context", lambda *args, **kwargs: context)
    result = result_for(competition())
    assert result["total_weight"] == 2
    assert not any(rule["key"].startswith("champions_league") for rule in result["matches"][0]["weight_rules"])


def test_unmatched_context_message_does_not_require_transfermarkt(monkeypatch):
    context = fotmob_context(match_context_status="unmatched")
    monkeypatch.setattr(weighting, "resolve_match_context", lambda *args, **kwargs: context)
    result = result_for(competition())
    issues = result["matches"][0]["context_issues"]
    assert "Недостаточно данных для определения контекста матча." in issues
    assert not any("Transfermarkt" in issue for issue in issues)


def test_complete_regular_fotmob_league_needs_no_tm_dates_or_opponent_alias():
    entry = competition("ES1", name="LaLiga", category="domestic_league", kind=1,
                        match=game(stage=None, opponent="Unfamiliar FotMob Club", competition_id="87", competition="LaLiga"))
    result = result_for(entry)
    context = result["matches"][0]["context"]
    assert result["status"] == "complete"
    assert result["total_weight"] == 1
    assert context["competition_provider"] == "fotmob"
    assert context["competition_id"] == "87"
    assert context["competition_type_id"] is None
    assert context["opponent"] == "Unfamiliar FotMob Club"


def test_explicit_fotmob_main_cl_stage_wins_over_conflicting_tm_context():
    entry = competition(match=game(stage="1/2", opponent="Real Madrid", competition_id="42", competition="Champions League"),
                        matches=[{"id": "tm-final", "date": "2024-04-01", "team": "Player Club",
                                  "opponent": "Different Transfermarkt Opponent", "stage_code": "FF"}])
    result = result_for(entry)
    assert result["status"] == "complete"
    assert result["total_weight"] == 5.5
    assert result["matches"][0]["context"]["competition_provider"] == "fotmob"
    assert result["matches"][0]["context"]["stage"] == "semifinal"
    assert not any(rule["key"] == "final" for rule in result["matches"][0]["weight_rules"])


@pytest.mark.parametrize(("identity", "name", "stage", "expected"), [
    ("132", "FA Cup", "1/2", 1.5),
    ("77", "World Cup", "1/2", 4),
    ("50", "EURO", "1/2", 3),
    ("139", "Community Shield", None, 1.5),
])
def test_complete_fotmob_cup_major_and_supercup_contexts_select_actual_rules(identity, name, stage, expected):
    opponent = "France" if identity in {"77", "50"} else "Liverpool"
    entry = competition("TM-PARENT", category="other", kind=None,
                        match=game(stage=stage, opponent=opponent, competition_id=identity, competition=name))
    result = result_for(entry)
    assert result["status"] == "complete"
    assert result["total_weight"] == expected
    assert result["matches"][0]["context"]["competition_provider"] == "fotmob"
    assert result["matches"][0]["context"]["competition_type_id"] is None


def test_missing_fotmob_cl_stage_still_uses_verified_tm_group_fallback():
    entry = competition(match=game(stage=None, competition_id="42", competition="Champions League"),
                        matches=[{"id": "tm-group", "date": "2024-04-01", "stage_code": "A"}])
    result = result_for(entry)
    assert result["status"] == "complete"
    assert result["total_weight"] == 1.5
    assert result["matches"][0]["context"].get("competition_provider") != "fotmob"
    assert result["matches"][0]["context"]["stage_code"] == "A"


def test_missing_cl_stage_without_source_fallback_remains_incomplete():
    entry = competition(match=game(stage=None, competition_id="42", competition="Champions League"))
    result = result_for(entry)
    assert result["status"] == "incomplete_context"
    assert result["rating"] is None
    assert result["matches"][0]["weight"] is None


def test_unknown_fotmob_competition_does_not_default_to_a_regular_or_final_weight():
    entry = competition("UNVERIFIED", name="Unrecognized Tournament", category="other", kind=None,
                        match=game(stage="final", competition_id="999999", competition="Unrecognized Tournament"),
                        matches=[{"id": "tm-other-date", "date": "2024-05-01", "stage_code": "FF"}])
    result = result_for(entry)
    assert result["status"] == "incomplete_context"
    assert result["rating"] is None
    assert result["matches"][0]["weight"] is None
    assert result["matches"][0]["context"]["match_context_status"] == "unmatched"
