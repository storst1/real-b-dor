from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.match_context import resolve_match_context
from season_ledger.providers.fotmob import _aggregate_matches
from season_ledger.ratings import apply_position_adjustment
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def affected_sources(player):
    if player == "salah":
        profile = {"id": "148455", "name": "Mohamed Salah", "position": "RW"}
        competition_id, name, fm_id, fm_name = "EL", "UEFA Europa League", "73", "Europa League"
        team, fm_team, opponent, fm_opponent, code = "Liverpool FC", "Liverpool", "Union Saint-Gilloise", "Union St.Gilloise", "E"
        games = [("4172111", "4315083", "2023-10-05", 6.7)]
    else:
        profile = ({"id": "126414", "name": "Hakan Calhanoglu", "position": "DM"} if player == "hakan"
                   else {"id": "406625", "name": "Lautaro Martínez", "position": "CF"})
        competition_id, name, fm_id, fm_name = "CL", "UEFA Champions League", "42", "Champions League"
        team, fm_team, opponent, fm_opponent, code = "Inter Milan", "Inter", "Red Bull Salzburg", "Salzburg", "D"
        ratings = (7.2, 8.6) if player == "hakan" else (7.5, 6.9)
        games = [("4171267", "4315063", "2023-11-08", ratings[0]),
                 ("4171264", "4315060", "2023-10-24", ratings[1])]
    tm = {"profile": profile, "competitions": [{
        "id": competition_id, "name": name, "category": "continental", "competition_type_id": 10,
        "team": team, "games": len(games),
        "matches": [{"id": tm_id, "date": day, "team": team, "opponent": opponent, "stage_code": code}
                    for tm_id, _, day, _ in games],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": fm_id, "name": fm_name, "category": "continental", "team": fm_team,
        "games": len(games), "rated_games": len(games), "rating": sum(game[3] for game in games) / len(games),
        "matches": [{"id": fm_match_id, "date": day, "team": fm_team, "opponent": fm_opponent,
                     "competition_id": fm_id, "competition": fm_name, "category": "continental",
                     "is_national": False, "stage": None, "rating": rating}
                    for _, fm_match_id, day, rating in games],
    }]}
    return tm, fm


@pytest.mark.parametrize("player,weight,raw,coefficient", [
    ("salah", 1, 6.7, 1.0096), ("hakan", 1.5, 7.9, 1.0021), ("lautaro", 1.5, 7.2, 1),
])
@pytest.mark.parametrize("legacy", [False, True])
def test_real_union_and_salzburg_group_matches_recover_source_rounds(player, weight, raw, coefficient, legacy):
    tm, fm = affected_sources(player)
    if legacy:
        for match in fm["competitions"][0]["matches"]:
            match.pop("category")
            match.pop("is_national")
    original_tm, original_fm = deepcopy(tm), deepcopy(fm)

    result = calculate_weighted_rating(apply_position_adjustment(combine(tm, fm)))

    assert (tm, fm) == (original_tm, original_fm)
    assert result["status"] == "complete"
    count = len(fm["competitions"][0]["matches"])
    assert result["rated_games"] == result["included_games"] == count
    assert result["total_weight"] == weight * count
    assert result["raw_rating"] == pytest.approx(raw)
    assert result["rating"] == pytest.approx(raw / coefficient)
    for match in result["matches"]:
        assert match["weight"] == weight
        assert match["context"]["stage"] == "group"
        assert match["context"]["stage_code"] == ("E" if player == "salah" else "D")
        assert match["context"]["is_knockout"] is False
        assert {rule["key"] for rule in match["weight_rules"]} == (
            {"base"} if player == "salah" else {"base", "champions_league"})


