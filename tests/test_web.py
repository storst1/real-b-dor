import copy
import json
import time

import pytest

from fastapi.testclient import TestClient

from season_ledger.domain import Season, combine
from season_ledger.importer import source_error
from season_ledger.store import Store
from season_ledger.web import create_app
from transfermarkt_api.exceptions import NotFound, UpstreamBlocked, UpstreamTimeout


TM_DATA = {"profile": {"id": "132098", "name": "Harry Kane", "position": "CF", "birth_date": "1993-07-28", "nationalities": ["England"], "clubs": [{"name": "Bayern Munich"}]},
           "competitions": [{"id": "L1", "name": "Bundesliga", "category": "domestic_league", "team": "Bayern Munich", "games": 32, "goals": 36, "assists": 8, "minutes": 2800, "place": "3"}],
           "market_value": {"amount": 100000000, "currency": "EUR", "observed_on": "2024-05-29", "as_of": "2024-06-30"}}
FM_DATA = {"status": "matched", "player_id": "194165", "profile_url": "https://www.fotmob.com/players/194165", "competitions": [
    {"id": "54", "name": "Bundesliga", "category": "domestic_league", "team": "Bayern Munich", "games": 32, "rating": 8.02, "rated_games": 31}]}


class TM:
    error = False
    calls = 0

    async def load(self, identity, season):
        self.calls += 1
        if self.error:
            raise ValueError("Transfermarkt временно недоступен")
        return copy.deepcopy(TM_DATA)


class FM:
    error = False

    async def load(self, profile, season, player_id=None):
        if self.error:
            raise ValueError("FotMob временно недоступен")
        return copy.deepcopy(FM_DATA)


def wait_for_job(client, identity):
    for _ in range(100):
        job = client.get(f"/api/jobs/{identity}").json()["job"]
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(.01)
    raise AssertionError("Job did not complete")


def add(client, season="2023-24"):
    response = client.post("/api/players", json={"url": "https://www.transfermarkt.com/harry-kane/profil/spieler/132098", "season": season})
    assert response.status_code == 202, response.text
    return response.json()


def test_import_duplicate_season_isolation_and_persistence(tmp_path):
    path = tmp_path / "seasons.sqlite3"
    tm, fm = TM(), FM()
    with TestClient(create_app(path, tm=tm, fm=fm)) as client:
        assert client.get("/").status_code == 200
        assert client.post("/api/seasons", json={"start_year": 2023}).status_code == 201
        job = wait_for_job(client, add(client)["job"]["id"])
        assert job["status"] == "completed"
        data = client.get("/api/seasons/2023-24").json()
        assert len(data["players"]) == 1
        player = data["players"][0]
        assert player["market_value"]["as_of"] == "2024-06-30"
        assert player["total"]["rating"] == 8.02
        assert player["total"]["adjusted_rating"] == 8.02
        assert player["rating_adjustment"]["group"] == "ST/SS"
        assert player["rating_adjustment"]["coefficient"] == 1.0
        assert player["age"] == 30
        assert player["age_as_of"] == "2024-06-30"
        assert add(client)["duplicate"] is True
        assert tm.calls == 1
        assert client.post("/api/seasons", json={"start_year": 2022}).status_code == 201
        assert client.get("/api/seasons/2022-23").json()["players"] == []
        assert wait_for_job(client, add(client, "2022-23")["job"]["id"])["status"] == "completed"
        assert tm.calls == 2
    with TestClient(create_app(path, tm=tm, fm=fm)) as client:
        assert len(client.get("/api/seasons/2023-24").json()["players"]) == 1
        identity = client.get("/api/seasons/2023-24").json()["players"][0]["id"]
        assert client.delete(f"/api/players/{identity}").status_code == 204
        assert client.get("/api/seasons/2023-24").json()["players"] == []
        assert len(client.get("/api/seasons/2022-23").json()["players"]) == 1


