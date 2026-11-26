"""Season honours are counted once per kind; match weighting stays explicit."""

from copy import deepcopy
import math

import pytest

from season_ledger.points import calculate_season_points


def row(identity, category, *, name=None, games=1, **extra):
    return {"id": identity, "name": name or identity, "category": category, "games": games,
            "team": "Club", "place": None, "stage_code": None, "stage_history": [],
            "final_outcome": None, **extra}


def league(*, games=15, **extra):
    return row("L1", "domestic_league", name="Bundesliga", competition_type_id=1, games=games, **extra)


def cl(*, games=5, **extra):
    return row("CL", "continental", name="UEFA Champions League", games=games, **extra)


def cup(identity="DFB", *, cup_type=8, **extra):
    return row(identity, "domestic_cup", name="National Cup", competition_type_id=cup_type, **extra)


def world_cup(identity="FIWC", *, games=3, **extra):
    return row(identity, "national_major", name="World Cup", team="Nation", games=games, **extra)


def national_cup(identity="EURO", *, games=3, **extra):
    return row(identity, "national_major", name="European Championship" if identity == "EURO" else "Copa America", team="Nation", games=games, **extra)


def europa_league(*, games=5, **extra):
    return row("EL", "continental", name="UEFA Europa League", games=games, **extra)


def conference_league(*, games=5, **extra):
    return row("UCOL", "continental", name="UEFA Conference League", games=games, **extra)


def afcon(*, games=3, **extra):
    return row("AFCN", "national_major", name="Africa Cup of Nations", games=games, **extra)


def snapshot(*competitions):
    return {"name": "Season Player", "position": "GK", "competitions": list(competitions),
            "total": {"rating": 7.77, "adjusted_rating": 8.397},
            "rating_adjustment": {"coefficient": .9253, "method": "divide"}}


def points(*competitions, weighted_rating=7.0):
    # Honour tests isolate their awards from absence deductions. One unrated
    # appearance in each scope prevents deductions without earning an award.
    return calculate_season_points(snapshot(*competitions,
        cl(games=1), row("FS", "national_regular", team="Nation")), weighted_rating=weighted_rating)


def assert_complete_award(result, bonus, *, weighted_rating=7.0):
    assert result["weighted_rating"] == weighted_rating
    assert result["base_points"] == pytest.approx(weighted_rating * 100)
    assert result["bonus_points"] == bonus
    assert result["total"] == pytest.approx(weighted_rating * 100 + bonus)
    assert result["status"] == "complete"
    assert not result["pending"]


def test_existing_kane_semifinal_and_euro_finalist_get_50_without_inventing_a_weighted_rating():
    saved = snapshot(cl(games=12, place="Полуфинал", stage_code="HFR"),
                     national_cup(games=7, place="2", stage_code="FF", final_outcome="runner_up"))
    result = calculate_season_points(saved)
    assert result["weighted_rating"] is None
    assert result["base_points"] is None
    assert result["bonus_points"] == 50
    assert result["total"] is None
    assert result["status"] == "awaiting_weighted_rating"
    assert len(result["bonuses"]) == 2
    assert result["formula_version"]


def test_weighted_rating_is_explicit_and_never_replaced_by_source_or_adjusted_season_average():
    saved = snapshot()
    saved["weighted_rating"] = 9.5
    assert calculate_season_points(saved, weighted_rating=None)["base_points"] is None
    result = calculate_season_points(saved, weighted_rating=6.5003)
    assert_complete_award(result, -37, weighted_rating=6.5003)
    assert result["base_points"] == pytest.approx(650.03)


@pytest.mark.parametrize("weighted_rating", [0, 7.234567, 10])
def test_valid_weighted_rating_is_multiplied_by_100_without_display_rounding(weighted_rating):
    result = points(cup(final_outcome="winner"), weighted_rating=weighted_rating)
    assert_complete_award(result, 12, weighted_rating=weighted_rating)


@pytest.mark.parametrize("weighted_rating", [True, False, -0.0001, 10.0001, math.nan, math.inf, -math.inf, "7.0", {}])
def test_invalid_weighted_rating_is_rejected(weighted_rating):
    with pytest.raises(ValueError):
        points(weighted_rating=weighted_rating)


