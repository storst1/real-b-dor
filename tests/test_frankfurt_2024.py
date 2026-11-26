from collections import Counter
from copy import deepcopy
import math

import pytest

from season_ledger.domain import Season, combine
from season_ledger.points import calculate_season_points
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def frankfurt_sources(player="ekitike"):
    # Selected actual 2024/25 appearances, not a synthetic full-season result.
    # TM match, FM match, date, TM opponent, FM opponent, stage, rating, home, score.
    besiktas = ("4445515", "4622124", "2024-10-03", "Beşiktaş JK", "Beşiktaş", "GP",
                6.4 if player == "ekitike" else 8.5, False, 3, 1)
    if player == "ekitike":
        profile = {"id": "709726", "name": "Hugo Ekitiké", "position": "CF"}
        records = [("EL", "73", "UEFA Europa League", "Europa League", "continental", 10,
                    "Eintracht Frankfurt", [besiktas,
                    ("4445369", "4622023", "2025-01-23", "Ferencvárosi TC", "Ferencváros", "GP", 8.2, True, 2, 0)])]
    else:
        profile = {"id": "445939", "name": "Omar Marmoush", "position": "CF"}
        records = [("EL", "73", "UEFA Europa League", "Europa League", "continental", 10,
                    "Eintracht Frankfurt", [besiktas]),
                   ("L1", "54", "Bundesliga", "Bundesliga", "domestic_league", 1, "Eintracht Frankfurt",
                    [("4373385", "4534587", "2024-10-06", "FC Bayern Munich", "Bayern München", None, 9.2, True, 3, 3)]),
                   ("GB1", "47", "Premier League", "Premier League", "domestic_league", 1, "Manchester City",
                    [("4362764", "4506629", "2025-02-15", "Newcastle United", "Newcastle United", None, 9.7, True, 4, 0)])]
    tm = {"profile": profile, "competitions": []}
    fm = {"status": "matched", "competitions": []}
    for tm_id, fm_id, name, fm_name, category, type_id, team, games in records:
        tm_row = {"id": tm_id, "name": name, "category": category, "competition_type_id": type_id,
                  "team": team, "games": len(games), "matches": []}
        fm_row = {"id": fm_id, "name": fm_name, "category": category, "team": team,
                  "games": len(games), "rated_games": len(games),
                  "rating": math.fsum(game[6] for game in games) / len(games), "matches": []}
        if player == "ekitike":
            # The team's eventual quarterfinal cannot change a group appearance.
            tm_row.update(stage_code="VFR", stage_history=["GP", "AFH", "AFR", "VFH", "VFR"])
        for tm_match, fm_match, day, official, short, code, rating, home, scored, conceded in games:
            tm_row["matches"].append({"id": tm_match, "date": day, "team": team, "opponent": official,
                                      "competition_id": tm_id, "competition_type_id": type_id,
                                      "season_id": "2024", "stage_code": code, "is_home": home,
                                      "goals_for": scored, "goals_against": conceded})
            fm_row["matches"].append({"id": fm_match, "date": day, "team": team, "opponent": short,
                                      "competition_id": fm_id, "competition": fm_name, "category": category,
                                      "is_national": False, "stage": None, "rating": rating, "is_home": home,
                                      "home_score": scored if home else conceded,
                                      "away_score": conceded if home else scored})
        tm["competitions"].append(tm_row)
        fm["competitions"].append(fm_row)
    return tm, fm


