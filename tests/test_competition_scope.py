"""Only the Champions League gets a column; every competition stays in totals."""

from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from season_ledger.domain import (
    CATEGORIES, Season, aggregate, apply_competition_scope, combine,
    contributes_to_group, is_champions_league,
)
from season_ledger.ratings import apply_position_adjustment
from season_ledger.store import Store
from season_ledger.web import create_app


def competition(identity, name, category, games, goals, assists, minutes, rating, rated_games, day, *, team="Club"):
    return {"id": identity, "name": name, "category": category, "team": team,
            "games": games, "goals": goals, "assists": assists, "minutes": minutes,
            "rating": rating, "rated_games": rated_games, "place": None,
            "matches": [{"id": f"tm-{identity}", "date": f"{day}T19:00:00Z"}],
            "sources": ["Transfermarkt", "FotMob"]}


def competitions():
    return [
        competition("L1", "Bundesliga", "domestic_league", 20, 10, 5, 1800, 6.5, 20, "2023-09-10"),
        competition("DFB", "DFB-Pokal", "domestic_cup", 4, 3, 1, 200, 7.0, 3, "2024-01-10"),
        competition("WMQ", "World Cup Qualification", "national_regular", 5, 2, 1, 350, 7.2, 4, "2023-10-10", team="Nation"),
        competition("EM", "European Championship", "national_major", 3, 1, 0, 240, 7.4, 2, "2024-07-10", team="Nation"),
        competition("CL", "UEFA Champions League", "continental", 6, 4, 2, 500, 8.0, 5, "2023-09-20"),
        competition("EL", "UEFA Europa League", "continental", 10, 15, 6, 900, 10.0, 10, "2023-09-21"),
        competition("ECL", "UEFA Conference League", "continental", 7, 9, 4, 630, 1.0, 7, "2023-10-21"),
        competition("USC", "UEFA Super Cup", "continental", 1, 2, 0, 90, 10.0, 1, "2023-08-16"),
        competition("KLUB", "FIFA Club World Cup", "continental", 5, 8, 2, 450, 10.0, 5, "2023-12-15"),
        competition("CLQ", "UEFA Champions League Qualifying", "continental", 4, 3, 1, 320, 9.0, 4, "2023-08-15"),
        competition("PLAYOFF", "Promotion playoffs", "other", 2, 1, 0, 120, 6.0, 1, "2024-05-15"),
    ]


def legacy_snapshot(rows=None):
    rows = deepcopy(competitions() if rows is None else rows)
    return {"name": "Season Player", "transfermarkt_id": "1", "position": "GK", "birth_date": "1990-01-01",
            "market_value": {"amount": 10000000, "currency": "EUR", "observed_on": "2024-06-01", "as_of": "2024-06-30"},
            "competitions": rows,
            # This simulates a snapshot saved before the CL-only column policy.
            "groups": {category["key"]: aggregate([row for row in rows if row["category"] == category["key"]])
                       for category in CATEGORIES},
            "total": aggregate(rows), "warnings": ["Original source warning"],
            "source_metadata": {"retrieved_from": ["Transfermarkt", "FotMob"]}}


@pytest.mark.parametrize(("provider", "identity"), [
    ("transfermarkt", "CL"), ("fotmob", "42"), ("fotmob", 42),
])
def test_main_champions_league_source_ids_are_authoritative(provider, identity):
    assert is_champions_league({"id": identity, "name": "Legacy caption"}, provider=provider)


@pytest.mark.parametrize("provider", ["transfermarkt", "fotmob"])
@pytest.mark.parametrize("name", ["UEFA Champions League", "Champions League", "  uefa champions league  "])
@pytest.mark.parametrize("identity", [None, ""])
def test_exact_name_fallback_is_available_only_without_a_source_id(provider, name, identity):
    assert is_champions_league({"id": identity, "name": name}, provider=provider)


@pytest.mark.parametrize(("provider", "identity"), [
    ("transfermarkt", "CLQ"), ("transfermarkt", "EL"), ("transfermarkt", "ECL"),
    ("transfermarkt", "USC"), ("transfermarkt", "KLUB"), ("transfermarkt", "42"),
    ("fotmob", "73"), ("fotmob", "10216"), ("fotmob", "78"), ("fotmob", "CL"),
])
def test_unknown_or_other_source_id_cannot_be_overridden_by_a_champions_league_name(provider, identity):
    assert not is_champions_league({"id": identity, "name": "UEFA Champions League"}, provider=provider)