def test_confirmed_league_winner_gets_37_once_despite_multiple_champion_clubs():
    result = points(league(team="First Club", place="1", competition_complete=True),
                    league(team="Second Club", place="1", competition_complete=True))
    assert_complete_award(result, 37)
    assert len(result["bonuses"]) == 1


@pytest.mark.parametrize("complete", [False, None])
def test_current_or_unconfirmed_league_leader_does_not_receive_a_winner_bonus(complete):
    result = points(league(place="1", competition_complete=complete))
    assert result["base_points"] == 700
    assert result["bonus_points"] == 0
    assert result["total"] is None
    assert result["status"] == "awaiting_results"
    assert result["pending"]


def test_completed_nonwinning_league_position_is_not_a_pending_title():
    assert_complete_award(points(league(place="2", competition_complete=True)), 0)


@pytest.mark.parametrize(("outcome", "expected"), [("winner", 75), ("runner_up", 50)])
def test_cl_final_outcome_has_priority_over_conflicting_place_and_semifinal_history(outcome, expected):
    result = points(cl(final_outcome=outcome, place="2" if outcome == "winner" else "1",
                       stage_code="FF", stage_history=["VFR", "HFR", "FF"]))
    assert_complete_award(result, expected)


@pytest.mark.parametrize(("place", "expected"), [
    ("1", 75), ("2", 50), ("Полуфинал", 25), ("1/4 финала", 12), ("Четвертьфинал", 12),
])
def test_cl_legacy_place_fallback(place, expected):
    assert_complete_award(points(cl(place=place)), expected)


@pytest.mark.parametrize("stage", ["HF", "HFH", "HFR"])
def test_cl_semifinal_stage_codes_award_25(stage):
    assert_complete_award(points(cl(stage_code=stage)), 25)


@pytest.mark.parametrize("stage", ["VF", "VFH", "VFR"])
@pytest.mark.parametrize("field", ["stage_code", "stage_history"])
def test_cl_quarterfinal_stage_codes_award_12(stage, field):
    value = ["AFR", stage] if field == "stage_history" else stage
    assert_complete_award(points(cl(**{field: value})), 12)


def test_cl_explicit_quarterfinal_outcome_awards_12():
    assert_complete_award(points(cl(final_outcome="quarterfinal")), 12)


def test_cl_semifinal_takes_priority_over_quarterfinal_history():
    result = points(cl(place="Полуфинал", stage_code="HFR", stage_history=["VFH", "VFR", "HFH", "HFR"]))
    assert_complete_award(result, 25)


def test_cl_earlier_round_has_no_honour_bonus():
    assert_complete_award(points(cl(place="1/8 финала", stage_code="AFR", stage_history=["AFH", "AFR"])), 0)


@pytest.mark.parametrize("factory", [cup, world_cup, national_cup])
def test_quarterfinal_bonus_is_only_awarded_for_cl(factory):
    assert_complete_award(points(factory(place="1/4 финала", stage_code="VFR", stage_history=["VFH", "VFR"])), 0)


def test_cl_winner_runner_up_semifinal_and_quarterfinal_do_not_stack_within_the_same_kind():
    result = points(cl(team="Winner", final_outcome="winner"),
                    cl(team="Finalist", final_outcome="runner_up"),
                    cl(team="Semifinalist", stage_code="HF"),
                    cl(team="Quarterfinalist", stage_code="VF"))
    assert_complete_award(result, 75)
    assert len(result["bonuses"]) == 1


@pytest.mark.parametrize("identity", ["EL", "ECL", "USC", "KLUB", "CLQ", "ACL", "CAFCL"])
def test_other_international_club_competitions_have_no_honour_bonus(identity):
    result = points(row(identity, "continental", name="UEFA Champions League" if identity == "CLQ" else identity,
                        final_outcome="winner", place="1", competition_complete=True))
    assert_complete_award(result, 0)


@pytest.mark.parametrize("cup_type", [8, 14])
def test_national_cup_and_league_cup_winner_award_12(cup_type):
    assert_complete_award(points(cup(cup_type=cup_type, final_outcome="winner")), 12)
    assert_complete_award(points(cup(cup_type=cup_type, place="1")), 12)


