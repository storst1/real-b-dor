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


def snapshot(*competitions):
    return {"name": "Season Player", "position": "GK", "competitions": list(competitions),
            "total": {"rating": 7.77, "adjusted_rating": 8.397},
            "rating_adjustment": {"coefficient": .9253, "method": "divide"}}


def points(*competitions, weighted_rating=7.0):
    return calculate_season_points(snapshot(*competitions), weighted_rating=weighted_rating)


def assert_complete_award(result, bonus, *, weighted_rating=7.0):
    assert result["weighted_rating"] == weighted_rating
    assert result["base_points"] == pytest.approx(weighted_rating * 100)
    assert result["bonus_points"] == bonus
    assert result["total"] == pytest.approx(weighted_rating * 100 + bonus)
    assert result["status"] == "complete"
    assert not result["pending"]


def test_existing_kane_semifinal_and_euro_finalist_get_100_without_inventing_a_weighted_rating():
    saved = snapshot(cl(games=12, place="Полуфинал", stage_code="HFR"),
                     national_cup(games=7, place="2", stage_code="FF", final_outcome="runner_up"))
    result = calculate_season_points(saved)
    assert result["weighted_rating"] is None
    assert result["base_points"] is None
    assert result["bonus_points"] == 100
    assert result["total"] is None
    assert result["status"] == "awaiting_weighted_rating"
    assert len(result["bonuses"]) == 2
    assert result["formula_version"]


def test_weighted_rating_is_explicit_and_never_replaced_by_source_or_adjusted_season_average():
    saved = snapshot()
    saved["weighted_rating"] = 9.5
    assert calculate_season_points(saved, weighted_rating=None)["base_points"] is None
    result = calculate_season_points(saved, weighted_rating=6.5003)
    assert_complete_award(result, 0, weighted_rating=6.5003)
    assert result["base_points"] == pytest.approx(650.03)


@pytest.mark.parametrize("weighted_rating", [0, 7.234567, 10])
def test_valid_weighted_rating_is_multiplied_by_100_without_display_rounding(weighted_rating):
    result = points(cup(final_outcome="winner"), weighted_rating=weighted_rating)
    assert_complete_award(result, 25, weighted_rating=weighted_rating)


@pytest.mark.parametrize("weighted_rating", [True, False, -0.0001, 10.0001, math.nan, math.inf, -math.inf, "7.0", {}])
def test_invalid_weighted_rating_is_rejected(weighted_rating):
    with pytest.raises(ValueError):
        points(weighted_rating=weighted_rating)


def test_confirmed_league_winner_gets_75_once_despite_multiple_champion_clubs():
    result = points(league(team="First Club", place="1", competition_complete=True),
                    league(team="Second Club", place="1", competition_complete=True))
    assert_complete_award(result, 75)
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


@pytest.mark.parametrize(("outcome", "expected"), [("winner", 150), ("runner_up", 100)])
def test_cl_final_outcome_has_priority_over_conflicting_place_and_semifinal_history(outcome, expected):
    result = points(cl(final_outcome=outcome, place="2" if outcome == "winner" else "1",
                       stage_code="FF", stage_history=["VFR", "HFR", "FF"]))
    assert_complete_award(result, expected)


@pytest.mark.parametrize(("place", "expected"), [
    ("1", 150), ("2", 100), ("Полуфинал", 50), ("1/4 финала", 25), ("Четвертьфинал", 25),
])
def test_cl_legacy_place_fallback(place, expected):
    assert_complete_award(points(cl(place=place)), expected)


@pytest.mark.parametrize("stage", ["HF", "HFH", "HFR"])
def test_cl_semifinal_stage_codes_award_50(stage):
    assert_complete_award(points(cl(stage_code=stage)), 50)


@pytest.mark.parametrize("stage", ["VF", "VFH", "VFR"])
@pytest.mark.parametrize("field", ["stage_code", "stage_history"])
def test_cl_quarterfinal_stage_codes_award_25(stage, field):
    value = ["AFR", stage] if field == "stage_history" else stage
    assert_complete_award(points(cl(**{field: value})), 25)


def test_cl_explicit_quarterfinal_outcome_awards_25():
    assert_complete_award(points(cl(final_outcome="quarterfinal")), 25)


def test_cl_semifinal_takes_priority_over_quarterfinal_history():
    result = points(cl(place="Полуфинал", stage_code="HFR", stage_history=["VFH", "VFR", "HFH", "HFR"]))
    assert_complete_award(result, 50)


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
    assert_complete_award(result, 150)
    assert len(result["bonuses"]) == 1


@pytest.mark.parametrize("identity", ["EL", "ECL", "USC", "KLUB", "CLQ", "ACL", "CAFCL"])
def test_other_international_club_competitions_have_no_honour_bonus(identity):
    result = points(row(identity, "continental", name="UEFA Champions League" if identity == "CLQ" else identity,
                        final_outcome="winner", place="1", competition_complete=True))
    assert_complete_award(result, 0)


