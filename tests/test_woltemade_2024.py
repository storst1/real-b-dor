from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.providers.fotmob import _aggregate_matches
from season_ledger.providers.transfermarkt import aggregate_performances
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def woltemade_youth_sources():
    finals = [
        ("4504198", "4684510", "2025-06-12", "Slovenia U21", "B", None, 9.6),
        ("4504205", "4684511", "2025-06-15", "Czechia U21", "B", None, 9.0),
        ("4589126", "4684250", "2025-06-22", "Italy U21", "VF", "1/4", 8.7),
        ("4589130", "4684329", "2025-06-25", "France U21", "HF", "1/2", 8.2),
        ("4589128", "4684330", "2025-06-28", "England U21", "FF", "final", 6.5),
    ]
    qualifiers = [
        ("4046447", "4120498", "2024-09-04", "Israel U21", "D", None, None),
        ("4046459", "4120529", "2024-09-10", "Estonia U21", "D", None, None),
        ("4046462", "4120518", "2024-10-11", "Bulgaria U21", "D", None, None),
    ]
    tm = {"profile": {"id": "455661", "name": "Nick Woltemade", "position": "CF"}, "competitions": []}
    fm = {"status": "matched", "competitions": []}
    for tm_id, tm_name, kind, fm_id, fm_name, category, games in [
        ("21EU", "UEFA Under-21 Euro", 17, "288", "EURO U21", "national_major", finals),
        ("U21Q", "UEFA Euro Under-21 qualification", 20, "10437", "EURO U-21 Qualification", "national_regular", qualifiers),
    ]:
        tm["competitions"].append({
            "id": tm_id, "name": tm_name, "category": "other", "competition_type_id": kind,
            "team": "Germany U21", "games": len(games),
            "matches": [{"id": tm_match_id, "date": day, "team": "Germany U21", "opponent": opponent, "stage_code": code}
                        for tm_match_id, _, day, opponent, code, _, _ in games],
        })
        fm["competitions"].append({
            "id": fm_id, "name": fm_name, "category": category, "team": "Germany U21",
            "games": len(games), "rating": 8.4 if fm_id == "288" else None, "rated_games": 5 if fm_id == "288" else 0,
            "matches": [{"id": fm_match_id, "date": day, "team": "Germany U21", "opponent": opponent,
                         "competition_id": fm_id, "competition": fm_name, "category": category,
                         "is_national": True, "stage": stage, "rating": rating}
                        for _, fm_match_id, day, opponent, _, stage, rating in games],
        })
    tm["competitions"][0].update(place="2", stage_code="FF", final_outcome="runner_up")
    return tm, fm


def test_real_woltemade_u21_final_and_qualifier_sources_join_without_losing_ratings():
    tm, fm = woltemade_youth_sources()
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert snapshot["unmatched_fotmob_competitions"] == []
    rows = {row["id"]: row for row in snapshot["competitions"]}
    assert rows["21EU"]["rated_games"] == 5
    assert rows["U21Q"]["rated_games"] == 0
    assert all(row["category"] == "other" for row in rows.values())
    assert all(match["category"] == "other" and match["is_national"] is True
               for row in rows.values() for match in row["fotmob_matches"])
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 5
    assert weighted["unrated_games"] == 3
    assert weighted["history_games"] == 8
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == 8
    assert weighted["rating"] == pytest.approx((9.6 + 9.0 + 8.7 + 8.2 * 2 + 6.5 * 3) / 8)
    assert {match["id"] for match in weighted["matches"]} == {
        "4684510", "4684511", "4684250", "4684329", "4684330", "4120498", "4120529", "4120518",
    }
    assert [match["weight"] for match in weighted["matches"] if match["raw_rating"] is not None] == [1, 1, 1, 2, 3]
    for match in weighted["matches"]:
        assert {rule["key"] for rule in match["weight_rules"]}.isdisjoint({
            "euro_copa", "world_cup", "major_final", "grand_national_opponent",
        })


def test_unknown_fotmob_youth_id_is_not_guessed_from_a_similar_name():
    tm, fm = woltemade_youth_sources()
    fm["competitions"][0]["id"] = "999999"
    for match in fm["competitions"][0]["matches"]:
        match["competition_id"] = "999999"

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "missing_history"
    assert weighted["rating"] is None
    assert weighted["missing_source_samples"][0]["competition_id"] == "999999"
    assert weighted["missing_source_samples"][0]["expected_rated_games"] == 5


def test_truncated_u21_history_still_blocks_a_final_rating():
    tm, fm = woltemade_youth_sources()
    fm["competitions"][0]["matches"].pop()

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "missing_history"
    assert weighted["rating"] is weighted["raw_rating"] is None
    assert weighted["missing_source_samples"][0]["expected_rated_games"] == 5
    assert weighted["missing_source_samples"][0]["available_rated_games"] == 4