def test_multiple_cup_wins_from_multiple_clubs_and_cup_types_count_once():
    result = points(cup("DFB", cup_type=8, team="First Club", final_outcome="winner"),
                    cup("FAC", cup_type=8, team="Second Club", final_outcome="winner"),
                    cup("EFL", cup_type=14, team="Second Club", final_outcome="winner"))
    assert_complete_award(result, 12)
    assert len(result["bonuses"]) == 1


@pytest.mark.parametrize("cup_type", [9, 13, 15, 23])
def test_supercups_and_youth_cups_are_excluded_even_after_a_win(cup_type):
    assert_complete_award(points(cup(cup_type=cup_type, final_outcome="winner", place="1")), 0)


@pytest.mark.parametrize("outcome", ["runner_up", None])
def test_cup_nonwinner_does_not_receive_the_winners_12(outcome):
    assert_complete_award(points(cup(final_outcome=outcome, place="2")), 0)


def test_unknown_cup_type_with_a_win_is_pending_instead_of_assuming_a_domestic_cup():
    result = points(cup(cup_type=None, final_outcome="winner", place="1"))
    assert result["bonus_points"] == 0
    assert result["total"] is None
    assert result["status"] == "awaiting_results"
    assert result["pending"]


def test_recognised_supercup_name_without_type_is_excluded_instead_of_pending():
    entry = cup(cup_type=None, final_outcome="winner", place="1")
    entry["name"] = "Super Cup"
    assert_complete_award(points(entry), 0)


@pytest.mark.parametrize("name", [
    "Supercopa", "Supercopa de España", "Supercoppa Italiana", "DFL-Supercup", "UEFA Super Cup",
    "Supercoupe", "Super Coupe", "Trophée des Champions", "Community Shield", "Charity Shield",
    "Supertaça Cândido de Oliveira", "Recopa", "Recopa Sudamericana", "U19 Supercup",
    "Johan Cruijff Schaal", "Johan Cruyff Shield", "PTT Telecom Cup", "Challenge des Champions",
])
@pytest.mark.parametrize("cup_type", [None, 8, 14])
def test_supercup_name_excludes_winner_bonus_even_when_legacy_type_says_ordinary_cup(name, cup_type):
    entry = cup("LEGACY", cup_type=cup_type, final_outcome="winner", place="1")
    entry["name"] = name
    assert_complete_award(points(entry), 0)


def test_supercup_win_does_not_replace_an_ordinary_cup_defeat_or_add_pending_honours():
    ordinary = cup(final_outcome="runner_up", place="2")
    supercup = cup("LEGACY", cup_type=8, final_outcome="winner", place="1")
    supercup["name"] = "Supercopa de España"
    assert_complete_award(points(ordinary, supercup), 0)


@pytest.mark.parametrize("identity", ["FIWC", "WM"])
@pytest.mark.parametrize(("place", "expected"), [("1", 87), ("2", 25), ("Полуфинал", 12)])
def test_world_cup_outcomes(identity, place, expected):
    assert_complete_award(points(world_cup(identity, place=place)), expected)


@pytest.mark.parametrize("identity", ["EURO", "COPA", "CA16"])
@pytest.mark.parametrize(("place", "expected"), [("1", 75), ("2", 25), ("Полуфинал", 0)])
def test_euro_and_copa_outcomes(identity, place, expected):
    assert_complete_award(points(national_cup(identity, place=place)), expected)


@pytest.mark.parametrize(("factory", "expected"), [(world_cup, 12), (national_cup, 0)])
def test_third_place_match_preserves_only_the_world_cup_semifinal_bonus(factory, expected):
    entry = factory(place="3", stage_code="SPIEL3", stage_history=["HF", "SPIEL3"])
    assert_complete_award(points(entry), expected)


def test_euro_and_copa_awards_share_one_highest_bonus_kind():
    result = points(national_cup("EURO", final_outcome="winner"),
                    national_cup("COPA", final_outcome="runner_up"),
                    national_cup("CA16", stage_code="HF"))
    assert_complete_award(result, 75)
    assert len(result["bonuses"]) == 1


@pytest.mark.parametrize(("identity", "category"), [
    ("WMQ", "national_regular"), ("EMQ", "national_regular"),
    ("FIWC", "national_regular"), ("EURO", "national_regular"),
    ("AFCON", "national_major"), ("ASIAN", "national_major"),
    ("GOLD", "national_major"), ("EM", "national_major"),
])
def test_qualifiers_and_other_national_tournaments_have_no_world_cup_euro_bonus(identity, category):
    assert_complete_award(points(row(identity, category, final_outcome="winner", place="1")), 0)


