from copy import deepcopy

import pytest

from season_ledger.domain import Season
from season_ledger.match_context import canonical_stage, normalize_team, resolve_match_context
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def mbappe_snapshot():
    """The three affected games and the confirmed honours of the saved season."""
    return {
        "name": "Kylian Mbappé", "position": "CF", "birth_date": "1998-12-20",
        "competitions": [
            {
                "id": "CL", "name": "UEFA Champions League", "category": "continental",
                "team": "Paris Saint-Germain", "competition_type_id": 10,
                "games": 2, "rating": 7.75, "rated_games": 2,
                "place": "Полуфинал", "stage_code": "HFR",
                "matches": [
                    {"id": "4171290", "date": "2023-11-07T20:00:00+00:00",
                     "team": "Paris Saint-Germain", "opponent": "AC Milan", "stage_code": "F"},
                    {"id": "4171285", "date": "2023-10-25T19:00:00+00:00",
                     "team": "Paris Saint-Germain", "opponent": "AC Milan", "stage_code": "F"},
                ],
                "fotmob_matches": [
                    {"id": "4315050", "date": "2023-11-07", "team": "Paris Saint-Germain",
                     "opponent": "Milan", "competition_id": "42", "competition": "Champions League",
                     "category": "continental", "is_national": False, "stage": None, "rating": 7.0},
                    {"id": "4315049", "date": "2023-10-25", "team": "Paris Saint-Germain",
                     "opponent": "Milan", "competition_id": "42", "competition": "Champions League",
                     "category": "continental", "is_national": False, "stage": None, "rating": 8.5},
                ],
            },
            {
                "id": "FRC", "name": "Coupe de France", "category": "domestic_cup",
                "team": "Paris Saint-Germain", "competition_type_id": 8,
                "games": 1, "rating": 9.8, "rated_games": 1,
                "place": "1", "final_outcome": "winner",
                "matches": [{"id": "4240680", "date": "2024-01-07T19:45:00+00:00",
                             "team": "Paris Saint-Germain", "opponent": "US Revel", "stage_code": "5R"}],
                "fotmob_matches": [{
                    "id": "4378660", "date": "2024-01-07", "team": "Paris Saint-Germain",
                    "opponent": "Revel", "competition_id": "134", "competition": "Coupe de France",
                    "category": "domestic_cup", "is_national": False, "stage": "1/32", "rating": 9.8,
                }],
            },
            {
                "id": "FR1", "name": "Ligue 1", "category": "domestic_league",
                "team": "Paris Saint-Germain", "competition_type_id": 1,
                "games": 29, "place": "1", "competition_complete": True,
            },
            {
                "id": "EURO", "name": "UEFA Euro", "category": "national_major",
                "team": "France", "competition_type_id": 11,
                "games": 5, "place": "Полуфинал", "stage_code": "HF",
            },
        ],
    }


@pytest.mark.parametrize("legacy", [False, True])
def test_real_mbappe_milan_games_recover_group_and_keep_both_cl_and_grand_opponent_additions(legacy):
    snapshot = mbappe_snapshot()
    if legacy:
        for row in snapshot["competitions"][:2]:
            for match in row["fotmob_matches"]:
                match.pop("category")
                match.pop("is_national")
    original = deepcopy(snapshot)

    result = calculate_weighted_rating(snapshot)

    assert snapshot == original
    assert result["status"] == "complete"
    assert result["rated_games"] == result["included_games"] == 3
    assert result["total_weight"] == 5
    assert result["rating"] == result["raw_rating"] == pytest.approx((7 * 2 + 8.5 * 2 + 9.8) / 5)
    assert [match["weight"] for match in result["matches"]] == [2, 2, 1]
    for match in result["matches"][:2]:
        assert match["context"]["competition_id"] == "CL"
        assert match["context"]["stage_code"] == "F"
        assert match["context"]["stage"] == "group"
        assert {rule["key"] for rule in match["weight_rules"]} == {
            "base", "champions_league", "grand_club_opponent",
        }


