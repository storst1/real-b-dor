from collections import Counter
from copy import deepcopy
import math

import pytest

from season_ledger.domain import combine
from season_ledger.weighting import calculate_weighted_rating


def qualification_sources(player="guler"):
    # Actual saved 2025/26 appearances: TM group edition 2024, playoffs 2025.
    if player == "guler":
        profile = {"id": "861410", "name": "Arda Güler", "position": "AM"}
        team, group = "Turkiye", "E"
        games = [
            ("4510734", "4689339", "2025-09-04", "Georgia", group, None, 7.5),
            ("4510665", "4689342", "2025-09-07", "Spain", group, None, 6.6),
            ("4510737", "4689343", "2025-10-11", "Bulgaria", group, None, 9.0),
            ("4510663", "4689345", "2025-10-14", "Georgia", group, None, 7.1),
            ("4510664", "4689348", "2025-11-15", "Bulgaria", group, None, 6.9),
            ("4770091", "5067289", "2026-03-26", "Romania", "HF", "1/2", 8.0),
            ("4770127", "5067301", "2026-03-31", "Kosovo", "FF", "final", 7.4),
        ]
    else:
        profile = {"id": "38253", "name": "Robert Lewandowski", "position": "CF"}
        team, group = "Poland", "G"
        games = [
            ("4510673", "4689270", "2025-09-04", "Netherlands", group, None, 6.5),
            ("4510749", "4689272", "2025-09-07", "Finland", group, None, 7.8),
            ("4510757", "4689275", "2025-10-12", "Lithuania", group, None, 8.2),
            ("4510752", "4689278", "2025-11-14", "Netherlands", group, None, 7.3),
            ("4510761", "4689279", "2025-11-17", "Malta", group, None, 8.5),
            ("4770077", "5067292", "2026-03-26", "Albania", "HF", "1/2", 7.3),
            ("4770126", "5067300", "2026-03-31", "Sweden", "FF", "final", 6.5),
        ]
    tm = {"profile": profile, "competitions": []}
    for identity, name, season, records in [
        ("WMQ6", "World Cup Qualifiers (Europe)", "2024", games[:5]),
        ("POWM", "World Cup Qualification Playoffs", "2025", games[5:]),
    ]:
        tm["competitions"].append({
            "id": identity, "name": name, "category": "national_regular", "competition_type_id": 19,
            "team": team, "games": len(records), "season_id": season, "season_ids": [season],
            "stage_code": group if identity == "WMQ6" else "FF",
            "stage_history": [group] if identity == "WMQ6" else ["HF", "FF"],
            "matches": [{"id": tm_id, "date": day, "team": team, "opponent": opponent,
                         "stage_code": code, "season_id": season}
                        for tm_id, _, day, opponent, code, _, _ in records],
        })
    fm = {"status": "matched", "competitions": [{
        "id": "10195", "name": "World Cup Qualification UEFA", "category": "national_regular", "team": team,
        "games": 7, "rated_games": 7, "rating": math.fsum(game[6] for game in games) / 7,
        "matches": [{"id": fm_id, "date": day, "team": team, "opponent": opponent,
                     "competition_id": "10195", "competition": "World Cup Qualification UEFA",
                     "category": "national_regular", "is_national": True, "stage": stage, "rating": rating}
                    for _, fm_id, day, opponent, _, stage, rating in games],
    }]}
    return tm, fm


@pytest.mark.parametrize("player,mean,coefficient", [("guler", 7.53, 1.0158), ("lewandowski", 7.24, 1.0)])
@pytest.mark.parametrize("legacy", [False, True])
def test_real_uefa_qualification_feed_splits_groups_and_playoffs_without_world_cup_weights(player, mean, coefficient, legacy):
    tm, fm = qualification_sources(player)
    if legacy:
        for match in fm["competitions"][0]["matches"]:
            match.pop("category")
            match.pop("is_national")
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == snapshot["unmatched_fotmob_competitions"] == []
    rows = {row["id"]: row for row in snapshot["competitions"]}
    assert rows["WMQ6"]["rated_games"] == 5
    assert rows["POWM"]["rated_games"] == 2
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == weighted["history_games"] == 7
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == 10
    assert weighted["raw_rating"] == pytest.approx(mean)
    assert weighted["rating"] == pytest.approx(mean / coefficient)
    assert Counter(match["id"] for match in weighted["matches"]) == Counter(
        match["id"] for match in fm["competitions"][0]["matches"])
    assert [match["weight"] for match in weighted["matches"]] == [1, 1, 1, 1, 1, 2, 3]
    assert [match["context"]["stage"] for match in weighted["matches"]] == ["group"] * 5 + ["semifinal", "final"]
    for match in weighted["matches"]:
        assert match["context"]["category"] == "national_regular"
        assert {rule["key"] for rule in match["weight_rules"]}.issubset({"base", "semifinal", "final"})


@pytest.mark.parametrize("problem", ["ambiguous", "truncated"])
def test_uefa_qualification_partition_retains_ambiguous_or_missing_source_samples(problem):
    tm, fm = qualification_sources()
    if problem == "ambiguous":
        tm["competitions"][0]["matches"].append(deepcopy(tm["competitions"][1]["matches"][0]))
    else:
        fm["competitions"][0]["matches"].pop()
    original = deepcopy((tm, fm))

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert (tm, fm) == original
    assert weighted["status"] == "missing_history"
    assert weighted["rating"] is weighted["raw_rating"] is None
    assert weighted["missing_source_samples"]
    assert weighted["duplicate_games"] == 0


@pytest.mark.parametrize("group_id,feed_id", [("WMQ2", "10195"), ("WMQ4", "10195"), ("WMQ6", "10607")])
def test_uefa_qualification_registry_does_not_partition_other_confederations_or_the_euro_feed(group_id, feed_id):
    tm, fm = qualification_sources()
    tm["competitions"][0]["id"] = group_id
    fm["competitions"][0]["id"] = feed_id

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert weighted["status"] == "missing_history"
    assert weighted["rating"] is None
    assert snapshot["competitions"][0]["rated_games"] is None


def test_uefa_qualification_with_only_the_group_phase_remains_complete():
    tm, fm = qualification_sources()
    tm["competitions"].pop()
    row = fm["competitions"][0]
    row["matches"] = row["matches"][:5]
    row.update(games=5, rated_games=5, rating=math.fsum(match["rating"] for match in row["matches"]) / 5)

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 5
    assert weighted["total_weight"] == 5