@pytest.mark.parametrize("factory", [
    lambda: league(place="1", competition_complete=True),
    lambda: cl(final_outcome="winner"),
    lambda: cup(final_outcome="winner"),
    lambda: world_cup(final_outcome="winner"),
    lambda: national_cup(final_outcome="winner"),
])
@pytest.mark.parametrize("games", [0, None])
def test_a_team_honour_without_player_participation_does_not_award_points(factory, games):
    entry = factory()
    entry["games"] = games
    assert_complete_award(points(entry), 0)


@pytest.mark.parametrize("factory", [cl, world_cup, national_cup])
def test_unresolved_final_with_prior_semifinal_does_not_guess_a_semifinal_award(factory):
    result = points(factory(stage_code="FF", stage_history=["VF", "HF", "FF"], place="Полуфинал"))
    assert result["bonus_points"] == 0
    assert result["total"] is None
    assert result["status"] == "awaiting_results"
    assert result["pending"]


def test_unplayed_unresolved_final_cannot_make_the_players_score_pending():
    assert_complete_award(points(cl(games=0, stage_code="FF", stage_history=["HF", "FF"])), 0)


@pytest.mark.parametrize(("known_outcome", "known_bonus", "pending_relevant"), [
    ("winner", 75, False), ("runner_up", 50, True), ("quarterfinal", 12, True),
])
def test_pending_same_kind_can_be_ignored_only_after_the_maximum_bonus_is_already_won(known_outcome, known_bonus, pending_relevant):
    result = points(cl(team="Known Club", final_outcome=known_outcome),
                    cl(team="Unresolved Club", stage_code="FF", stage_history=["HFR", "FF"]))
    assert result["bonus_points"] == known_bonus
    if pending_relevant:
        assert result["pending"]
        assert result["status"] == "awaiting_results"
        assert result["total"] is None
    else:
        assert_complete_award(result, known_bonus)


def test_pending_cup_type_and_league_leader_do_not_duplicate_already_maximum_awards():
    result = points(cup(final_outcome="winner"), cup("UNKNOWN", cup_type=None, final_outcome="winner"),
                    league(team="Champion", place="1", competition_complete=True),
                    league(team="Current Leader", place="1", competition_complete=False))
    # Each bonus was halved and rounded down separately: 37 + 12, not 100 / 2.
    assert_complete_award(result, 49)


def test_missing_weighted_rating_remains_the_primary_pending_status_while_honours_are_preserved():
    result = points(cl(final_outcome="runner_up"), league(place="1", competition_complete=None), weighted_rating=None)
    assert result["bonus_points"] == 50
    assert result["base_points"] is result["total"] is None
    assert result["status"] == "awaiting_weighted_rating"
    assert result["pending"]


def test_total_can_exceed_1000_and_all_distinct_bonus_kinds_reach_1346():
    result = points(league(place="1", competition_complete=True), cl(final_outcome="winner"),
                    cup(final_outcome="winner"), world_cup(final_outcome="winner"),
                    national_cup(final_outcome="winner"), europa_league(final_outcome="winner"),
                    conference_league(final_outcome="winner"), afcon(final_outcome="winner"), weighted_rating=10)
    assert_complete_award(result, 346, weighted_rating=10)
    assert result["total"] == 1346
    assert len(result["bonuses"]) == 8


def test_calculation_is_repeatable_and_does_not_mutate_saved_competitions_or_source_ratings():
    saved = snapshot(cl(final_outcome="runner_up"), league(place="1", competition_complete=True))
    saved["competitions"][0]["rating"] = 7.758333333333333
    saved["competitions"][0]["matches"] = [{"id": "4446289", "rating": 8.3}]
    saved["warnings"] = ["Original warning"]
    before = deepcopy(saved)
    first = calculate_season_points(saved, weighted_rating=7.123456)
    assert saved == before
    assert calculate_season_points(saved, weighted_rating=7.123456) == first
    assert first["formula_version"]
    assert_complete_award(first, 75, weighted_rating=7.123456)