@pytest.mark.parametrize("stage", ["1/32", "Round of 64", "1/32 final", "1/32 finals"])
def test_explicit_round_of_64_cup_stage_uses_fotmob_without_tm_comparison(stage):
    row = mbappe_snapshot()["competitions"][1]
    row["matches"] = []
    row["fotmob_matches"][0]["stage"] = stage

    result = calculate_weighted_rating({"position": "CF", "competitions": [row]})

    assert canonical_stage(stage) == "playoff"
    assert result["status"] == "complete"
    assert result["total_weight"] == 1
    match = result["matches"][0]
    assert match["context"]["competition_provider"] == "fotmob"
    assert match["context"]["competition_id"] == "134"
    assert match["context"]["stage"] == "playoff"
    assert [rule["key"] for rule in match["weight_rules"]] == ["base"]


def test_revel_without_fotmob_stage_uses_dated_tm_round_and_exact_alias():
    row = mbappe_snapshot()["competitions"][1]
    row["fotmob_matches"][0]["stage"] = None

    result = calculate_weighted_rating({"position": "CF", "competitions": [row]})

    assert result["status"] == "complete"
    assert result["total_weight"] == 1
    assert result["matches"][0]["context"]["competition_id"] == "FRC"
    assert result["matches"][0]["context"]["stage_code"] == "5R"
    assert result["matches"][0]["context"]["stage"] == "playoff"


def test_unknown_source_stages_stay_incomplete_despite_known_clubs():
    snapshot = mbappe_snapshot()
    for row in snapshot["competitions"][:2]:
        for match in row["fotmob_matches"]:
            match["stage"] = "Unknown phase"
        for match in row["matches"]:
            match["stage_code"] = "UNKNOWN"

    result = calculate_weighted_rating(snapshot)

    assert result["status"] == "incomplete_context"
    assert result["rating"] is result["raw_rating"] is None
    assert result["unresolved_match_ids"] == ["4315050", "4315049", "4378660"]


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_milan_and_revel_aliases_preserve_distinct_team_identity(index, suffix):
    row = mbappe_snapshot()["competitions"][index]
    tm_match, fm_match = row["matches"][0], row["fotmob_matches"][0]
    fm_match["stage"] = None
    assert normalize_team(tm_match["opponent"]) == normalize_team(fm_match["opponent"])
    tm_match["opponent"] += suffix

    context = resolve_match_context(fm_match, [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None
    assert normalize_team(tm_match["opponent"]) != normalize_team(fm_match["opponent"])


def test_conflicting_milan_opponent_is_not_accepted_just_because_the_date_matches():
    row = mbappe_snapshot()["competitions"][0]
    row["matches"][0]["opponent"] = "Roma"

    context = resolve_match_context(row["fotmob_matches"][0], [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


def test_saved_mbappe_recalculates_without_reimport_and_applies_bonus_participation_thresholds(tmp_path):
    path = tmp_path / "seasons.sqlite3"
    store = Store(path)
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "342229", mbappe_snapshot(), {"saved": "tm"}, {"saved": "fm"})

    player = store.player(identity)
    repeated = store.player(identity)
    reopened = Store(path).player(identity)

    assert player["weighted_rating"]["status"] == "complete"
    assert repeated["weighted_rating"] == reopened["weighted_rating"] == player["weighted_rating"]
    assert repeated["updated_at"] == reopened["updated_at"] == player["updated_at"]
    points = player["season_points"]
    assert points["status"] == "complete"
    assert {bonus["kind"]: bonus["points"] for bonus in points["bonuses"]} == {
        "league": 37, "cup": 12,
    }
    assert points["bonus_points"] == 49
    assert points["total"] == pytest.approx((7 * 2 + 8.5 * 2 + 9.8) / 5 * 100 + 49)
    assert repeated["season_points"] == reopened["season_points"] == points
    assert store.raw(identity) == ({"saved": "tm"}, {"saved": "fm"})