@pytest.mark.parametrize("player,count,weight,mean", [("ekitike", 2, 2, 7.3), ("marmoush", 3, 3.5, 32 / 3.5)])
@pytest.mark.parametrize("legacy", [False, True])
def test_real_frankfurt_opponents_restore_complete_weighted_calculation(player, count, weight, mean, legacy):
    tm, fm = frankfurt_sources(player)
    if legacy:
        for row in fm["competitions"]:
            for match in row["matches"]:
                match.pop("category")
                match.pop("is_national")
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == snapshot["unmatched_fotmob_competitions"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == weighted["history_games"] == count
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == weight
    assert weighted["raw_rating"] == weighted["rating"] == pytest.approx(mean)
    source_ids = Counter(match["id"] for row in fm["competitions"] for match in row["matches"])
    assert Counter(match["id"] for match in weighted["matches"] if match["included"]) == source_ids
    europa = [match for match in weighted["matches"] if match["competition_id"] == "73"]
    assert {match["id"] for match in europa} == ({"4622124", "4622023"} if player == "ekitike" else {"4622124"})
    for match in europa:
        assert match["context"]["match_context_status"] == "matched"
        assert match["context"]["competition_id"] == "EL"
        assert match["context"]["stage_code"] == "GP"
        assert match["context"]["stage"] == "league"
        assert match["weight"] == 1
        assert {rule["key"] for rule in match["weight_rules"]} == {"base"}


def test_marmoush_transfer_keeps_both_clubs_and_all_selected_ratings():
    tm, fm = frankfurt_sources("marmoush")

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert {(row["id"], row["team"], row["rated_games"]) for row in snapshot["competitions"]} == {
        ("EL", "Eintracht Frankfurt", 1), ("L1", "Eintracht Frankfurt", 1), ("GB1", "Manchester City", 1),
    }
    assert Counter(match["team"] for match in weighted["matches"] if match["included"]) == {
        "Eintracht Frankfurt": 2, "Manchester City": 1,
    }
    matches = {match["id"]: match for match in weighted["matches"]}
    assert {identity: match["rating"] for identity, match in matches.items()} == {
        "4622124": 8.5, "4534587": 9.2, "4506629": 9.7,
    }
    assert matches["4534587"]["weight"] == 1.5  # Bayern opponent addition still applies.
    assert matches["4506629"]["context"]["competition_id"] == "47"
    assert weighted["rating"] == pytest.approx((8.5 + 9.2 * 1.5 + 9.7) / 3.5)


@pytest.mark.parametrize("identity,wrong_opponent", [
    ("4445515", "Fenerbahçe SK"), ("4445515", "Beşiktaş JK U19"), ("4445515", "Beşiktaş JK Women"),
    ("4445369", "Ferencvárosi TC U21"), ("4445369", "Ferencvárosi TC Women"),
])
def test_same_day_different_or_youth_opponent_does_not_inherit_the_alias_context(identity, wrong_opponent):
    tm, fm = frankfurt_sources()
    match = next(match for match in tm["competitions"][0]["matches"] if match["id"] == identity)
    match["opponent"] = wrong_opponent

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is None
    assert weighted["unresolved_match_ids"] == ["4622124" if identity == "4445515" else "4622023"]


def test_team_quarterfinal_history_does_not_replace_missing_individual_stage():
    tm, fm = frankfurt_sources()
    tm["competitions"][0]["matches"][1].pop("stage_code")

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "incomplete_context"
    assert weighted["rating"] is None
    assert weighted["unresolved_match_ids"] == ["4622023"]


def test_saved_frankfurt_sources_rebuild_weighted_rating_and_points_without_import(tmp_path):
    tm, fm = frankfurt_sources()
    original = deepcopy((tm, fm))
    stale = combine(tm, {"status": "matched", "competitions": []})
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2024))
    identity = store.save_player("2024-25", tm["profile"]["id"], stale, tm, fm)

    first = store.player(identity)
    reopened = Store(store.path).player(identity)

    assert first["weighted_rating"]["status"] == "complete"
    assert first["weighted_rating"]["rating"] == pytest.approx(7.3)
    assert first["weighted_rating"]["rated_games"] == 2
    points = calculate_season_points(combine(tm, fm), weighted_rating=7.3)
    assert first["season_points"] == points
    assert points["status"] == "complete"
    assert points["total"] == pytest.approx(693)
    assert {bonus["kind"]: bonus["points"] for bonus in points["bonuses"]} == {
        "no_champions_league": -25, "no_national_team": -12,
    }
    assert reopened["weighted_rating"] == first["weighted_rating"]
    assert reopened["updated_at"] == first["updated_at"]
    assert store.raw(identity) == original