@pytest.mark.parametrize(("factory", "minimum", "bonus"), [
    (league, 15, 37), (cl, 5, 75), (cup, 1, 12),
    (world_cup, 3, 87), (lambda **extra: world_cup("WM", **extra), 3, 87),
    (national_cup, 3, 75), (lambda **extra: national_cup("COPA", **extra), 3, 75),
    (lambda **extra: national_cup("CA16", **extra), 3, 75),
])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_bonus_requires_the_minimum_played_games_inclusively(factory, minimum, bonus, offset):
    entry = factory(games=minimum + offset, place="1", final_outcome="winner", competition_complete=True)
    # Unrated appearances still count towards participation, however few minutes
    # the player spent on the pitch. Neither value replaces the games count.
    entry.update(rated_games=0, minutes=minimum + offset)
    assert_complete_award(points(entry), bonus if offset >= 0 else 0)


@pytest.mark.parametrize(("factory", "games"), [
    (league, 14), (cl, 4), (world_cup, 2), (national_cup, 2),
])
def test_insufficient_appearances_cannot_make_an_unconfirmed_result_pending(factory, games):
    entry = factory(games=games, place="Финал", stage_code="FF", competition_complete=False)
    assert_complete_award(points(entry), 0)


@pytest.mark.parametrize(("factory", "games"), [(league, 14), (cl, 4), (world_cup, 2), (national_cup, 2)])
def test_appearances_for_different_teams_are_not_combined_for_a_bonus(factory, games):
    first = factory(games=games, place="1", competition_complete=True)
    second = factory(games=games, place="1", competition_complete=True)
    first["team"], second["team"] = "First Team", "Second Team"
    assert_complete_award(points(first, second), 0)


def test_euro_and_copa_appearances_are_not_combined_to_meet_the_threshold():
    result = points(national_cup("EURO", games=2, final_outcome="winner"),
                    national_cup("COPA", games=2, final_outcome="winner"))
    assert_complete_award(result, 0)


def test_ineligible_winner_does_not_replace_a_bonus_for_an_eligible_lower_result():
    result = points(cl(games=5, team="Quarterfinalist", stage_code="VFR"),
                    cl(games=4, team="Winner", final_outcome="winner"),
                    cl(games=4, team="Unconfirmed", stage_code="FF"))
    assert_complete_award(result, 12)
    assert result["bonuses"][0]["team"] == "Quarterfinalist"


@pytest.mark.parametrize("identity", ["EURO", "COPA", "CA16"])
@pytest.mark.parametrize("place", ["Полуфинал", "3", "4"])
def test_euro_and_copa_semifinalists_have_no_bonus_even_with_sufficient_appearances(identity, place):
    entry = national_cup(identity, games=6, place=place, stage_history=["HF", "SPIEL3"])
    assert_complete_award(points(entry), 0)


@pytest.mark.parametrize(("identity", "name"), [
    ("GB1", "Premier League"), ("FR1", "Ligue 1"), ("ES1", "LaLiga"),
    ("IT1", "Serie A"), ("L1", "Bundesliga"),
])
def test_only_verified_top_five_leagues_award_the_championship_bonus(identity, name):
    entry = row(identity, "domestic_league", name=name, games=15, place="1", competition_complete=True)
    assert_complete_award(points(entry), 37)


@pytest.mark.parametrize(("identity", "league_penalty"), [
    ("MLS1", -15), ("POUS", -15), ("SA1", -15), ("BRA1", -15), ("PO1", -15), ("NL1", -15),
    ("GB2", -40), ("FR2", -40), ("ES2", -40), ("IT2", -40), ("L2", -40), (None, 0), ("", 0),
])
@pytest.mark.parametrize("complete", [True, False, None])
def test_other_leagues_only_apply_their_tier_penalty_without_pending_even_with_a_top_five_name(identity, league_penalty, complete):
    entry = row(identity, "domestic_league", name="Premier League", games=38,
                place="1", competition_complete=complete)
    assert_complete_award(points(entry), league_penalty)


def test_ineligible_league_cannot_replace_the_result_of_an_eligible_top_five_league():
    eligible = league(games=15, place="2", competition_complete=True)
    other = row("SA1", "domestic_league", name="Saudi Pro League", games=38,
                place="1", competition_complete=True)
    assert_complete_award(points(eligible, other), -15)