@pytest.mark.parametrize("name", [
    "CAF Champions League", "AFC Champions League", "AFC Champions League Elite",
    "UEFA Women's Champions League", "UEFA Youth League", "UEFA Champions League U19",
    "UEFA Champions League Qualification", "UEFA Champions League Qualifying",
    "UEFA Europa League", "UEFA Conference League", "UEFA Europa Conference League",
    "UEFA Super Cup", "FIFA Club World Cup", "Champions League final", "",
])
@pytest.mark.parametrize("provider", ["transfermarkt", "fotmob"])
def test_other_organisations_editions_and_competitions_do_not_match_name_fallback(name, provider):
    assert not is_champions_league({"name": name}, provider=provider)


def test_only_main_cl_contributes_to_continental_column_without_reclassifying_other_rows():
    for row in competitions():
        assert contributes_to_group(row, "continental") is (row["id"] == "CL")
        assert contributes_to_group(row, "other") is (row["category"] == "other")
        if row["category"] != "continental":
            assert contributes_to_group(row, row["category"])


def test_scope_rebuilds_only_group_policy_and_retains_all_source_competitions_and_total():
    snapshot = legacy_snapshot()
    before = deepcopy(snapshot)
    assert snapshot["groups"]["continental"]["games"] == 33
    result = apply_competition_scope(snapshot)
    assert snapshot == before
    for original, current in zip(snapshot["competitions"], result["competitions"], strict=True):
        assert {key: current[key] for key in original} == original
        assert current["is_champions_league"] is (original["id"] == "CL")
    assert len(result["competitions"]) == 11
    assert result["groups"]["continental"] == aggregate([snapshot["competitions"][4]])
    assert result["groups"]["continental"]["games"] == 6
    assert result["groups"]["continental"]["rating"] == 8.0
    assert result["groups"]["continental"]["rated_games"] == 5
    assert result["total"] == snapshot["total"]
    assert result["total"]["games"] == 67
    assert result["total"]["goals"] == 58
    assert result["total"]["assists"] == 22
    assert result["total"]["rating"] == 7.15
    assert result["total"]["rated_games"] == 62
    for category in ("domestic_league", "domestic_cup", "national_regular", "national_major", "other"):
        assert result["groups"][category] == snapshot["groups"][category]
    assert result["groups"]["other"]["games"] == 2  # no international competition moves to Other
    assert result["market_value"] == snapshot["market_value"]
    assert result["warnings"] == snapshot["warnings"]
    assert result["source_metadata"] == snapshot["source_metadata"]
    assert apply_competition_scope(result) == result
    result["competitions"][0]["sources"].append("Changed")
    result["source_metadata"]["retrieved_from"].append("Changed")
    assert snapshot == before


def test_only_cl_group_adjusted_rating_uses_cl_rows_while_total_uses_every_competition():
    result = apply_position_adjustment(apply_competition_scope(legacy_snapshot()))
    assert result["groups"]["continental"]["adjusted_rating"] == pytest.approx(8.0 / .9253)
    expected_mean = 443.6 / 62
    assert result["total"]["adjusted_rating"] == pytest.approx(expected_mean / .9253)
    assert result["total"]["adjusted_rating"] != pytest.approx(7.15 / .9253)
    for row in result["competitions"]:
        assert row["adjusted_rating"] == pytest.approx(row["rating"] / .9253)


def test_no_main_cl_means_empty_column_but_other_international_games_stay_in_total_and_details():
    rows = [row for row in competitions() if row["id"] != "CL"]
    result = apply_position_adjustment(apply_competition_scope(legacy_snapshot(rows)))
    assert result["groups"]["continental"]["games"] == 0
    assert result["groups"]["continental"]["rating"] is None
    assert result["groups"]["continental"]["adjusted_rating"] is None
    assert result["total"]["games"] == 61
    assert {row["id"] for row in result["competitions"] if row["category"] == "continental"} == {"EL", "ECL", "USC", "KLUB", "CLQ"}
    assert result["total"]["rating"] == aggregate(rows)["rating"]
    assert result["total"]["adjusted_rating"] is not None