@pytest.mark.parametrize("player", ["salah", "hakan"])
@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_union_and_salzburg_aliases_preserve_distinct_opponent_teams(player, suffix):
    tm, fm = affected_sources(player)
    row = tm["competitions"][0]
    row["matches"][0]["opponent"] += suffix

    context = resolve_match_context(fm["competitions"][0]["matches"][0], [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


@pytest.mark.parametrize("player", ["salah", "hakan"])
@pytest.mark.parametrize("unknown_stage", [None, "Unknown phase"])
def test_opponent_alias_does_not_invent_a_missing_or_unknown_group_stage(player, unknown_stage):
    tm, fm = affected_sources(player)
    for match in tm["competitions"][0]["matches"]:
        match["stage_code"] = unknown_stage

    result = calculate_weighted_rating(apply_position_adjustment(combine(tm, fm)))

    assert result["status"] == "incomplete_context"
    assert result["rating"] is result["raw_rating"] is None
    assert all(match["weight"] is None for match in result["matches"])


@pytest.mark.parametrize("player", ["salah", "hakan"])
def test_conflicting_opponent_still_blocks_parent_context(player):
    tm, fm = affected_sources(player)
    row = tm["competitions"][0]
    fm["competitions"][0]["matches"][0]["opponent"] = "Different Club"

    context = resolve_match_context(fm["competitions"][0]["matches"][0], [row], parent=row)

    assert context["match_context_status"] == "unmatched"
    assert context["competition_id"] is None


def add_legacy_lautaro_coppa(tm, fm):
    tm["competitions"].append({
        "id": "CIT", "name": "Italy Cup", "category": "domestic_cup", "competition_type_id": 8,
        "team": "Inter Milan", "games": 1, "stage_code": "AF",
        "matches": [{"id": "4221297", "date": "2023-12-20T20:00:00+00:00", "team": "Inter Milan",
                     "opponent": "Bologna FC 1909", "stage_code": "AF"}],
    })
    fm["competitions"].append({
        "id": "141", "name": "Coppa Italia", "category": "domestic_league", "team": "Inter",
        "games": 1, "rating": 6.6, "rated_games": 1,
        "matches": [{"id": "4301267", "date": "2023-12-20", "competition_id": "141", "competition": "Coppa Italia",
                     "category": "domestic_league", "is_national": False, "team": "Inter",
                     "opponent": "Bologna", "stage": "1/8", "rating": 6.6}],
    })


def test_coppa_italia_player_history_imports_as_cup_including_match_metadata():
    matches = [{
        "id": 4301267, "matchDate": {"utcTime": "2023-12-20T20:00:00Z"},
        "leagueId": 141, "leagueName": "Coppa Italia", "teamId": 8636, "teamName": "Inter",
        "opponentTeamId": 9857, "opponentTeamName": "Bologna", "stage": "1/8",
        "ratingProps": {"rating": 6.6}, "minutesPlayed": 99, "playedInMatch": True,
    }]

    competitions = _aggregate_matches(matches, {}, Season(2023))

    assert len(competitions) == 1
    assert competitions[0]["category"] == "domestic_cup"
    assert competitions[0]["matches"][0]["category"] == "domestic_cup"
    assert competitions[0]["rated_games"] == 1


def test_combine_restores_misclassified_coppa_without_changing_saved_sources():
    tm, fm = affected_sources("lautaro")
    add_legacy_lautaro_coppa(tm, fm)
    original_tm, original_fm = deepcopy(tm), deepcopy(fm)

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(apply_position_adjustment(snapshot))

    assert (tm, fm) == (original_tm, original_fm)
    assert snapshot["warnings"] == []
    cup = next(row for row in snapshot["competitions"] if row["id"] == "CIT")
    assert cup["category"] == "domestic_cup"
    assert cup["rated_games"] == 1
    assert cup["fotmob_matches"][0]["id"] == "4301267"
    assert cup["fotmob_matches"][0]["category"] == "domestic_cup"
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 3
    assert weighted["total_weight"] == 4
    assert weighted["rating"] == pytest.approx((7.5 * 1.5 + 6.9 * 1.5 + 6.6) / 4)
    assert next(match for match in weighted["matches"] if match["id"] == "4301267")["weight"] == 1


def test_store_recovers_lautaro_coppa_from_raw_sources_without_reimport(tmp_path):
    tm, fm = affected_sources("lautaro")
    add_legacy_lautaro_coppa(tm, fm)
    tm["competitions"].extend([
        {"id": "IT1", "name": "Serie A", "category": "domestic_league", "games": 33,
         "team": "Inter Milan", "place": "1", "competition_complete": True},
        {"id": "COPA", "name": "Copa América", "category": "national_major", "games": 6,
         "team": "Argentina", "place": "1", "final_outcome": "winner"},
        {"id": "SCI", "name": "Supercoppa Italiana", "category": "domestic_cup", "games": 2,
         "team": "Inter Milan", "competition_type_id": 9, "place": "1", "final_outcome": "winner"},
    ])
    stale = combine(tm, fm)
    cup = next(row for row in stale["competitions"] if row["id"] == "CIT")
    cup.update(rating=None, rated_games=None, sources=["Transfermarkt"])
    cup.pop("fotmob_matches", None)
    stale["warnings"] = ["Оценка FotMob для «Coppa Italia» не включена: турнир или команда не сопоставлены однозначно."]
    original_tm, original_fm = deepcopy(tm), deepcopy(fm)
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "406625", stale, tm, fm)

    restored = store.player(identity)
    repeated = store.player(identity)

    assert restored["warnings"] == []
    assert restored["weighted_rating"]["status"] == "complete"
    assert restored["weighted_rating"]["rated_games"] == restored["weighted_rating"]["included_games"] == 3
    assert "4301267" in {match["id"] for match in restored["weighted_rating"]["matches"]}
    assert restored["season_points"]["status"] == "complete"
    assert {bonus["kind"]: bonus["points"] for bonus in restored["season_points"]["bonuses"]} == {"league": 75, "euro_copa": 150}
    assert restored["season_points"]["total"] == pytest.approx(restored["weighted_rating"]["rating"] * 100 + 225)
    assert repeated["weighted_rating"] == restored["weighted_rating"]
    assert repeated["updated_at"] == restored["updated_at"]
    assert store.raw(identity) == (original_tm, original_fm)