@pytest.mark.parametrize(("cl_games", "national_games", "deductions"), [
    (0, 0, {"no_champions_league": -25, "no_national_team": -12}),
    (1, 0, {"no_national_team": -12}),
    (0, 1, {"no_champions_league": -25}),
    (1, 1, {}),
    (None, None, {"no_champions_league": -25, "no_national_team": -12}),
])
def test_absence_penalties_require_only_one_played_appearance_to_be_removed(cl_games, national_games, deductions):
    saved = snapshot(cl(games=cl_games), row("FS", "national_regular", games=national_games))
    result = calculate_season_points(saved, weighted_rating=7)
    assert {item["kind"]: item["points"] for item in result["bonuses"]} == deductions
    assert_complete_award(result, sum(deductions.values()))
    assert all(item["outcome"] == "no_appearances" for item in result["bonuses"])


def test_absence_penalties_are_awarded_once_per_season_and_can_make_the_total_negative():
    saved = snapshot(league(games=2), cup(games=1), cup("OTHER", games=3))
    before = deepcopy(saved)
    result = calculate_season_points(saved, weighted_rating=0)
    assert_complete_award(result, -37, weighted_rating=0)
    assert len(result["bonuses"]) == 2
    assert saved == before
    assert calculate_season_points(saved, weighted_rating=0) == result


def test_deductions_are_available_while_the_weighted_rating_is_missing():
    result = calculate_season_points(snapshot())
    assert result["bonus_points"] == -37
    assert result["base_points"] is result["total"] is None
    assert result["status"] == "awaiting_weighted_rating"


@pytest.mark.parametrize(("category", "kind"), [
    ("national_regular", None), ("national_major", None),
    ("other", 11), ("other", 17), ("other", 19), ("other", 20),
])
def test_any_national_appearance_including_youth_and_olympics_removes_the_national_penalty(category, kind):
    entry = row("NATIONAL", category, games=1, competition_type_id=kind, rated_games=0)
    result = calculate_season_points(snapshot(cl(games=1), entry), weighted_rating=7)
    assert_complete_award(result, 0)
    assert result["bonuses"] == []


def test_legacy_fotmob_national_flags_on_club_matches_do_not_hide_the_national_penalty():
    club = row("CCL", "continental", games=1, competition_type_id=10,
               fotmob_matches=[{"rating": 8, "is_national": True, "competition_id": "297"}])
    result = calculate_season_points(snapshot(cl(games=1), club), weighted_rating=7)
    assert_complete_award(result, -12)
    assert result["bonuses"][0]["kind"] == "no_national_team"


@pytest.mark.parametrize("identity", ["CLQ", "EL", "UCOL", "ECLQ", "ACL", "CAFCL"])
def test_other_continental_competitions_and_qualifiers_do_not_remove_the_main_cl_penalty(identity):
    entry = row(identity, "continental", games=10, name="UEFA Champions League")
    result = calculate_season_points(snapshot(entry, row("FS", "national_regular")), weighted_rating=7)
    assert_complete_award(result, -25)
    assert result["bonuses"][0]["kind"] == "no_champions_league"


@pytest.mark.parametrize(("factory", "minimum", "bonus"), [
    (europa_league, 5, 25), (conference_league, 5, 10), (afcon, 3, 25),
])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_new_winner_awards_require_their_minimum_appearances_inclusively(factory, minimum, bonus, offset):
    result = points(factory(games=minimum + offset, final_outcome="winner"))
    assert_complete_award(result, bonus if offset >= 0 else 0)


@pytest.mark.parametrize("factory", [europa_league, conference_league])
@pytest.mark.parametrize("outcome", ["runner_up", "semifinal", "quarterfinal"])
def test_europa_and_conference_leagues_only_award_the_winner(factory, outcome):
    assert_complete_award(points(factory(final_outcome=outcome)), 0)


@pytest.mark.parametrize(("place", "expected"), [("1", 25), ("2", 8), ("Полуфинал", 0), ("3", 0)])
def test_afcon_awards(place, expected):
    assert_complete_award(points(afcon(place=place, stage_history=["HF"])), expected)


@pytest.mark.parametrize("identity", ["AFCQ", "AFAC", "AFR", "AFCON", "CHAN"])
def test_only_the_verified_senior_afcon_final_tournament_receives_an_afcon_award(identity):
    entry = row(identity, "national_major", name="Africa Cup of Nations", games=7, final_outcome="winner")
    assert_complete_award(points(entry), 0)