def test_unknown_non_cl_rating_sample_does_not_poison_cl_column_but_keeps_total_uncertainty():
    rows = competitions()
    next(row for row in rows if row["id"] == "EL")["rated_games"] = None
    result = apply_position_adjustment(apply_competition_scope(legacy_snapshot(rows)))
    assert result["groups"]["continental"]["rating"] == 8.0
    assert result["groups"]["continental"]["adjusted_rating"] == pytest.approx(8.0 / .9253)
    assert result["total"]["games"] == 67
    assert result["total"]["rating"] is None
    assert result["total"]["adjusted_rating"] is None


def sources():
    rows = competitions()
    tm = {"profile": {"id": "1", "name": "Season Player", "position": "GK", "birth_date": "1990-01-01"},
          "competitions": deepcopy(rows), "market_value": None, "warnings": []}
    fotmob_ids = {"L1": "54", "DFB": "209", "WMQ": "10195", "EM": "50", "CL": "42",
                  "EL": "73", "ECL": "10216", "USC": "74", "KLUB": "78", "CLQ": "10614", "PLAYOFF": "900"}
    fm = {"status": "matched", "player_id": "100", "profile_url": "https://www.fotmob.com/players/100",
          "competitions": [{**row, "id": fotmob_ids[row["id"]],
                            "games": row["games"] + 3, "goals": 999,
                            "matches": [{"id": f"fm-{row['id']}", "date": row["matches"][0]["date"]}]}
                           for row in rows], "warnings": []}
    return tm, fm


def test_combining_sources_keeps_transfermarkt_counts_and_all_fotmob_ratings_in_total():
    tm, fm = sources()
    before_tm, before_fm = deepcopy(tm), deepcopy(fm)
    result = combine(tm, fm)
    assert len(result["competitions"]) == 11
    assert result["total"]["games"] == 67  # not inflated by FotMob appearances
    assert result["total"]["goals"] == 58  # FotMob's conflicting goals never replace TM
    assert result["total"]["rating"] == 7.15
    assert result["total"]["rated_games"] == 62
    assert result["groups"]["continental"]["games"] == 6
    assert result["groups"]["continental"]["rating"] == 8.0
    assert {row["id"] for row in result["competitions"] if "FotMob" in row["sources"]} == {row["id"] for row in tm["competitions"]}
    assert tm == before_tm
    assert fm == before_fm


class NoImport:
    def __init__(self):
        self.calls = 0

    async def load(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("Reading saved season statistics must not fetch external sources")


def test_cached_api_rebuilds_cl_column_and_all_competition_total_without_reimport_or_storage_mutation(tmp_path):
    path = tmp_path / "existing.db"
    snapshot = legacy_snapshot()
    stored_tm, stored_fm = sources()
    store = Store(path)
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "1", snapshot, stored_tm, stored_fm)
    tm, fm = NoImport(), NoImport()
    with TestClient(create_app(path, tm=tm, fm=fm)) as client:
        first_response = client.get("/api/seasons/2023-24")
        assert first_response.status_code == 200
        first = first_response.json()["players"][0]
        second = client.get("/api/seasons/2023-24").json()["players"][0]
        assert first == second
        assert first["groups"]["continental"]["games"] == 6
        assert first["groups"]["continental"]["rating"] == 8.0
        assert first["groups"]["continental"]["adjusted_rating"] == pytest.approx(8.0 / .9253)
        assert first["total"]["games"] == 67
        assert first["total"]["goals"] == 58
        assert first["total"]["rating"] == 7.15
        assert first["total"]["adjusted_rating"] == pytest.approx((443.6 / 62) / .9253)
        assert len(first["competitions"]) == 11
        assert first["groups"]["other"]["games"] == 2
        assert tm.calls == fm.calls == 0
    with store.connection() as db:
        preserved = json.loads(db.execute("SELECT snapshot FROM players WHERE id=?", (identity,)).fetchone()[0])
    assert preserved == snapshot
    assert preserved["groups"]["continental"]["games"] == 33
    assert store.raw(identity) == (stored_tm, stored_fm)
