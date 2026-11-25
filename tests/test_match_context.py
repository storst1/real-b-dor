from copy import deepcopy

import pytest

from season_ledger.match_context import canonical_stage, normalize_team, resolve_match_context


def tm_comp(identity="CL", stage="AFH", *, day="2024-03-05", opponent="Lazio", team="FC Bayern Munich", match_id="tm-1"):
    return {"id": identity, "name": "UEFA Champions League" if identity == "CL" else "UEFA Champions League Qualifying",
            "category": "continental", "competition_type_id": 10, "team": team, "team_id": "27",
            "matches": [{"id": match_id, "date": day + "T20:00:00+00:00", "team": team, "team_id": "27",
                         "opponent": opponent, "opponent_id": "398", "stage_code": stage}]}


def fm_match(*, day="2024-03-05", opponent="Lazio", team="Bayern München", stage=None):
    return {"id": "fm-1", "date": day, "team": team, "team_id": "9823", "opponent": opponent,
            "opponent_id": "8543", "competition_id": "42", "competition": "Champions League", "stage": stage}


@pytest.mark.parametrize(("source", "expected"), [
    ("AF", "round_of_16"), ("AFH", "round_of_16"), ("AFR", "round_of_16"),
    ("VFH", "quarterfinal"), ("HFR", "semifinal"), ("FFH", "final"), ("16F", "playoff"),
    ("1/8", "round_of_16"), ("1/4", "quarterfinal"), ("1/2", "semifinal"),
    ("Semi-final", "semifinal"), ("Quarter-final", "quarterfinal"), ("Round of 16", "round_of_16"),
    ("Final 2nd leg", "final"), ("knockout round play-offs", "playoff"),
    ("PD", "third_place"), ("PDH", "third_place"), ("PDR", "third_place"),
    ("Third Place Play-Off", "third_place"), ("3rd place match", "third_place"),
    ("A", "group"), ("Group C", "group"), ("Group Stage", "group"),
    ("Regular Season", "league"), ("League phase", "league"),
    ("19", None), ("Round 19", None), ("3rd Round", None), ("Qualification", None), ("Final Stages", None),
    (None, None), (19, None), ("", None),
])
def test_canonical_stages_do_not_interpret_matchday_numbers_as_knockouts(source, expected):
    assert canonical_stage(source) == expected


def test_team_aliases_preserve_youth_identity():
    assert normalize_team("FC Bayern München") == normalize_team("Bayern Munich")
    assert normalize_team("Bayer 04 Leverkusen") == normalize_team("Bayer Leverkusen")
    assert normalize_team("Real Madrid CF") == normalize_team("Real Madrid")
    assert normalize_team("Bayern Munich U19") != normalize_team("Bayern Munich")
    assert normalize_team("SS Lazio") == normalize_team("Lazio")
    assert normalize_team("FC København") == normalize_team("FC Copenhagen")
    assert normalize_team("1.FC Heidenheim 1846") == normalize_team("FC Heidenheim")
    assert normalize_team("Bosnia-Herzegovina") == normalize_team("Bosnia and Herzegovina")


def test_complete_fotmob_stage_is_used_without_comparing_with_transfermarkt():
    competition = tm_comp(stage="VFR")
    result = resolve_match_context(fm_match(stage="final"), [competition], parent=competition)
    assert result == {
        "competition_provider": "fotmob", "competition_id": "42", "competition": "Champions League",
        "category": "continental", "competition_kind": "champions_league",
        "competition_type_id": None, "stage_code": None, "stage": "final", "is_knockout": True,
        "opponent": "Lazio", "opponent_id": "8543", "is_national": False, "match_context_status": "matched",
    }


def test_global_actual_match_distinguishes_main_champions_league_from_qualifiers():
    main = tm_comp(day="2024-03-05", stage="A")
    qualifiers = tm_comp("CLQ", "POH", day="2023-08-22")
    result = resolve_match_context(fm_match(day="2023-08-22"), [main, qualifiers], parent=main)
    assert result["competition_id"] == "CLQ"
    assert result["stage"] == "playoff"
    assert result["match_context_status"] == "matched"


def test_opponent_resolves_same_day_collision_without_cross_source_id_matching():
    correct = tm_comp(opponent="Lazio", match_id="100")
    other = tm_comp("CLQ", opponent="Dortmund", match_id="fm-1")
    result = resolve_match_context(fm_match(), [other, correct], parent=other)
    assert result["competition_id"] == "CL"