def test_senior_euro_category_and_additions_are_unchanged():
    tm = {"profile": {"id": "1", "position": "CF"}, "competitions": [{
        "id": "EURO", "name": "UEFA Euro", "category": "national_major", "competition_type_id": 11,
        "team": "Senior Team", "games": 1,
        "matches": [{"id": "senior-tm", "date": "2024-07-14", "opponent": "Spain", "stage_code": "FF"}],
    }]}
    fm = {"competitions": [{
        "id": "50", "name": "EURO", "category": "national_major", "team": "Senior Team", "games": 1,
        "rating": 8, "rated_games": 1,
        "matches": [{"id": "senior-fm", "date": "2024-07-14", "competition_id": "50", "competition": "EURO",
                     "category": "national_major", "opponent": "Spain", "stage": "final", "rating": 8}],
    }]}

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert snapshot["competitions"][0]["category"] == "national_major"
    assert weighted["status"] == "complete"
    assert weighted["total_weight"] == 4.5
    assert {rule["key"] for rule in weighted["matches"][0]["weight_rules"]} == {
        "base", "final", "grand_national_opponent", "euro_copa", "major_final",
    }


def test_fetch_parser_keeps_the_u21_final_before_domain_category_bridge():
    raw_match = {
        "id": 4684330, "matchDate": {"utcTime": "2025-06-28T19:00:00Z"}, "leagueId": 288, "leagueName": "EURO U21",
        "teamId": 6638, "teamName": "Germany U21", "opponentTeamName": "England U21", "stage": "final",
        "playedInMatch": True, "minutesPlayed": 120, "ratingProps": {"rating": 6.5},
    }
    profile = {"careerHistory": {"careerItems": {"national team": {"teamEntries": [{"teamId": 6638}]}}}}

    rows = _aggregate_matches([raw_match], profile, Season(2024))

    assert len(rows) == 1
    assert rows[0]["category"] == "national_major"
    assert rows[0]["rated_games"] == 1
    assert rows[0]["matches"][0]["id"] == "4684330"


@pytest.mark.parametrize("season,expected", [(Season(2024), 1), (Season(2025), 0)])
def test_real_june_u21_final_already_belongs_to_the_correct_tm_season(season, expected):
    record = {
        "gameInformation": {"gameId": "4589128", "competitionId": "21EU", "competitionTypeId": 17,
                            "seasonId": "2024", "isNationalGame": True, "competitionGroupId": "FF",
                            "date": {"dateTimeUTC": "2025-06-28T19:00:00+00:00"}, "gameState": "extra_time"},
        "clubsInformation": {"club": {"clubId": "3817"}, "opponent": {"clubId": "9565"}},
        "statistics": {"generalStatistics": {"participationState": "played"},
                       "playingTimeStatistics": {"playedMinutes": 120}, "goalStatistics": {"goalsScoredTotal": 0, "assists": 0}},
    }

    rows = aggregate_performances([record], season, {"21EU": {"name": "UEFA Under-21 Euro", "typeId": 17}},
                                  {"3817": {"name": "Germany U21"}, "9565": {"name": "England U21"}})

    assert sum(row["games"] for row in rows) == expected
    if rows:
        assert rows[0]["category"] == "other"


def test_saved_woltemade_restores_youth_ratings_without_import_or_senior_euro_bonus(tmp_path):
    tm, fm = woltemade_youth_sources()
    tm["competitions"].append({"id": "DFB", "name": "DFB-Pokal", "category": "domestic_cup", "competition_type_id": 8,
                               "team": "VfB Stuttgart", "games": 5, "place": "1", "final_outcome": "winner"})
    saved = combine(tm, {"status": "matched", "competitions": []})
    saved["warnings"] = ["EURO U21 was not matched."]
    original = deepcopy((tm, fm))
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2024))
    identity = store.save_player("2024-25", "455661", saved, tm, fm)

    first = store.player(identity)
    reopened = Store(store.path).player(identity)

    assert first["warnings"] == []
    assert first["weighted_rating"]["status"] == "complete"
    assert first["weighted_rating"]["rated_games"] == 5
    assert first["weighted_rating"]["unrated_games"] == 3
    assert first["weighted_rating"]["rating"] == pytest.approx(7.9)
    assert reopened["weighted_rating"] == first["weighted_rating"]
    assert reopened["updated_at"] == first["updated_at"]
    assert first["season_points"]["status"] == "complete"
    assert {row["kind"]: row["points"] for row in first["season_points"]["bonuses"]} == {"cup": 12, "no_champions_league": -25}
    assert first["season_points"]["total"] == pytest.approx(777)
    assert store.raw(identity) == original
