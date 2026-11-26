"""Real 2024/25 saved matches whose two providers spell the opponent differently."""

from copy import deepcopy

import pytest

from season_ledger.domain import combine
from season_ledger.match_context import normalize_team
from season_ledger.weighting import calculate_weighted_rating


PLAYERS = {
    "yamal": ("937958", "Lamine Yamal", "RW", "FC Barcelona", "Barcelona"),
    "haaland": ("418560", "Erling Haaland", "CF", "Manchester City", "Manchester City"),
    "mbappe": ("342229", "Kylian Mbappé", "CF", "Real Madrid", "Real Madrid"),
    "wirtz": ("598577", "Florian Wirtz", "AM", "Bayer 04 Leverkusen", "Bayer Leverkusen"),
    "musiala": ("580195", "Jamal Musiala", "AM", "FC Bayern Munich", "Bayern München"),
    "neves": ("670681", "João Neves", "CM", "Paris Saint-Germain", "Paris Saint-Germain"),
    "alvarez": ("576024", "Julián Álvarez", "CF", "Atlético de Madrid", "Atlético Madrid"),
    "lautaro": ("406625", "Lautaro Martínez", "CF", "Inter Milan", "Inter"),
    "sesko": ("627442", "Benjamin Šeško", "CF", "RB Leipzig", "RB Leipzig"),
    "williams": ("709187", "Nico Williams", "LW", "Athletic Bilbao", "Athletic Club"),
    "baena": ("548111", "Álex Baena", "LW", "Villarreal CF", "Villarreal"),
    "woltemade": ("455661", "Nick Woltemade", "CF", "VfB Stuttgart", "VfB Stuttgart"),
}
COMPETITIONS = {
    "CL": ("UEFA Champions League", "42", "Champions League", "continental", 10, "league", 1.5),
    "DFB": ("DFB-Pokal", "209", "DFB Pokal", "domestic_cup", 8, "playoff", 1),
    "KLUB": ("FIFA Club World Cup", "78", "FIFA Club World Cup", "continental", 10, "group", 1),
    "CDR": ("Copa del Rey", "138", "Copa del Rey", "domestic_cup", 8, "playoff", 1),
    "EL": ("UEFA Europa League", "73", "Europa League", "continental", 10, "league", 1),
}
# player, tournament, TM ID, FotMob ID, date, TM opponent, FotMob opponent, TM stage, rating
CASES = [
    ("yamal", "CL", "4445065", "4621536", "2025-01-29", "Atalanta BC", "Atalanta", "GP", 8.6),
    ("haaland", "CL", "4445039", "4621494", "2025-01-29", "Club Brugge KV", "Club Brugge", "GP", 7.8),
    ("haaland", "CL", "4445045", "4621498", "2024-10-23", "AC Sparta Praha", "Sparta Prague", "GP", 9.1),
    ("mbappe", "CL", "4445144", "4621549", "2025-01-29", "Stade Brestois 29", "Brest", "GP", 6.8),
    ("mbappe", "CL", "4445120", "4621547", "2024-10-02", "LOSC Lille", "Lille", "GP", 6.0),
    ("musiala", "CL", "4445042", "4621512", "2024-09-17", "GNK Dinamo Zagreb", "Dinamo Zagreb", "GP", 9.0),
    ("wirtz", "DFB", "4446799", "4623970", "2024-10-29", "SV 07 Elversberg", "Elversberg", "2R", 6.1),
    ("musiala", "DFB", "4353910", "4490625", "2024-08-16", "SSV Ulm 1846", "Ulm", "1R", 6.2),
    ("neves", "KLUB", "4506835", "4685716", "2025-06-20", "Botafogo de Futebol e Regatas", "Botafogo", "B", 7.1),
    ("alvarez", "CDR", "4500191", "4682067", "2024-12-05", "CP Cacereño", "Cacereño", "2R", 8.5),
    ("lautaro", "KLUB", "4506538", "4685736", "2025-06-26", "CA River Plate", "River Plate", "E", 7.6),
    ("sesko", "DFB", "4353906", "4490621", "2024-08-17", "Rot-Weiss Essen", "RW Essen", "1R", 8.7),
    ("williams", "EL", "4445414", "4622145", "2024-11-28", "IF Elfsborg", "Elfsborg", "GP", 8.0),
    ("baena", "CDR", "4472358", "4656446", "2024-10-29", "UD Poblense", "Poblense", "1R", 6.3),
    ("woltemade", "DFB", "4446793", "4623966", "2024-10-29", "1.FC Kaiserslautern", "Kaiserslautern", "2R", 7.1),
]


def saved_sources(case):
    player, competition, tm_id, fm_id, day, official, short, code, rating = case
    identity, name, position, tm_team, fm_team = PLAYERS[player]
    tm_name, fm_competition, fm_name, category, type_id, _, _ = COMPETITIONS[competition]
    tm = {"profile": {"id": identity, "name": name, "position": position}, "competitions": [{
        "id": competition, "name": tm_name, "category": category, "competition_type_id": type_id,
        "team": tm_team, "games": 1, "season_id": "2024", "season_ids": ["2024"],
        "matches": [{"id": tm_id, "date": day, "team": tm_team, "opponent": official,
                     "competition_id": competition, "season_id": "2024", "stage_code": code}],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": fm_competition, "name": fm_name, "category": category, "team": fm_team,
        "games": 1, "rated_games": 1, "rating": rating,
        "matches": [{"id": fm_id, "date": day, "team": fm_team, "opponent": short,
                     "competition_id": fm_competition, "competition": fm_name, "category": category,
                     "is_national": False, "stage": None, "rating": rating}],
    }]}
    return tm, fm


@pytest.mark.parametrize("case", CASES, ids=[case[5] for case in CASES])
def test_saved_opponent_names_resolve_missing_stage_and_preserve_ratings(case):
    tm, fm = saved_sources(case)
    original = deepcopy((tm, fm))
    assert normalize_team(case[5]) == normalize_team(case[6])

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert snapshot["competitions"][0]["matches"][0]["id"] == case[2]
    assert snapshot["competitions"][0]["fotmob_matches"][0]["id"] == case[3]
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 1
    assert weighted["unresolved_match_ids"] == []
    assert weighted["missing_source_samples"] == []
    assert weighted["raw_rating"] == pytest.approx(case[8])
    expected_stage, expected_weight = COMPETITIONS[case[1]][-2:]
    assert weighted["total_weight"] == expected_weight
    match = weighted["matches"][0]
    assert match["id"] == case[3]
    assert match["rating"] == match["raw_rating"] == case[8]
    assert match["stage"] is None
    assert match["context"]["competition_id"] == case[1]
    assert match["context"]["stage_code"] == case[7]
    assert match["context"]["stage"] == expected_stage
    assert match["context"]["match_context_status"] == "matched"
    assert match["context"]["is_national"] is False
    assert match["context"]["is_knockout"] is (expected_stage == "playoff")
    assert match["weight"] == expected_weight
    expected_rules = {"base", "champions_league"} if case[1] == "CL" else {"base"}
    assert {rule["key"] for rule in match["weight_rules"]} == expected_rules


@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_verified_alias_does_not_match_a_different_age_womens_or_reserve_team(suffix):
    tm, fm = saved_sources(CASES[0])
    tm["competitions"][0]["matches"][0]["opponent"] += suffix

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "incomplete_context"
    assert weighted["unresolved_match_ids"] == [CASES[0][3]]
    assert weighted["included_games"] == 0
    assert weighted["matches"][0]["context"]["match_context_status"] == "unmatched"