def test_matching_date_collision_is_ambiguous_even_when_parent_is_supplied():
    first, second = tm_comp(), tm_comp("CLQ")
    result = resolve_match_context(fm_match(), [first, second], parent=first)
    assert result["match_context_status"] == "ambiguous"
    assert result["competition_id"] is None


def test_conflicting_opponent_blocks_parent_and_same_match_id_is_not_evidence():
    competition = tm_comp(opponent="Real Madrid", match_id="fm-1")
    result = resolve_match_context(fm_match(opponent="Dortmund"), [competition], parent=competition)
    assert result["match_context_status"] == "unmatched"
    assert result["competition_id"] is None
    result = resolve_match_context(fm_match(day="2024-04-01"), [competition], parent=competition)
    assert result["match_context_status"] == "unmatched"


def test_legacy_parent_without_match_dates_uses_only_fotmob_match_stage():
    parent = {"id": "CL", "name": "UEFA Champions League", "category": "continental",
              "competition_type_id": 10, "team": "FC Bayern Munich", "stage_code": "FF"}
    result = resolve_match_context(fm_match(stage="1/8"), [parent], parent=parent)
    assert result["competition_id"] == "42"
    assert result["competition_provider"] == "fotmob"
    assert result["stage"] == "round_of_16"
    assert result["stage_code"] is None
    assert result["match_context_status"] == "matched"


def test_complete_fotmob_stage_does_not_require_a_known_transfermarkt_stage():
    competition = tm_comp(stage=None)
    assert resolve_match_context(fm_match(stage="1/4"), [competition])["stage"] == "quarterfinal"
    competition["matches"][0]["stage_code"] = "UNKNOWN"
    result = resolve_match_context(fm_match(stage="final"), [competition])
    assert result["stage"] == "final"
    assert result["is_knockout"] is True
    assert result["competition_provider"] == "fotmob"


def test_complete_fotmob_context_keeps_its_provider_id_without_transfermarkt():
    result = resolve_match_context(fm_match(stage="final"), [])
    assert result["competition_id"] == "42"
    assert result["competition_provider"] == "fotmob"
    assert result["category"] == "continental"
    assert result["match_context_status"] == "matched"
    assert result["stage"] == "final"


def test_group_codes_and_undefined_league_stage_remain_distinct():
    competition = tm_comp(stage="C")
    result = resolve_match_context(fm_match(), [competition])
    assert result["stage"] == "group"
    assert result["is_knockout"] is False
    league = tm_comp(stage=None)
    league.update(id="L1", name="Bundesliga", category="domestic_league", competition_type_id=1)
    result = resolve_match_context(fm_match(), [league])
    assert result["stage"] is None
    assert result["is_knockout"] is None


def test_each_leg_resolves_independently_and_duplicate_snapshots_are_deduplicated():
    first = tm_comp(stage="AFH", day="2024-02-14", match_id="first")
    second = tm_comp(stage="AFR", day="2024-03-05", match_id="second")
    competition = {**first, "matches": first["matches"] + second["matches"]}
    for day, code in (("2024-02-14", "AFH"), ("2024-03-05", "AFR")):
        result = resolve_match_context(fm_match(day=day), [competition, deepcopy(competition)])
        assert result["stage_code"] == code
        assert result["stage"] == "round_of_16"
        assert result["match_context_status"] == "matched"


def test_utc_kickoff_date_and_national_context():
    competition = tm_comp(stage="HF", day="2024-07-10", team="England", opponent="Netherlands")
    competition.update(id="EURO", name="UEFA Euro", category="national_major", competition_type_id=11)
    match = fm_match(day="2024-07-11", team="England", opponent="Netherlands")
    match["kickoff"] = "2024-07-11T01:00:00+03:00"
    result = resolve_match_context(match, [competition])
    assert result["stage"] == "semifinal"
    assert result["is_national"] is True


def test_unique_parent_day_allows_unknown_team_alias_but_not_conflicting_opponent():
    competition = tm_comp(team="Unknown Official Club Name", opponent="Lazio")
    result = resolve_match_context(fm_match(team="Different Club Alias"), [competition], parent=competition)
    assert result["match_context_status"] == "matched"


