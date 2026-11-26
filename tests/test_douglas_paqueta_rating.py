from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.match_context import normalize_team, resolve_match_context
from season_ledger.ratings import apply_position_adjustment
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def affected_sources(player):
    if player == "douglas":
        profile = {"id": "447661", "name": "Douglas Luiz", "position": "CM"}
        identity, competition, fm_identity, fm_competition = "UCOL", "UEFA Conference League", "10216", "Conference League"
        team, official, short, stage = "Aston Villa", "HSK Zrinjski Mostar", "Zrinjski Mostar", "E"
        games = [("4172288", "4315318", "2023-12-14", 6.7), ("4172283", "4315302", "2023-10-05", 7.5)]
    else:
        profile = {"id": "444523", "name": "Lucas Paquetá", "position": "AM"}
        identity, competition, fm_identity, fm_competition = "EL", "UEFA Europa League", "73", "Europa League"
        team, official, short, stage = "West Ham United", "Olympiacos Piraeus", "Olympiacos", "A"
        games = [("4172061", "4315076", "2023-11-09", 8.3), ("4172064", "4315073", "2023-10-26", 7.3)]
    tm = {"profile": profile, "competitions": [{
        "id": identity, "name": competition, "category": "continental", "competition_type_id": 10,
        "team": team, "games": 2,
        "matches": [{"id": tm_id, "date": day, "team": team, "opponent": official, "stage_code": stage}
                    for tm_id, _, day, _ in games],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": fm_identity, "name": fm_competition, "category": "continental", "team": team,
        "games": 2, "rated_games": 2, "rating": sum(game[3] for game in games) / 2,
        "matches": [{"id": fm_id, "date": day, "team": team, "opponent": short,
                     "competition_id": fm_identity, "competition": fm_competition, "category": "continental",
                     "is_national": False, "stage": None, "rating": rating}
                    for _, fm_id, day, rating in games],
    }]}
    return tm, fm


@pytest.mark.parametrize("player,raw,coefficient,stage", [
    ("douglas", 7.1, 1.0021, "E"), ("paqueta", 7.8, 1.0158, "A"),
])
@pytest.mark.parametrize("legacy", [False, True])
def test_real_zrinjski_and_olympiacos_group_matches_receive_only_base_weight(player, raw, coefficient, stage, legacy):
    tm, fm = affected_sources(player)
    if legacy:
        for match in fm["competitions"][0]["matches"]:
            match.pop("category")
            match.pop("is_national")
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(apply_position_adjustment(snapshot))

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == weighted["history_games"] == 2
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == 2
    assert weighted["raw_rating"] == pytest.approx(raw)
    assert weighted["rating"] == pytest.approx(raw / coefficient)
    assert {match["id"] for match in weighted["matches"]} == {match["id"] for match in fm["competitions"][0]["matches"]}
    for match in weighted["matches"]:
        assert match["context"]["stage_code"] == stage
        assert match["context"]["stage"] == "group"
        assert match["context"]["is_knockout"] is False
        assert match["weight"] == 1
        assert [rule["key"] for rule in match["weight_rules"]] == ["base"]


