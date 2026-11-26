from copy import deepcopy

import pytest

from season_ledger.domain import combine
from season_ledger.match_context import resolve_match_context
from season_ledger.weighting import calculate_weighted_rating


def gyokeres_league_sources():
    games = [
        ("4396936", "4539366", "2025-05-17", "Vitória Guimarães SC", "Vitória de Guimarães", 8.6),
        ("4396796", "4539273", "2025-03-29", "CF Estrela Amadora", "Estrela da Amadora", 9.4),
        ("4396923", "4539319", "2025-04-12", "CD Santa Clara", "Santa Clara", 6.9),
        ("4396907", "4539201", "2025-02-23", "Avs Futebol", "AVS Futebol SAD", 7.8),
    ]
    tm = {"profile": {"id": "325443", "name": "Viktor Gyökeres", "position": "CF"}, "competitions": [{
        "id": "PO1", "name": "Liga Portugal", "category": "domestic_league", "competition_type_id": 1,
        "team": "Sporting CP", "games": 4,
        "matches": [{"id": tm_id, "date": day, "team": "Sporting CP", "opponent": official, "stage_code": None}
                    for tm_id, _, day, official, _, _ in games],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": "61", "name": "Liga Portugal", "category": "domestic_league", "team": "Sporting CP",
        "games": 4, "rated_games": 4, "rating": sum(game[5] for game in games) / 4,
        "matches": [{"id": fm_id, "date": day, "team": "Sporting CP", "opponent": short,
                     "competition_id": "61", "competition": "Liga Portugal", "stage": None, "rating": rating}
                    for _, fm_id, day, _, short, rating in games],
    }]}
    return tm, fm


@pytest.mark.parametrize("definition", ["verified_id", "exact_name"])
def test_real_gyokeres_league_matches_use_direct_fotmob_context_without_opponent_aliases(definition):
    tm, fm = gyokeres_league_sources()
    if definition == "exact_name":
        for match in fm["competitions"][0]["matches"]:
            match.pop("competition_id")
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == weighted["history_games"] == 4
    assert weighted["total_weight"] == 4
    assert weighted["rating"] == pytest.approx((8.6 + 9.4 + 6.9 + 7.8) / 4)
    assert {match["id"] for match in weighted["matches"]} == {"4539366", "4539273", "4539319", "4539201"}
    for match in weighted["matches"]:
        context = match["context"]
        assert context["competition_provider"] == "fotmob"
        assert context["competition_id"] == ("61" if definition == "verified_id" else None)
        assert context["competition_kind"] == "league"
        assert context["category"] == "domestic_league"
        assert context["stage"] is None
        assert context["is_knockout"] is False
        assert context["is_national"] is False
        assert match["weight"] == 1
        assert [rule["key"] for rule in match["weight_rules"]] == ["base"]


def test_verified_liga_portugal_id_is_sufficient_without_transfermarkt():
    _, fm = gyokeres_league_sources()

    context = resolve_match_context(fm["competitions"][0]["matches"][0], [])

    assert context["competition_provider"] == "fotmob"
    assert context["competition_id"] == "61"
    assert context["competition_kind"] == "league"
    assert context["match_context_status"] == "matched"


@pytest.mark.parametrize("name", [
    "Liga Portugal 2", "Liga Portugal 2 Sabseg", "Liga Portugal Cup",
    "Liga Portugal U19", "League Cup", "Taça de Portugal", "Portugal",
])
def test_liga_portugal_exact_name_does_not_identify_other_formats(name):
    match = {"id": "different-competition", "competition": name,
             "opponent": "Neutral Team", "stage": None, "rating": 8}

    context = resolve_match_context(match, [])

    assert context["match_context_status"] == "unmatched"
    assert context.get("competition_kind") != "league"


@pytest.mark.parametrize("identity,name,category,stage,is_national,expected_kind", [
    ("187", "League Cup", "domestic_cup", "1/2", False, "domestic_cup"),
    ("186", "Taça de Portugal", "domestic_cup", "final", False, "domestic_cup"),
    ("9808", "UEFA Nations League C", "national_regular", "group", True, "national_regular"),
])
def test_portuguese_cups_and_national_matches_keep_their_own_context(identity, name, category, stage, is_national, expected_kind):
    match = {"id": "other-source-match", "competition_id": identity, "competition": name, "category": category,
             "opponent": "Neutral Team", "stage": stage, "rating": 8, "is_national": is_national}

    context = resolve_match_context(match, [])

    assert context["match_context_status"] == "matched"
    assert context["competition_kind"] == expected_kind
    assert context["category"] == category
    assert context["is_national"] is is_national