def test_missing_fotmob_stage_uses_third_place_match_without_inheriting_semifinal_history():
    competition = tm_comp(stage="PD", team="Croatia", opponent="Morocco")
    competition.update(id="FIWC", name="World Cup", category="national_major", competition_type_id=11,
                       stage_history=["HF", "PD"])
    match = fm_match(team="Croatia", opponent="Morocco")
    match.update(competition_id="77", competition="World Cup")
    result = resolve_match_context(match, [competition], parent=competition)
    assert result["stage"] == "third_place"
    assert result["stage_code"] == "PD"
    assert result["is_knockout"] is True


def test_missing_fotmob_opponent_name_uses_tm_name_but_keeps_fotmob_id_namespace():
    competition = tm_comp(opponent="Real Madrid")
    match = fm_match(opponent=None)
    result = resolve_match_context(match, [competition], parent=competition)
    assert result["opponent"] == "Real Madrid"
    assert result["opponent_id"] == "8543"
    match["opponent_id"] = None
    result = resolve_match_context(match, [competition], parent=competition)
    assert result["opponent"] == "Real Madrid"
    assert result["opponent_id"] is None


@pytest.mark.parametrize("code", [f"{number}R{suffix}" for number in range(1, 8) for suffix in ("", "E", "H", "R")])
def test_verified_numbered_transfermarkt_cup_rounds_and_replays_are_nonfinal_playoffs(code):
    assert canonical_stage(code) == "playoff"


@pytest.mark.parametrize("stage", ["8R", "5RX", "5RRE", "Round 5", "5", "Fifth Round", "7VR"])
def test_unknown_round_suffixes_and_generic_matchdays_remain_unknown(stage):
    assert canonical_stage(stage) is None


def test_young_boys_alias_is_precise_and_preserves_age_team_identity():
    assert normalize_team("BSC Young Boys") == normalize_team("Young Boys")
    assert normalize_team("Young Boys U19") != normalize_team("Young Boys")
    assert normalize_team("BSC Young Boys U19") != normalize_team("Young Boys")
    assert normalize_team("BSC Other Club") != normalize_team("Other Club")


@pytest.mark.parametrize(("day", "tm_id", "fm_id"), [
    ("2023-10-25", "4171324", "4314976"),
    ("2023-11-07", "4171317", "4314979"),
])
def test_real_haaland_young_boys_matches_resolve_group_context(day, tm_id, fm_id):
    competition = tm_comp(stage="G", day=day, opponent="BSC Young Boys", team="Manchester City", match_id=tm_id)
    match = fm_match(day=day, opponent="Young Boys", team="Manchester City")
    match.update(id=fm_id, opponent_id="10192")
    result = resolve_match_context(match, [competition], parent=competition)
    assert result["match_context_status"] == "matched"
    assert result["competition_id"] == "CL"
    assert result["stage_code"] == "G"
    assert result["stage"] == "group"
    assert result["is_knockout"] is False
    assert result["opponent_id"] == "10192"


def test_real_haaland_luton_fa_cup_fifth_round_is_playoff_not_final():
    competition = tm_comp(stage="5R", day="2024-02-27", opponent="Luton Town", team="Manchester City", match_id="4285021")
    competition.update(id="FAC", name="FA Cup", category="domestic_cup", competition_type_id=8)
    match = fm_match(day="2024-02-27", opponent="Luton Town", team="Manchester City")
    match.update(id="4414627", opponent_id="8346", competition_id="132", competition="FA Cup")
    result = resolve_match_context(match, [competition], parent=competition)
    assert result["match_context_status"] == "matched"
    assert result["competition_id"] == "FAC"
    assert result["stage_code"] == "5R"
    assert result["stage"] == "playoff"
    assert result["is_knockout"] is True


