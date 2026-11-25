from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from season_ledger.domain import Season, apply_competition_scope
from season_ledger.store import Store
from season_ledger.web import create_app


def stored_snapshot():
    tm_matches = [{"id": "tm-1", "date": "2024-05-01", "team": "Arsenal", "opponent": "Fulham",
                   "competition_type_id": 1, "stage_code": None},
                  {"id": "tm-2", "date": "2024-05-08", "team": "Arsenal", "opponent": "Liverpool",
                   "competition_type_id": 1, "stage_code": None}]
    fm_matches = [{"id": "fm-1", "date": "2024-05-01", "team": "Arsenal", "opponent": "Fulham", "rating": 10},
                  {"id": "fm-2", "date": "2024-05-08", "team": "Arsenal", "opponent": "Liverpool", "rating": 8}]
    return apply_competition_scope({"name": "Fixture Player", "position": "GK", "birth_date": "1990-01-01",
        "competitions": [{"id": "GB1", "name": "Premier League", "category": "domestic_league",
            "competition_type_id": 1, "competition_complete": True, "place": "1", "team": "Arsenal",
            "games": 2, "goals": 0, "assists": 0, "minutes": 180,
            "rating": 9, "rated_games": 2, "matches": tm_matches, "fotmob_matches": fm_matches}]})


class NoFetch:
    async def load(self, *args, **kwargs):
        raise AssertionError("Saved match weights must be calculated without network imports")


@pytest.mark.parametrize(("games", "expected_bonus"), [(2, 0), (14, 0), (15, 75)])
def test_saved_match_weights_feed_final_points_and_preserve_original_sources(tmp_path, games, expected_bonus):
    path = tmp_path / "weighted.db"
    store = Store(path)
    store.add_season(Season(2023))
    snapshot = stored_snapshot()
    # Appearance totals determine eligibility; only two games have ratings.
    snapshot["competitions"][0]["games"] = games
    before = deepcopy(snapshot)
    tm_source, fm_source = {"source": "TM", "profile": {}}, {"source": "FM"}
    identity = store.save_player("2023-24", "1", snapshot, tm_source, fm_source)
    with TestClient(create_app(path, tm=NoFetch(), fm=NoFetch())) as client:
        first = client.get("/api/seasons/2023-24").json()["players"][0]
        second = client.get("/api/seasons/2023-24").json()["players"][0]
    expected_rating = (10 * 1 + (8 / .9253) * 1.5) / 2.5
    assert first == second
    assert first["weighted_rating"]["status"] == "complete"
    assert first["weighted_rating"]["rating"] == pytest.approx(expected_rating)
    assert first["weighted_rating"]["total_weight"] == 2.5
    assert [row["weight"] for row in first["weighted_rating"]["matches"]] == [1, 1.5]
    assert first["weighted_rating"]["cap_applied"] is True
    assert first["season_points"]["base_points"] == pytest.approx(expected_rating * 100)
    assert first["season_points"]["bonus_points"] == expected_bonus
    assert first["season_points"]["total"] == pytest.approx(expected_rating * 100 + expected_bonus)
    assert first["season_points"]["status"] == "complete"
    assert first["total"]["rating"] == 9
    with store.connection() as db:
        persisted = json.loads(db.execute("SELECT snapshot FROM players WHERE id=?", (identity,)).fetchone()[0])
    assert persisted == before
    assert snapshot == before
    assert store.raw(identity) == (tm_source, fm_source)


def test_legacy_summary_without_individual_ratings_waits_for_history_not_average(tmp_path):
    path = tmp_path / "legacy.db"
    store = Store(path)
    store.add_season(Season(2023))
    snapshot = stored_snapshot()
    snapshot["competitions"][0].pop("fotmob_matches")
    store.save_player("2023-24", "1", snapshot, {}, {})
    with TestClient(create_app(path, tm=NoFetch(), fm=NoFetch())) as client:
        player = client.get("/api/seasons/2023-24").json()["players"][0]
    assert player["total"]["rating"] == 9
    assert player["weighted_rating"]["status"] == "missing_history"
    assert player["season_points"]["status"] == "awaiting_match_history"
    assert player["season_points"]["base_points"] is None
    assert player["season_points"]["total"] is None
    assert player["season_points"]["bonus_points"] == 0
