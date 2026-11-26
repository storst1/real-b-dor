from copy import deepcopy

import pytest

from season_ledger.domain import Season
from season_ledger.match_context import normalize_team, resolve_match_context
from season_ledger.ratings import apply_position_adjustment
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def wirtz_snapshot():
    """Keep the two affected matches and Wirtz's confirmed season honours."""
    return {
        "name": "Florian Wirtz", "position": "AM", "birth_date": "2003-05-03",
        "competitions": [
            {
                "id": "EL", "name": "UEFA Europa League", "category": "continental",
                "team": "Bayer 04 Leverkusen", "competition_type_id": 10,
                "games": 1, "rating": 8.7, "rated_games": 1,
                "place": "2", "final_outcome": "runner_up",
                "matches": [{"id": "4172147", "date": "2023-09-21T16:45:00+00:00",
                             "team": "Bayer 04 Leverkusen", "opponent": "BK Häcken", "stage_code": "H"}],
                "fotmob_matches": [{
                    "id": "4315153", "date": "2023-09-21", "team": "Bayer Leverkusen",
                    "opponent": "Häcken", "competition_id": "73", "competition": "Europa League",
                    "category": "continental", "is_national": False, "stage": None, "rating": 8.7,
                }],
            },
            {
                "id": "DFB", "name": "DFB-Pokal", "category": "domestic_cup",
                "team": "Bayer 04 Leverkusen", "competition_type_id": 8,
                "games": 1, "rating": 7.9, "rated_games": 1,
                "place": "1", "final_outcome": "winner",
                "matches": [{"id": "4088610", "date": "2023-08-12T13:30:00+00:00",
                             "team": "Bayer 04 Leverkusen", "opponent": "FC Teutonia 05 Ottensen", "stage_code": "1R"}],
                "fotmob_matches": [{
                    "id": "4197687", "date": "2023-08-12", "team": "Bayer Leverkusen",
                    "opponent": "Teutonia Ottensen", "competition_id": "209", "competition": "DFB Pokal",
                    "category": "domestic_cup", "is_national": False, "stage": None, "rating": 7.9,
                }],
            },
            {
                "id": "L1", "name": "Bundesliga", "category": "domestic_league",
                "team": "Bayer 04 Leverkusen", "competition_type_id": 1,
                "games": 32, "place": "1", "competition_complete": True,
            },
        ],
    }


@pytest.mark.parametrize("legacy", [False, True])
def test_real_wirtz_aliases_recover_group_and_first_round_without_champions_league_weights(legacy):
    snapshot = wirtz_snapshot()
    if legacy:
        for row in snapshot["competitions"][:2]:
            row["fotmob_matches"][0].pop("category")
            row["fotmob_matches"][0].pop("is_national")
    original = deepcopy(snapshot)

    result = calculate_weighted_rating(apply_position_adjustment(snapshot))

    assert snapshot == original
    assert result["status"] == "complete"
    assert result["rated_games"] == result["included_games"] == 2
    assert result["total_weight"] == 2
    assert result["raw_rating"] == pytest.approx(8.3)
    assert result["rating"] == pytest.approx(8.3 / 1.0158)
    assert [match["weight"] for match in result["matches"]] == [1, 1]
    assert [match["context"]["stage_code"] for match in result["matches"]] == ["H", "1R"]
    assert [match["context"]["stage"] for match in result["matches"]] == ["group", "playoff"]
    assert all([rule["key"] for rule in match["weight_rules"]] == ["base"] for match in result["matches"])


def test_wirtz_aliases_cannot_replace_missing_source_rounds():
    snapshot = wirtz_snapshot()
    for row in snapshot["competitions"][:2]:
        row["matches"][0].pop("stage_code")

    result = calculate_weighted_rating(snapshot)

    assert result["status"] == "incomplete_context"
    assert result["rating"] is result["raw_rating"] is None
    assert result["unresolved_match_ids"] == ["4315153", "4197687"]
    assert all(match["weight"] is None for match in result["matches"])


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_wirtz_opponent_aliases_do_not_merge_distinct_teams(index, suffix):
    row = wirtz_snapshot()["competitions"][index]
    tm_match, fm_match = row["matches"][0], row["fotmob_matches"][0]
    assert normalize_team(tm_match["opponent"]) == normalize_team(fm_match["opponent"])
    tm_match["opponent"] += suffix

    context = resolve_match_context(fm_match, [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None
    assert normalize_team(tm_match["opponent"]) != normalize_team(fm_match["opponent"])


def test_saved_wirtz_recalculates_league_and_cup_bonuses_with_absence_penalties_without_reimport(tmp_path):
    path = tmp_path / "seasons.sqlite3"
    store = Store(path)
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "598577", wirtz_snapshot(), {"saved": "tm"}, {"saved": "fm"})

    player = store.player(identity)
    repeated = store.player(identity)
    reopened = Store(path).player(identity)

    assert player["weighted_rating"]["status"] == "complete"
    assert repeated["weighted_rating"] == reopened["weighted_rating"] == player["weighted_rating"]
    assert repeated["updated_at"] == reopened["updated_at"] == player["updated_at"]
    points = player["season_points"]
    assert points["status"] == "complete"
    assert {bonus["kind"]: bonus["points"] for bonus in points["bonuses"]} == {
        "league": 37, "cup": 12, "no_champions_league": -25, "no_national_team": -12,
    }
    assert points["bonus_points"] == 12
    assert points["total"] == pytest.approx(8.3 / 1.0158 * 100 + 12)
    assert repeated["season_points"] == reopened["season_points"] == points
    assert store.raw(identity) == ({"saved": "tm"}, {"saved": "fm"})