@pytest.mark.parametrize("cup_type", [8, 14])
def test_national_cup_and_league_cup_winner_award_25(cup_type):
    assert_complete_award(points(cup(cup_type=cup_type, final_outcome="winner")), 25)
    assert_complete_award(points(cup(cup_type=cup_type, place="1")), 25)


def test_multiple_cup_wins_from_multiple_clubs_and_cup_types_count_once():
    result = points(cup("DFB", cup_type=8, team="First Club", final_outcome="winner"),
                    cup("FAC", cup_type=8, team="Second Club", final_outcome="winner"),
                    cup("EFL", cup_type=14, team="Second Club", final_outcome="winner"))
    assert_complete_award(result, 25)
    assert len(result["bonuses"]) == 1


@pytest.mark.parametrize("cup_type", [9, 13, 15, 23])
def test_supercups_and_youth_cups_are_excluded_even_after_a_win(cup_type):
    assert_complete_award(points(cup(cup_type=cup_type, final_outcome="winner", place="1")), 0)


@pytest.mark.parametrize("outcome", ["runner_up", None])
def test_cup_nonwinner_does_not_receive_the_winners_25(outcome):
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
@pytest.mark.parametrize(("place", "expected"), [("1", 175), ("2", 50), ("Полуфинал", 25)])
def test_world_cup_outcomes(identity, place, expected):
    assert_complete_award(points(world_cup(identity, place=place)), expected)


@pytest.mark.parametrize("identity", ["EURO", "COPA", "CA16"])
@pytest.mark.parametrize(("place", "expected"), [("1", 150), ("2", 50), ("Полуфинал", 0)])
def test_euro_and_copa_outcomes(identity, place, expected):
    assert_complete_award(points(national_cup(identity, place=place)), expected)


@pytest.mark.parametrize(("factory", "expected"), [(world_cup, 25), (national_cup, 0)])
def test_third_place_match_preserves_only_the_world_cup_semifinal_bonus(factory, expected):
    entry = factory(place="3", stage_code="SPIEL3", stage_history=["HF", "SPIEL3"])
    assert_complete_award(points(entry), expected)


def test_euro_and_copa_awards_share_one_highest_bonus_kind():
    result = points(national_cup("EURO", final_outcome="winner"),
                    national_cup("COPA", final_outcome="runner_up"),
                    national_cup("CA16", stage_code="HF"))
    assert_complete_award(result, 150)
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
    ("winner", 150, False), ("runner_up", 100, True), ("quarterfinal", 25, True),
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
    assert_complete_award(result, 100)


def test_missing_weighted_rating_remains_the_primary_pending_status_while_honours_are_preserved():
    result = points(cl(final_outcome="runner_up"), league(place="1", competition_complete=None), weighted_rating=None)
    assert result["bonus_points"] == 100
    assert result["base_points"] is result["total"] is None
    assert result["status"] == "awaiting_weighted_rating"
    assert result["pending"]


def test_total_can_exceed_1000_and_all_distinct_bonus_kinds_reach_1575():
    result = points(league(place="1", competition_complete=True), cl(final_outcome="winner"),
                    cup(final_outcome="winner"), world_cup(final_outcome="winner"),
                    national_cup(final_outcome="winner"), weighted_rating=10)
    assert_complete_award(result, 575, weighted_rating=10)
    assert result["total"] == 1575
    assert len(result["bonuses"]) == 5


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
    assert_complete_award(first, 175, weighted_rating=7.123456)


@pytest.mark.parametrize(("factory", "minimum", "bonus"), [
    (league, 15, 75), (cl, 5, 150), (cup, 1, 25),
    (world_cup, 3, 175), (lambda **extra: world_cup("WM", **extra), 3, 175),
    (national_cup, 3, 150), (lambda **extra: national_cup("COPA", **extra), 3, 150),
    (lambda **extra: national_cup("CA16", **extra), 3, 150),
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
    assert_complete_award(result, 25)
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
    assert_complete_award(points(entry), 75)


@pytest.mark.parametrize("identity", [
    "MLS1", "POUS", "SA1", "BRA1", "PO1", "NL1", "GB2", "FR2", "ES2", "IT2", "L2", None, "",
])
@pytest.mark.parametrize("complete", [True, False, None])
def test_other_leagues_do_not_award_bonus_or_leave_pending_even_with_a_top_five_name(identity, complete):
    entry = row(identity, "domestic_league", name="Premier League", games=38,
                place="1", competition_complete=complete)
    assert_complete_award(points(entry), 0)


def test_ineligible_league_cannot_replace_the_result_of_an_eligible_top_five_league():
    eligible = league(games=15, place="2", competition_complete=True)
    other = row("SA1", "domestic_league", name="Saudi Pro League", games=38,
                place="1", competition_complete=True)
    assert_complete_award(points(eligible, other), 0)