def test_refresh_failure_retains_last_successful_snapshot_and_fm_ratings(tmp_path):
    tm, fm = TM(), FM()
    with TestClient(create_app(tmp_path / "data.db", tm=tm, fm=fm)) as client:
        client.post("/api/seasons", json={"start_year": 2023})
        job = wait_for_job(client, add(client)["job"]["id"])
        identity = job["player_id"]
        tm.error = True
        job = wait_for_job(client, client.post(f"/api/players/{identity}/refresh", json={}).json()["job"]["id"])
        assert job["status"] == "failed"
        player = client.get("/api/seasons/2023-24").json()["players"][0]
        assert player["total"]["goals"] == 36 and player["error"]
        tm.error, fm.error = False, True
        job = wait_for_job(client, client.post(f"/api/players/{identity}/refresh", json={}).json()["job"]["id"])
        player = client.get("/api/seasons/2023-24").json()["players"][0]
        assert job["status"] == "completed"
        assert player["total"]["rating"] == 8.02
        assert any("предыдущей" in message for message in player["warnings"])


def test_invalid_inputs_and_missing_rows_do_not_import(tmp_path):
    tm = TM()
    with TestClient(create_app(tmp_path / "data.db", tm=tm, fm=FM())) as client:
        client.post("/api/seasons", json={"start_year": 2023})
        assert client.post("/api/players", json={"url": "https://evil.com/", "season": "2023-24"}).status_code == 400
        assert client.post("/api/seasons", json={"start_year": True}).status_code == 422
        assert client.post("/api/seasons", json={"start_year": 2300}).status_code == 400
        assert client.get("/api/seasons/2023-25").status_code == 400
        assert client.post("/api/players/999/refresh", json={}).status_code == 404
        assert client.delete("/api/players/999").status_code == 404
        assert tm.calls == 0


def test_pending_imports_resume_after_restart(tmp_path):
    path = tmp_path / "data.db"
    store = Store(path)
    store.add_season(Season(2023))
    job = store.create_job("2023-24", "132098")
    store.update_job(job["id"], status="running")
    with TestClient(create_app(path, tm=TM(), fm=FM())) as client:
        assert wait_for_job(client, job["id"])["status"] == "completed"
        assert len(client.get("/api/seasons/2023-24").json()["players"]) == 1


def test_duplicate_pending_job_is_shared_and_active_refresh_cannot_resurrect_deleted_player(tmp_path):
    store = Store(tmp_path / "data.db")
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "132098", TM_DATA["profile"], TM_DATA, FM_DATA)
    first = store.create_job("2023-24", "132098", player_id=identity)
    second = store.create_job("2023-24", "132098", player_id=identity)
    assert first["id"] == second["id"]
    import pytest
    with pytest.raises(ValueError, match="завершения обновления"):
        store.delete_player(identity)
    store.update_job(first["id"], status="failed")
    assert store.delete_player(identity)


def test_source_errors_explain_sdk_failures():
    assert "не найден" in source_error(NotFound("upstream", status_code=404))
    assert "ограничил" in source_error(UpstreamBlocked("upstream", status_code=429))
    assert "не разрешает" in source_error(UpstreamBlocked("upstream", status_code=403))
    assert "вовремя" in source_error(UpstreamTimeout("upstream"))


def test_existing_saved_ratings_are_adjusted_on_read_without_reimport_or_rounding_loss(tmp_path):
    path = tmp_path / "existing.db"
    stored_tm, stored_fm = copy.deepcopy(TM_DATA), copy.deepcopy(FM_DATA)
    stored_tm["profile"]["position"] = "GK"
    stored_fm["competitions"][0]["rating"] = 6.5003
    snapshot = combine(stored_tm, stored_fm)
    assert snapshot["total"]["rating"] == 6.5
    store = Store(path)
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "132098", snapshot, stored_tm, stored_fm)
    tm = TM()
    with TestClient(create_app(path, tm=tm, fm=FM())) as client:
        first = client.get("/api/seasons/2023-24").json()["players"][0]
        second = client.get("/api/seasons/2023-24").json()["players"][0]
        assert first == second
        assert first["rating_adjustment"]["group"] == "GK"
        assert first["rating_adjustment"]["coefficient"] == .9253
        assert first["total"]["rating"] == 6.5
        assert first["total"]["adjusted_rating"] == pytest.approx(6.5003 / .9253)
        assert round(first["total"]["adjusted_rating"], 2) == 7.03
        assert first["groups"]["domestic_league"]["adjusted_rating"] == pytest.approx(6.5003 / .9253)
        assert tm.calls == 0
    with store.connection() as db:
        preserved = json.loads(db.execute("SELECT snapshot FROM players WHERE id=?", (identity,)).fetchone()[0])
    assert "adjusted_rating" not in preserved["total"]
    assert preserved["competitions"][0]["rating"] == 6.5003
    assert store.raw(identity) == (stored_tm, stored_fm)
