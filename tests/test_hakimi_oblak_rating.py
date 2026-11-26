from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.match_context import normalize_team, resolve_match_context
from season_ledger.ratings import apply_position_adjustment
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def affected_sources(player):
    if player == "oblak":
        profile = {"id": "121483", "name": "Jan Oblak", "position": "GK"}
        rows = [("CL", "UEFA Champions League", "continental", 10, "Atlético de Madrid", "Atlético Madrid", "42", "Champions League", [
            ("4171272", "4315040", "2023-11-28", "Feyenoord Rotterdam", "Feyenoord", "E", 6.4),
            ("4171275", "4315034", "2023-10-04", "Feyenoord Rotterdam", "Feyenoord", "E", 8.1),
        ])]
    else:
        profile = {"id": "398073", "name": "Achraf Hakimi", "position": "RB"}
        rows = [
            ("AFCN", "Africa Cup of Nations", "national_major", 11, "Morocco", "Morocco", "289", "Africa Cup of Nations", [
                ("4225480", "4346235", "2024-01-21", "Democratic Republic of the Congo", "DR Congo", "F", 8.1),
            ]),
            ("WMQ2", "World Cup Qualification Africa", "national_regular", 19, "Morocco", "Morocco", "10196", "World Cup Qualification CAF", [
                ("4350448", "4341217", "2024-06-11", "Republic of the Congo", "Congo", "E", 8.2),
            ]),
        ]
    tm, fm = {"profile": profile, "competitions": []}, {"status": "matched", "competitions": []}
    for identity, name, category, kind, team, fm_team, fm_id, fm_name, matches in rows:
        tm["competitions"].append({
            "id": identity, "name": name, "category": category, "competition_type_id": kind,
            "team": team, "games": len(matches),
            "matches": [{"id": tm_id, "date": day, "team": team, "opponent": official, "stage_code": code}
                        for tm_id, _, day, official, _, code, _ in matches],
        })
        fm["competitions"].append({
            "id": fm_id, "name": fm_name, "category": category, "team": fm_team,
            "games": len(matches), "rating": sum(match[6] for match in matches) / len(matches), "rated_games": len(matches),
            "matches": [{"id": fm_match_id, "date": day, "team": fm_team, "opponent": short,
                         "competition_id": fm_id, "competition": fm_name, "category": category,
                         "is_national": player != "oblak", "stage": None, "rating": rating}
                        for _, fm_match_id, day, _, short, _, rating in matches],
        })
    return tm, fm


@pytest.mark.parametrize("player,weight,raw,coefficient", [
    ("hakimi", 1, 8.15, .9811), ("oblak", 1.5, 7.25, .9253),
])
@pytest.mark.parametrize("legacy", [False, True])
def test_real_congo_and_feyenoord_aliases_preserve_source_ratings_and_group_weights(player, weight, raw, coefficient, legacy):
    tm, fm = affected_sources(player)
    if legacy:
        for row in fm["competitions"]:
            for match in row["matches"]:
                match.pop("category")
                match.pop("is_national")
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(apply_position_adjustment(snapshot))

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 2
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == weight * 2
    assert weighted["raw_rating"] == pytest.approx(raw)
    assert weighted["rating"] == pytest.approx(raw / coefficient)
    for match in weighted["matches"]:
        assert match["context"]["stage"] == "group"
        assert match["weight"] == weight
        assert {rule["key"] for rule in match["weight_rules"]} == (
            {"base", "champions_league"} if player == "oblak" else {"base"})


def test_dr_congo_and_congo_remain_distinct_countries_and_cannot_cross_match():
    assert normalize_team("Democratic Republic of the Congo") == normalize_team("DR Congo")
    assert normalize_team("Republic of the Congo") == normalize_team("Congo")
    assert normalize_team("DR Congo") != normalize_team("Congo")
    tm, fm = affected_sources("hakimi")
    for index in (0, 1):
        row = tm["competitions"][index]
        match = deepcopy(fm["competitions"][index]["matches"][0])
        match["opponent"] = "Congo" if index == 0 else "DR Congo"
        assert resolve_match_context(match, [row], parent=row)["match_context_status"] == "unmatched"