@pytest.mark.parametrize(("identity", "name"), [
    ("ELQ", "UEFA Europa League"), ("ECLQ", "UEFA Conference League"),
])
def test_qualification_ids_do_not_receive_the_main_competition_winner_award(identity, name):
    entry = row(identity, "continental", name=name, games=10, final_outcome="winner")
    assert_complete_award(points(entry), 0)


@pytest.mark.parametrize("factory", [europa_league, conference_league, afcon])
def test_legacy_competition_names_without_ids_still_receive_their_awards(factory):
    entry = factory(final_outcome="winner")
    entry.pop("id")
    expected = {"UEFA Europa League": 25, "UEFA Conference League": 10, "Africa Cup of Nations": 25}[entry["name"]]
    assert_complete_award(points(entry), expected)


@pytest.mark.parametrize(("factory", "minimum", "maximum"), [
    (europa_league, 5, 25), (conference_league, 5, 10), (afcon, 3, 25),
])
def test_unconfirmed_new_finals_only_block_an_eligible_not_already_maximum_award(factory, minimum, maximum):
    result = points(factory(games=minimum, stage_code="FF"))
    assert result["status"] == "awaiting_results"
    assert result["bonus_points"] == 0
    assert_complete_award(points(factory(games=minimum - 1, stage_code="FF")), 0)
    assert_complete_award(points(factory(games=minimum, final_outcome="winner"),
                                 factory(games=minimum, stage_code="FF")), maximum)


def test_new_tournament_awards_and_absence_penalties_are_combined_as_signed_values():
    saved = snapshot(europa_league(final_outcome="winner"), conference_league(final_outcome="winner"),
                     afcon(place="2"))
    result = calculate_season_points(saved, weighted_rating=7)
    assert_complete_award(result, 18)
    assert {item["kind"]: item["points"] for item in result["bonuses"]} == {
        "europa_league": 25, "conference_league": 10, "afcon": 8, "no_champions_league": -25,
    }


def test_afcon_and_euro_copa_awards_belong_to_separate_categories():
    result = points(afcon(final_outcome="winner"), national_cup(final_outcome="winner"))
    assert_complete_award(result, 100)
    assert len(result["bonuses"]) == 2


def domestic_league(identity, games, *, team="Club", name=None):
    return row(identity, "domestic_league", name=name, games=games, team=team,
               place="2", competition_complete=True)


@pytest.mark.parametrize("identity", ["GB1", "FR1", "ES1", "IT1", "L1"])
def test_top_five_main_league_receives_neither_tier_penalty(identity):
    result = points(domestic_league(identity, 30))
    assert_complete_award(result, 0)
    assert result["bonuses"] == []


@pytest.mark.parametrize("identity", ["MLS1", "PO1", "SA1", "NL1", "BE1", "TR1", "RU1", "ARG1", "BRA1", "MEX1"])
def test_each_medium_main_league_receives_only_minus_15(identity):
    result = points(domestic_league(identity, 30))
    assert_complete_award(result, -15)
    assert {item["kind"]: item["points"] for item in result["bonuses"]} == {"outside_top_five": -15}


def test_other_main_league_receives_both_cumulative_tier_penalties():
    result = points(domestic_league("OTHER", 30))
    assert_complete_award(result, -40)
    assert {item["kind"]: item["points"] for item in result["bonuses"]} == {
        "outside_top_five": -15, "outside_medium_leagues": -25,
    }