@pytest.mark.parametrize("player", ["douglas", "paqueta"])
@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_verified_opponent_aliases_keep_youth_womens_and_reserve_teams_distinct(player, suffix):
    tm, fm = affected_sources(player)
    row = tm["competitions"][0]
    match = fm["competitions"][0]["matches"][0]
    assert normalize_team(row["matches"][0]["opponent"]) == normalize_team(match["opponent"])
    row["matches"][0]["opponent"] += suffix

    context = resolve_match_context(match, [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


@pytest.mark.parametrize("player", ["douglas", "paqueta"])
@pytest.mark.parametrize("stage", [None, "Unknown phase"])
def test_opponent_alias_does_not_guess_a_missing_or_unknown_round(player, stage):
    tm, fm = affected_sources(player)
    for match in tm["competitions"][0]["matches"]:
        match["stage_code"] = stage

    weighted = calculate_weighted_rating(apply_position_adjustment(combine(tm, fm)))

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is weighted["raw_rating"] is None
    assert weighted["included_games"] == 0
    assert len(weighted["unresolved_match_ids"]) == 2


@pytest.mark.parametrize("player", ["douglas", "paqueta"])
def test_conflicting_opponent_still_blocks_an_otherwise_matching_parent(player):
    tm, fm = affected_sources(player)
    row = tm["competitions"][0]
    row["matches"][0]["opponent"] = "Different Club"

    context = resolve_match_context(fm["competitions"][0]["matches"][0], [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


@pytest.mark.parametrize("player,raw,coefficient", [
    ("douglas", 7.1, 1.0021), ("paqueta", 7.8, 1.0158),
])
def test_saved_douglas_and_paqueta_recalculate_without_import_or_champions_league_bonuses(tmp_path, player, raw, coefficient):
    tm, fm = affected_sources(player)
    tm["competitions"][0].update(games=12 if player == "douglas" else 9, stage_code="HFR" if player == "douglas" else "VFR")
    fm["competitions"][0]["games"] = tm["competitions"][0]["games"]
    original = deepcopy((tm, fm))
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", tm["profile"]["id"], combine(tm, fm), tm, fm)

    first = store.player(identity)
    repeated = store.player(identity)
    reopened = Store(store.path).player(identity)

    assert first["weighted_rating"]["status"] == "complete"
    assert repeated["weighted_rating"] == reopened["weighted_rating"] == first["weighted_rating"]
    assert repeated["updated_at"] == reopened["updated_at"] == first["updated_at"]
    assert first["season_points"]["status"] == "complete"
    assert first["season_points"]["bonus_points"] == -37
    assert {bonus["kind"]: bonus["points"] for bonus in first["season_points"]["bonuses"]} == {
        "no_champions_league": -25, "no_national_team": -12,
    }
    assert first["season_points"]["total"] == pytest.approx(raw / coefficient * 100 - 37)
    assert store.raw(identity) == original


def test_douglas_unrated_conference_qualifiers_remain_visible_without_affecting_the_mean():
    tm, fm = affected_sources("douglas")
    qualifiers = [("4162619", "4287944", "2023-08-23", "QRH"),
                  ("4162643", "4287966", "2023-08-31", "QRR")]
    tm["competitions"].append({
        "id": "ECLQ", "name": "UEFA Conference League Qualifying", "category": "continental", "competition_type_id": 10,
        "team": "Aston Villa", "games": 2,
        "matches": [{"id": tm_id, "date": day, "team": "Aston Villa", "opponent": "Hibernian FC", "stage_code": stage}
                    for tm_id, _, day, stage in qualifiers],
    })
    fm["competitions"].append({
        "id": "10615", "name": "Conference League Qualification", "category": "continental", "team": "Aston Villa",
        "games": 2, "rating": None, "rated_games": 0,
        "matches": [{"id": fm_id, "date": day, "team": "Aston Villa", "opponent": "Hibernian",
                     "competition_id": "10615", "competition": "Conference League Qualification", "category": "continental",
                     "stage": "final", "rating": None} for _, fm_id, day, _ in qualifiers],
    })
    original = deepcopy((tm, fm))

    weighted = calculate_weighted_rating(apply_position_adjustment(combine(tm, fm)))

    assert (tm, fm) == original
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 2
    assert weighted["unrated_games"] == 2
    assert weighted["history_games"] == 4
    assert weighted["total_weight"] == 2
    assert weighted["rating"] == pytest.approx(7.1 / 1.0021)
    assert {match["id"] for match in weighted["matches"]} == {"4315318", "4315302", "4287944", "4287966"}
    assert all(not match["included"] for match in weighted["matches"] if match["id"] in {"4287944", "4287966"})