@pytest.mark.parametrize("player", ["hakimi", "oblak"])
@pytest.mark.parametrize("suffix", [" U23", " Women", " B"])
def test_official_opponent_aliases_do_not_collapse_distinct_teams(player, suffix):
    tm, fm = affected_sources(player)
    row = tm["competitions"][0]
    row["matches"][0]["opponent"] += suffix

    context = resolve_match_context(fm["competitions"][0]["matches"][0], [row], parent=row)

    assert context["match_context_status"] == "unmatched"


@pytest.mark.parametrize("identity,competition,category,stage,opponent,expected_weight,expected_keys", [
    ("66", "Summer Olympics", "other", "1/2", "Spain U23", 2, {"base", "semifinal"}),
    ("66", "Summer Olympics", "other", "bronze", "Egypt U23", 1, {"base"}),
    ("44", "Copa America", "national_major", "1/2", "Brazil", 3, {"base", "semifinal", "grand_national_opponent", "euro_copa"}),
    ("50", "EURO", "national_major", "1/2", "Spain", 3, {"base", "semifinal", "grand_national_opponent", "euro_copa"}),
])
def test_olympic_medal_rounds_do_not_receive_copa_or_euro_additions(identity, competition, category, stage, opponent, expected_weight, expected_keys):
    snapshot = {"position": "RB", "competitions": [{
        "id": "OLYM" if identity == "66" else "COPA" if identity == "44" else "EURO",
        "name": competition, "category": category, "team": "Morocco U23" if identity == "66" else "Senior National Team",
        "games": 1, "rating": 8, "rated_games": 1,
        "fotmob_matches": [{"id": "4451343" if stage == "bronze" else "4451342", "competition_id": identity,
                            "competition": competition, "category": category, "opponent": opponent, "stage": stage, "rating": 8}],
    }]}

    result = calculate_weighted_rating(snapshot)

    assert result["status"] == "complete"
    assert result["total_weight"] == expected_weight
    assert {rule["key"] for rule in result["matches"][0]["weight_rules"]} == expected_keys
    if stage == "bronze":
        assert result["matches"][0]["context"]["stage"] == "third_place"


def test_saved_oblak_sources_recalculate_without_dropping_either_feyenoord_rating(tmp_path):
    tm, fm = affected_sources("oblak")
    # The compact rating sample still retains the actual ten-game CL result.
    tm["competitions"][0].update(games=10, stage_code="VFR")
    snapshot = combine(tm, fm)
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "121483", snapshot, tm, fm)

    first = store.player(identity)
    reopened = Store(store.path).player(identity)

    assert first["weighted_rating"]["status"] == "complete"
    assert {match["id"] for match in first["weighted_rating"]["matches"]} == {"4315040", "4315034"}
    assert reopened["weighted_rating"] == first["weighted_rating"]
    assert reopened["updated_at"] == first["updated_at"]
    assert first["season_points"]["status"] == "complete"
    assert first["season_points"]["bonus_points"] == 0
    assert {bonus["kind"]: bonus["points"] for bonus in first["season_points"]["bonuses"]} == {
        "champions_league": 12, "no_national_team": -12,
    }
    assert first["season_points"]["total"] == pytest.approx(7.25 / .9253 * 100)
    assert store.raw(identity) == (tm, fm)


def add_hakimi_olympics(tm, fm):
    # TM IDs/stages/names were checked against the live SDK performance feed.
    games = [
        ("4317054", "4451294", "2024-07-24", "Argentina Olympic Team", "Argentina U23", "B", None, 7.7),
        ("4317061", "4451297", "2024-07-27", "Ukraine Olympic Team", "Ukraine U23", "B", None, 7.1),
        ("4317069", "4451299", "2024-07-30", "Iraq Olympic Team", "Iraq U23", "B", None, 8.7),
        ("4397264", "4451338", "2024-08-02", "United States Olympic Team", "USA U23", "VF", "1/4", 8.3),
        ("4397277", "4451342", "2024-08-05", "Spain Olympic Team", "Spain U23", "HF", "1/2", 7.5),
        ("4397279", "4451343", "2024-08-08", "Egypt Olympic Team", "Egypt U23", "PD", "bronze", 8.1),
    ]
    tm["competitions"].append({
        "id": "OLYM", "name": "Olympic Games", "category": "other", "competition_type_id": 17,
        "team": "Morocco Olympic Team", "team_id": "53820", "season_id": "2023",
        "games": 6, "stage_code": "PD",
        "matches": [{"id": tm_id, "date": day, "team": "Morocco Olympic Team", "opponent": official,
                     "stage_code": code} for tm_id, _, day, official, _, code, _, _ in games],
    })
    fm["competitions"].append({
        "id": "66", "name": "Summer Olympics", "category": "national_major", "team": "Morocco U23",
        "games": 6, "rating": 7.9, "rated_games": 6,
        "matches": [{"id": fm_id, "date": day, "team": "Morocco U23", "opponent": short,
                     "competition_id": "66", "competition": "Summer Olympics", "category": "national_major",
                     "is_national": True, "stage": stage, "rating": rating}
                    for _, fm_id, day, _, short, _, stage, rating in games],
    })