@pytest.mark.parametrize(("day", "tm_id", "fm_id", "tm_opponent", "fm_opponent"), [
    ("2024-05-25", "4112903", "4205721", "Real Betis Balompié", "Real Betis"),
    ("2024-04-13", "4113018", "4205646", "RCD Mallorca", "Mallorca"),
    ("2024-01-03", "4112863", "4205531", "RCD Mallorca", "Mallorca"),
    ("2024-03-16", "4112987", "4205625", "CA Osasuna", "Osasuna"),
    ("2023-10-07", "4113062", "4205430", "CA Osasuna", "Osasuna"),
    ("2024-03-10", "4113003", "4205621", "Celta de Vigo", "Celta Vigo"),
    ("2023-08-25", "4113082", "4205364", "Celta de Vigo", "Celta Vigo"),
    ("2024-01-27", "4112836", "4205557", "UD Las Palmas", "Las Palmas"),
    ("2023-09-27", "4113073", "4205411", "UD Las Palmas", "Las Palmas"),
    ("2024-01-21", "4112828", "4205546", "UD Almería", "Almería"),
    ("2023-08-19", "4113127", "4205353", "UD Almería", "Almería"),
])
def test_real_vinicius_laliga_opponents_resolve_without_guessing_stage(day, tm_id, fm_id, tm_opponent, fm_opponent):
    competition = tm_comp(stage=None, day=day, opponent=tm_opponent, team="Real Madrid", match_id=tm_id)
    competition.update(id="ES1", name="LaLiga", category="domestic_league", competition_type_id=1)
    match = fm_match(day=day, opponent=fm_opponent, team="Real Madrid")
    match.update(id=fm_id, competition_id="87", competition="LaLiga")
    result = resolve_match_context(match, [competition], parent=competition)
    assert result["match_context_status"] == "matched"
    assert result["competition_id"] == "87"
    assert result["competition_provider"] == "fotmob"
    assert result["category"] == "domestic_league"
    assert result["stage"] is None
    assert result["opponent"] == fm_opponent


@pytest.mark.parametrize(("official", "short"), [
    ("Real Betis Balompié", "Real Betis"), ("RCD Mallorca", "Mallorca"),
    ("CA Osasuna", "Osasuna"), ("Celta de Vigo", "Celta Vigo"),
    ("UD Las Palmas", "Las Palmas"), ("UD Almería", "Almería"),
])
def test_spanish_aliases_are_exact_and_keep_youth_womens_and_reserve_suffixes(official, short):
    assert normalize_team(official) == normalize_team(short)
    for suffix in (" U19", " Women", " B"):
        assert normalize_team(official + suffix) != normalize_team(short)
        competition = tm_comp(opponent=official + suffix)
        assert resolve_match_context(fm_match(opponent=short), [competition], parent=competition)["match_context_status"] == "unmatched"


def test_unknown_spanish_prefixes_and_conflicting_opponents_are_not_collapsed():
    for name in ("CA Unknown Club", "UD Unknown Club", "RCD Unknown Club"):
        assert normalize_team(name) != normalize_team("Unknown Club")
    competition = tm_comp(opponent="CA Osasuna")
    result = resolve_match_context(fm_match(opponent="Mallorca"), [competition], parent=competition)
    assert result["match_context_status"] == "unmatched"
    assert result["competition_id"] is None


@pytest.mark.parametrize("stage", [None, "playoff", "Qualification", "Unknown phase"])
def test_incomplete_or_ambiguous_fotmob_cl_context_still_needs_transfermarkt(stage):
    result = resolve_match_context(fm_match(stage=stage), [])
    assert result["match_context_status"] == "unmatched"
    assert result.get("competition_provider") != "fotmob"


def test_unknown_champions_league_id_is_not_treated_as_an_unweighted_continental_cup():
    match = fm_match(stage="final")
    match.update(competition_id="99999", category="continental")
    result = resolve_match_context(match, [])
    assert result["match_context_status"] == "unmatched"


def test_unknown_national_tournament_does_not_silently_lose_world_cup_additions():
    match = fm_match(stage="final")
    match.update(competition_id="99999", competition="Unidentified major tournament", category="national_major")
    assert resolve_match_context(match, [])["match_context_status"] == "unmatched"


def test_supercup_does_not_require_an_unused_stage_to_be_known():
    match = fm_match(opponent="Barcelona", stage="Unidentified round")
    match.update(competition_id="139", competition="Supercopa de España")
    result = resolve_match_context(match, [])
    assert result["competition_provider"] == "fotmob"
    assert result["competition_kind"] == "supercup"
    assert result["stage"] is None


def test_unfamiliar_league_format_requires_explicit_stage_in_fotmob():
    match = fm_match(stage=None)
    match.update(competition_id="99999", competition="Unfamiliar league", category="domestic_league")
    assert resolve_match_context(match, [])["match_context_status"] == "unmatched"
    match["stage"] = "semifinal"
    result = resolve_match_context(match, [])
    assert result["competition_provider"] == "fotmob"
    assert result["stage"] == "semifinal"