@pytest.mark.parametrize(("first", "second", "expected", "selected"), [
    (("GB1", 1), ("PO1", 20), -15, "PO1"),
    (("GB1", 20), ("OTHER", 21), -40, "OTHER"),
    (("PO1", 20), ("OTHER", 21), -40, "OTHER"),
    (("OTHER", 20), ("PO1", 21), -15, "PO1"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_main_league_is_selected_by_played_appearances_not_the_best_league_or_source_order(first, second, expected, selected, reverse):
    entries = [domestic_league(*first), domestic_league(*second)]
    result = points(*(list(reversed(entries)) if reverse else entries))
    assert_complete_award(result, expected)
    assert all(item["competition_id"] == selected for item in result["bonuses"])


def test_appearances_in_the_same_league_are_combined_across_clubs_before_choosing_the_main_league():
    result = points(domestic_league("PO1", 8, team="First Club"),
                    domestic_league("PO1", 8, team="Second Club"), domestic_league("OTHER", 15))
    assert_complete_award(result, -15)
    assert result["bonuses"][0]["competition_id"] == "PO1"
    assert result["bonuses"][0]["games"] == 16


@pytest.mark.parametrize(("regular", "phase"), [
    ("MLS1", "POUS"), ("BE1", "EJPL"), ("BE1", "POBE"), ("BE1", "BPO4"), ("BE1", "ESJP"),
    ("NL1", "PONL"), ("NL1", "NL1P"),
    ("ARG1", "AR1N"), ("ARG1", "ARGC"), ("ARG1", "CDLP"), ("ARG1", "CDCA"),
    ("MEX1", "MEXA"), ("MEX1", "POMX"), ("MEX1", "POME"),
])
@pytest.mark.parametrize("phase_category", ["domestic_league", "other"])
def test_verified_top_division_phases_are_combined_for_main_league_selection(regular, phase, phase_category):
    playoff = domestic_league(phase, 5)
    playoff["category"] = phase_category
    result = points(domestic_league(regular, 10), playoff, domestic_league("OTHER", 14))
    assert_complete_award(result, -15)
    assert result["bonuses"][0]["competition_id"] == regular
    assert result["bonuses"][0]["games"] == 15


@pytest.mark.parametrize("identities", [("GB1", "PO1"), ("PO1", "OTHER")])
@pytest.mark.parametrize("reverse", [False, True])
def test_equal_appearance_counts_choose_the_better_tier(identities, reverse):
    entries = [domestic_league(identity, 15) for identity in identities]
    result = points(*(list(reversed(entries)) if reverse else entries))
    expected = 0 if "GB1" in identities else -15
    assert_complete_award(result, expected)


@pytest.mark.parametrize("games", [0, None, -1, True, math.nan, math.inf])
def test_unplayed_or_invalid_league_counts_cannot_override_the_main_league(games):
    result = points(domestic_league("GB1", 10), domestic_league("OTHER", games))
    assert_complete_award(result, 0)


def test_cup_continental_and_national_games_do_not_determine_the_main_league():
    not_leagues = [row("BESC", "domestic_cup", games=100, competition_type_id=9),
                   row("EL", "continental", games=100), row("FS", "national_regular", games=100),
                   row("PON2", "other", games=100, competition_type_id=12)]
    assert_complete_award(points(domestic_league("GB1", 10), *not_leagues), 0)


def test_even_a_legacy_supercup_mislabelled_as_a_league_cannot_determine_the_main_league():
    supercup = domestic_league("BESC", 100, name="Volkswagen Supercup")
    supercup["competition_type_id"] = 9
    assert_complete_award(points(domestic_league("GB1", 10), supercup), 0)


def test_no_identified_played_domestic_league_does_not_invent_a_tier_penalty():
    result = points(domestic_league(None, 30), domestic_league("", 30), domestic_league("OTHER", 0))
    assert_complete_award(result, 0)
    assert result["bonuses"] == []


def test_league_tier_penalties_stack_with_absence_penalties_only_once_each():
    saved = snapshot(domestic_league("OTHER", 15), domestic_league("OTHER", 10, team="Second Club"))
    before = deepcopy(saved)
    result = calculate_season_points(saved, weighted_rating=7)
    assert_complete_award(result, -77)
    assert {item["kind"]: item["points"] for item in result["bonuses"]} == {
        "no_champions_league": -25, "no_national_team": -12,
        "outside_top_five": -15, "outside_medium_leagues": -25,
    }
    assert saved == before
    assert calculate_season_points(saved, weighted_rating=7) == result


def test_main_league_aggregation_does_not_change_positive_title_bonus_participation_per_team():
    first = league(games=8, team="First Club", place="1", competition_complete=True)
    second = league(games=8, team="Second Club", place="1", competition_complete=True)
    assert_complete_award(points(first, second, domestic_league("OTHER", 15)), 0)


def test_a_top_five_title_bonus_can_coexist_with_a_different_main_leagues_tier_penalty():
    result = points(league(games=15, place="1", competition_complete=True), domestic_league("PO1", 16))
    assert_complete_award(result, 22)
    assert {item["kind"]: item["points"] for item in result["bonuses"]} == {"league": 37, "outside_top_five": -15}
