from copy import deepcopy

import pytest

from season_ledger.domain import Season
from season_ledger.match_context import normalize_team, resolve_match_context
from season_ledger.ratings import apply_position_adjustment
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def affected_snapshot(player):
    if player == "saka":
        return {
            "name": "Bukayo Saka", "position": "RW", "birth_date": "2001-09-05",
            "competitions": [{
                "id": "CL", "name": "UEFA Champions League", "category": "continental",
                "competition_type_id": 10, "team": "Arsenal FC", "games": 2,
                "rating": 7.95, "rated_games": 2, "stage_code": "VFR",
                "matches": [
                    {"id": "4171233", "date": "2023-11-29T20:00:00+00:00",
                     "team": "Arsenal FC", "opponent": "RC Lens", "stage_code": "B"},
                    {"id": "4171238", "date": "2023-10-03T19:00:00+00:00",
                     "team": "Arsenal FC", "opponent": "RC Lens", "stage_code": "B"},
                ],
                "fotmob_matches": [
                    {"id": "4315028", "date": "2023-11-29", "team": "Arsenal", "opponent": "Lens",
                     "competition_id": "42", "competition": "Champions League", "category": "continental",
                     "is_national": False, "stage": None, "rating": 8.6},
                    {"id": "4315022", "date": "2023-10-03", "team": "Arsenal", "opponent": "Lens",
                     "competition_id": "42", "competition": "Champions League", "category": "continental",
                     "is_national": False, "stage": None, "rating": 7.3},
                ],
            }],
        }
    return {
        "name": "Jamal Musiala", "position": "AM", "birth_date": "2003-02-26",
        "competitions": [{
            "id": "DFB", "name": "DFB-Pokal", "category": "domestic_cup",
            "competition_type_id": 8, "team": "FC Bayern Munich", "games": 1,
            "rating": 6.6, "rated_games": 1, "stage_code": "2R",
            "matches": [{"id": "4194144", "date": "2023-11-01T19:45:00+00:00",
                         "team": "FC Bayern Munich", "opponent": "1.FC Saarbrücken", "stage_code": "2R"}],
            "fotmob_matches": [{
                "id": "4340450", "date": "2023-11-01", "team": "Bayern München", "opponent": "Saarbrücken",
                "competition_id": "209", "competition": "DFB Pokal", "category": "domestic_cup",
                "is_national": False, "stage": None, "rating": 6.6,
            }],
        }],
    }


@pytest.mark.parametrize("player,code,stage,weight,raw,coefficient", [
    ("saka", "B", "group", 1.5, 7.95, 1.0096),
    ("musiala", "2R", "playoff", 1, 6.6, 1.0158),
])
@pytest.mark.parametrize("legacy", [False, True])
def test_real_lens_and_saarbrucken_rounds_recover_with_correct_weights(player, code, stage, weight, raw, coefficient, legacy):
    snapshot = affected_snapshot(player)
    if legacy:
        for match in snapshot["competitions"][0]["fotmob_matches"]:
            match.pop("category")
            match.pop("is_national")
    original = deepcopy(snapshot)

    result = calculate_weighted_rating(apply_position_adjustment(snapshot))

    assert snapshot == original
    count = len(snapshot["competitions"][0]["fotmob_matches"])
    assert result["status"] == "complete"
    assert result["rated_games"] == result["included_games"] == count
    assert result["total_weight"] == weight * count
    assert result["raw_rating"] == pytest.approx(raw)
    assert result["rating"] == pytest.approx(raw / coefficient)
    for match in result["matches"]:
        assert match["weight"] == weight
        assert match["context"]["stage_code"] == code
        assert match["context"]["stage"] == stage
        assert match["context"]["is_knockout"] is (player == "musiala")
        rules = {rule["key"] for rule in match["weight_rules"]}
        assert rules == ({"base", "champions_league"} if player == "saka" else {"base"})


@pytest.mark.parametrize("player", ["saka", "musiala"])
@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_opponent_aliases_preserve_distinct_lens_and_saarbrucken_teams(player, suffix):
    row = affected_snapshot(player)["competitions"][0]
    match = row["fotmob_matches"][0]
    assert normalize_team(row["matches"][0]["opponent"]) == normalize_team(match["opponent"])
    row["matches"][0]["opponent"] += suffix

    context = resolve_match_context(match, [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


@pytest.mark.parametrize("player", ["saka", "musiala"])
def test_conflicting_opponent_cannot_use_the_parent_round(player):
    row = affected_snapshot(player)["competitions"][0]
    row["fotmob_matches"][0]["opponent"] = "Different Club"

    context = resolve_match_context(row["fotmob_matches"][0], [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


@pytest.mark.parametrize("player", ["saka", "musiala"])
@pytest.mark.parametrize("unknown_stage", [None, "Unknown phase"])
def test_aliases_cannot_guess_missing_or_unknown_match_rounds(player, unknown_stage):
    snapshot = affected_snapshot(player)
    for match in snapshot["competitions"][0]["matches"]:
        match["stage_code"] = unknown_stage

    result = calculate_weighted_rating(snapshot)

    assert result["status"] == "incomplete_context"
    assert result["rating"] is result["raw_rating"] is None
    assert all(match["weight"] is None for match in result["matches"])


@pytest.mark.parametrize("player,tm_id,expected_bonuses", [
    ("saka", "433177", {"euro_copa": 25}),
    ("musiala", "580195", {"champions_league": 25, "no_national_team": -12}),
])
def test_saved_saka_and_musiala_recalculate_without_reimport_and_keep_eligible_bonuses(tmp_path, player, tm_id, expected_bonuses):
    snapshot = affected_snapshot(player)
    if player == "saka":
        snapshot["competitions"].extend([
            {"id": "EURO", "name": "UEFA Euro", "category": "national_major", "games": 7,
             "team": "England", "place": "2", "final_outcome": "runner_up"},
            {"id": "GBCS", "name": "Community Shield", "category": "domestic_cup", "games": 1,
             "competition_type_id": 9, "team": "Arsenal FC", "place": "1", "final_outcome": "winner"},
        ])
    else:
        snapshot["competitions"].append({
            "id": "CL", "name": "UEFA Champions League", "category": "continental",
            "games": 11, "team": "FC Bayern Munich", "stage_code": "HFR",
        })
    path = tmp_path / "seasons.sqlite3"
    store = Store(path)
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", tm_id, snapshot, {"saved": "tm"}, {"saved": "fm"})

    first = store.player(identity)
    reopened = Store(path).player(identity)

    assert first["weighted_rating"]["status"] == "complete"
    assert reopened["weighted_rating"] == first["weighted_rating"]
    assert reopened["updated_at"] == first["updated_at"]
    assert reopened["season_points"] == first["season_points"]
    points = first["season_points"]
    assert points["status"] == "complete"
    assert {bonus["kind"]: bonus["points"] for bonus in points["bonuses"]} == expected_bonuses
    assert points["bonus_points"] == sum(expected_bonuses.values())
    assert points["total"] == pytest.approx(first["weighted_rating"]["rating"] * 100 + sum(expected_bonuses.values()))
    assert store.raw(identity) == ({"saved": "tm"}, {"saved": "fm"})
