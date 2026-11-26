from copy import deepcopy

import pytest

from season_ledger.domain import Season
from season_ledger.match_context import normalize_team
from season_ledger.ratings import apply_position_adjustment
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def rodri_club_world_cup():
    """The legacy saved format predates category/is_national on FotMob matches."""
    return {
        "name": "Rodri", "position": "Defensive Midfield", "birth_date": "1996-06-22",
        "competitions": [{
            "id": "KLUB", "name": "FIFA Club World Cup", "category": "continental",
            "competition_type_id": 10, "team": "Manchester City", "games": 2,
            "rating": 7.8, "rated_games": 2, "place": "1",
            "matches": [
                {"id": "4243187", "date": "2023-12-19T18:00:00+00:00",
                 "team": "Manchester City", "opponent": "Urawa Red Diamonds", "stage_code": "HF"},
                {"id": "4244600", "date": "2023-12-22T18:00:00+00:00",
                 "team": "Manchester City", "opponent": "Fluminense Football Club", "stage_code": "FF"},
            ],
            "fotmob_matches": [
                {"id": "4320880", "date": "2023-12-19", "competition_id": "78",
                 "competition": "FIFA Club World Cup", "team": "Manchester City",
                 "opponent": "Urawa Red Diamonds", "stage": "1/2", "rating": 8.0},
                {"id": "4325851", "date": "2023-12-22", "competition_id": "78",
                 "competition": "FIFA Club World Cup", "team": "Manchester City",
                 "opponent": "Fluminense", "stage": "final", "rating": 7.6},
            ],
        }],
    }


def test_legacy_club_world_cup_ratings_use_fotmob_without_matching_transfermarkt():
    snapshot = rodri_club_world_cup()
    # Complete FotMob context must work even without a usable TM match history.
    snapshot["competitions"][0]["matches"] = []
    original = deepcopy(snapshot)
    result = calculate_weighted_rating(apply_position_adjustment(snapshot))

    assert snapshot == original
    assert result["status"] == "complete"
    assert result["rated_games"] == result["included_games"] == 2
    assert result["total_weight"] == 5
    assert result["raw_rating"] == pytest.approx((8.0 * 2 + 7.6 * 3) / 5)
    assert result["rating"] == pytest.approx((8.0 * 2 + 7.6 * 3) / 5 / 1.0021)
    assert [match["weight"] for match in result["matches"]] == [2, 3]
    for match in result["matches"]:
        context = match["context"]
        assert context["competition_provider"] == "fotmob"
        assert context["competition_id"] == "78"
        assert context["category"] == context["competition_kind"] == "continental"
        assert context["is_national"] is False
        assert {rule["key"] for rule in match["weight_rules"]}.isdisjoint({
            "world_cup", "major_final", "grand_national_opponent",
            "champions_league", "champions_league_knockout",
        })


def test_missing_fotmob_stage_uses_dated_tm_round_despite_fluminense_name_variant():
    snapshot = rodri_club_world_cup()
    for match in snapshot["competitions"][0]["fotmob_matches"]:
        match.pop("stage")

    result = calculate_weighted_rating(snapshot)

    assert result["status"] == "complete"
    assert [match["weight"] for match in result["matches"]] == [2, 3]
    assert [match["context"]["stage_code"] for match in result["matches"]] == ["HF", "FF"]
    assert result["matches"][1]["context"]["competition_id"] == "KLUB"
    assert result["matches"][1]["context"]["opponent"] == "Fluminense"


@pytest.mark.parametrize("name", ["FIFA Club World Cup", "Club World Cup"])
def test_club_world_cup_name_resolves_legacy_matches_without_a_known_id(name):
    snapshot = rodri_club_world_cup()
    competition = snapshot["competitions"][0]
    competition["matches"] = []
    for match in competition["fotmob_matches"]:
        match.pop("competition_id")
        match["competition"] = name

    result = calculate_weighted_rating(snapshot)

    assert result["status"] == "complete"
    assert result["total_weight"] == 5
    assert all(match["context"]["competition_kind"] == "continental" for match in result["matches"])
    assert all(match["context"]["is_national"] is False for match in result["matches"])


def test_club_world_cup_without_either_source_stage_remains_incomplete():
    snapshot = rodri_club_world_cup()
    final = snapshot["competitions"][0]
    final["fotmob_matches"][1].pop("stage")
    final["matches"][1].pop("stage_code")

    result = calculate_weighted_rating(snapshot)

    assert result["status"] == "incomplete_context"
    assert result["rating"] is result["raw_rating"] is None
    assert result["unresolved_match_ids"] == ["4325851"]
    assert result["matches"][1]["weight"] is None


def test_saved_rodri_snapshot_recalculates_on_repeated_store_reads_without_reimport(tmp_path):
    path = tmp_path / "seasons.sqlite3"
    store = Store(path)
    store.add_season(Season(2023))
    snapshot = rodri_club_world_cup()
    identity = store.save_player("2023-24", "357565", snapshot, {"saved": "tm"}, {"saved": "fm"})

    first = store.player(identity)
    repeated = store.player(identity)
    reopened = Store(path).player(identity)

    assert first["weighted_rating"]["status"] == "complete"
    assert first["weighted_rating"]["rating"] == pytest.approx(7.76 / 1.0021)
    assert repeated["weighted_rating"] == reopened["weighted_rating"] == first["weighted_rating"]
    assert repeated["updated_at"] == reopened["updated_at"] == first["updated_at"]
    assert reopened["season_points"]["status"] == "complete"
    assert reopened["season_points"]["bonus_points"] == -37
    assert {bonus["kind"]: bonus["points"] for bonus in reopened["season_points"]["bonuses"]} == {
        "no_champions_league": -25, "no_national_team": -12,
    }
    assert reopened["season_points"]["total"] == pytest.approx(7.76 / 1.0021 * 100 - 37)
    assert store.raw(identity) == ({"saved": "tm"}, {"saved": "fm"})


@pytest.mark.parametrize("suffix", [" Women", " U19", " B"])
def test_fluminense_official_name_alias_preserves_distinct_teams(suffix):
    assert normalize_team("Fluminense Football Club") == normalize_team("Fluminense")
    assert normalize_team("Fluminense Football Club" + suffix) != normalize_team("Fluminense")