def test_six_real_hakimi_olympic_matches_join_without_major_tournament_additions():
    tm, fm = affected_sources("hakimi")
    add_hakimi_olympics(tm, fm)
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(apply_position_adjustment(snapshot))

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == weighted["history_games"] == 8
    assert weighted["duplicate_games"] == 0
    assert weighted["total_weight"] == 9
    assert weighted["raw_rating"] == pytest.approx((8.1 + 8.2 + 7.7 + 7.1 + 8.7 + 8.3 + 7.5 * 2 + 8.1) / 9)
    source_ids = {match["id"] for row in fm["competitions"] for match in row["matches"]}
    assert {match["id"] for match in weighted["matches"]} == source_ids
    olympics = next(row for row in snapshot["competitions"] if row["id"] == "OLYM")
    assert olympics["category"] == "other"
    assert olympics["rated_games"] == 6
    assert len(olympics["fotmob_matches"]) == 6
    olympic_matches = [match for match in weighted["matches"] if match.get("competition_id") == "66"]
    assert [match["weight"] for match in olympic_matches] == [1, 1, 1, 1, 2, 1]
    assert [match["context"]["stage"] for match in olympic_matches] == [
        "group", "group", "group", "quarterfinal", "semifinal", "third_place",
    ]
    for match in olympic_matches:
        assert {rule["key"] for rule in match["weight_rules"]}.isdisjoint({
            "euro_copa", "world_cup", "major_final", "grand_national_opponent",
        })


def test_hakimi_friendlies_source_gap_is_visible_without_inventing_a_sierra_leone_rating(tmp_path):
    tm, fm = affected_sources("hakimi")
    add_hakimi_olympics(tm, fm)
    friendlies = [
        ("4174583", "4312714", "2023-09-12", "Burkina Faso", None),
        ("4198421", "4301285", "2023-10-14", "Ivory Coast", None),
        ("4260433", None, "2024-01-11", "Sierra Leone", None),
        ("4312223", "4433663", "2024-03-22", "Angola", 8.0),
        ("4312224", "4433664", "2024-03-26", "Mauritania", 7.7),
    ]
    tm["competitions"].append({
        "id": "FS", "name": "International Friendlies", "category": "national_regular", "competition_type_id": 11,
        "team": "Morocco", "games": 5,
        "matches": [{"id": tm_id, "date": day, "team": "Morocco", "opponent": opponent}
                    for tm_id, _, day, opponent, _ in friendlies],
    })
    fm["competitions"].append({
        "id": "114", "name": "Friendlies", "category": "national_regular", "team": "Morocco",
        "games": 4, "rating": 7.85, "rated_games": 2,
        "matches": [{"id": fm_id, "date": day, "team": "Morocco", "opponent": opponent,
                     "competition_id": "114", "competition": "Friendlies", "stage": None, "rating": rating}
                    for _, fm_id, day, opponent, rating in friendlies if fm_id is not None],
    })
    original = deepcopy((tm, fm))
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "398073", combine(tm, fm), tm, fm)

    player = store.player(identity)

    assert player["weighted_rating"]["status"] == "complete"
    assert player["weighted_rating"]["rated_games"] == 10
    assert player["weighted_rating"]["unrated_games"] == 2
    assert player["weighted_rating"]["history_games"] == 12
    assert any("International Friendlies" in message and "5" in message and "4" in message for message in player["warnings"])
    assert all(match.get("opponent") != "Sierra Leone" for match in player["weighted_rating"]["matches"])
    assert {match["id"] for match in player["weighted_rating"]["matches"]} == {
        match["id"] for row in fm["competitions"] for match in row["matches"]
    }
    assert player["season_points"]["bonus_points"] == -25
    assert {bonus["kind"]: bonus["points"] for bonus in player["season_points"]["bonuses"]} == {"no_champions_league": -25}
    assert store.raw(identity) == original
