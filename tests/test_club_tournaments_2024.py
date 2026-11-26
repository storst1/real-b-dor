from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.match_context import normalize_team, resolve_match_context
from season_ledger.points import calculate_season_points
from season_ledger.providers.fotmob import _aggregate_matches
from season_ledger.providers.transfermarkt import _selected
from season_ledger.store import Store
from season_ledger.weighting import calculate_weighted_rating


def club_sources(kind="hilal"):
    if kind == "boca":
        profile = {"id": "580195", "name": "Jamal Musiala", "position": "AM"}
        team, fm_team, official, short = "FC Bayern Munich", "Bayern München", "CA Boca Juniors", "Boca Juniors"
        identity, name, fm_id = "KLUB", "FIFA Club World Cup", "78"
        tm_id, source_id, day, code, stage, rating = "4506539", "4685722", "2025-06-21", "C", None, 6.1
    elif kind == "intercontinental":
        profile = {"id": "371998", "name": "Vinicius Junior", "position": "RW"}
        team = fm_team = "Real Madrid"
        official, short = "CF Pachuca", "Pachuca"
        identity, name, fm_id = "FIC1", "FIFA Intercontinental Cup", "10703"
        tm_id, source_id, day, code, stage, rating = "4462645", "4683431", "2024-12-18", "FF", "final", 8.9
    else:
        profile = {"id": "581678", "name": "Jude Bellingham", "position": "AM"}
        team = fm_team = "Real Madrid"
        official, short = "Al-Hilal SFC", "Al Hilal"
        identity, name, fm_id = "KLUB", "FIFA Club World Cup", "78"
        tm_id, source_id, day, code, stage, rating = "4504592", "4685750", "2025-06-18", "H", None, 7.0
    tm = {"profile": profile, "competitions": [{
        "id": identity, "name": name, "category": "continental", "competition_type_id": 10,
        "team": team, "games": 1, "season_id": "2024", "season_ids": ["2024"],
        "matches": [{"id": tm_id, "date": day, "team": team, "opponent": official,
                     "competition_id": identity, "season_id": "2024", "stage_code": code}],
    }]}
    fm = {"status": "matched", "competitions": [{
        "id": fm_id, "name": name, "category": "continental", "team": fm_team,
        "games": 1, "rated_games": 1, "rating": rating,
        "matches": [{"id": source_id, "date": day, "competition_id": fm_id, "competition": name,
                     "category": "continental", "is_national": False, "team": fm_team,
                     "opponent": short, "stage": stage, "rating": rating}],
    }]}
    return tm, fm


@pytest.mark.parametrize("kind,weight,stage", [("hilal", 1, "group"), ("boca", 1, "group"),
                                               ("intercontinental", 3, "final")])
@pytest.mark.parametrize("legacy", [False, True])
def test_real_club_world_cup_and_intercontinental_matches_keep_their_own_rules(kind, weight, stage, legacy):
    tm, fm = club_sources(kind)
    if legacy:
        fm["competitions"][0]["matches"][0].pop("category")
        fm["competitions"][0]["matches"][0].pop("is_national")
    original = deepcopy((tm, fm))

    snapshot = combine(tm, fm)
    weighted = calculate_weighted_rating(snapshot)

    assert (tm, fm) == original
    assert snapshot["warnings"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 1
    assert weighted["total_weight"] == weight
    match = weighted["matches"][0]
    assert match["context"]["stage"] == stage
    assert match["context"]["is_national"] is False
    assert match["weight"] == weight
    keys = {rule["key"] for rule in match["weight_rules"]}
    assert keys == ({"base", "final"} if kind == "intercontinental" else {"base"})
    assert not snapshot["competitions"][0]["is_champions_league"]
    assert not snapshot["competitions"][0]["is_supercup"]


def test_intercontinental_final_is_fully_resolved_by_fotmob_without_tm_comparison():
    tm, fm = club_sources("intercontinental")
    tm["competitions"][0]["matches"] = []

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "complete"
    assert weighted["total_weight"] == 3
    assert weighted["matches"][0]["context"]["competition_provider"] == "fotmob"
    assert weighted["matches"][0]["context"]["competition_id"] == "10703"


def test_real_club_world_cup_psg_semifinal_has_general_stage_and_opponent_additions_only():
    tm, fm = club_sources()
    tm["competitions"][0]["matches"] = []
    match = fm["competitions"][0]["matches"][0]
    match.update(id="4685782", date="2025-07-09", opponent="Paris Saint-Germain", stage="1/2", rating=7.2)

    weighted = calculate_weighted_rating(combine(tm, fm))

    assert weighted["status"] == "complete"
    assert weighted["total_weight"] == 2.5
    assert {rule["key"] for rule in weighted["matches"][0]["weight_rules"]} == {
        "base", "semifinal", "grand_club_opponent",
    }


@pytest.mark.parametrize("kind", ["hilal", "boca"])
@pytest.mark.parametrize("suffix", [" U19", " Women", " B"])
def test_verified_club_aliases_preserve_age_womens_and_reserve_team_identity(kind, suffix):
    tm, fm = club_sources(kind)
    row = tm["competitions"][0]
    source = fm["competitions"][0]["matches"][0]
    assert normalize_team(row["matches"][0]["opponent"]) == normalize_team(source["opponent"])
    row["matches"][0]["opponent"] += suffix

    context = resolve_match_context(source, [row], parent=row)

    assert context["match_context_status"] == "unmatched"


def test_cwc_and_intercontinental_wins_do_not_award_cl_or_domestic_cup_trophy_bonuses():
    tm, fm = club_sources("intercontinental")
    snapshot = combine(tm, fm)
    snapshot["competitions"][0].update(place="1", final_outcome="winner", games=6)
    snapshot["competitions"].extend([
        {"id": "KLUB", "name": "FIFA Club World Cup", "category": "continental",
         "competition_type_id": 10, "games": 6, "place": "1", "final_outcome": "winner"},
        {"id": "CL", "name": "UEFA Champions League", "category": "continental", "games": 1},
        {"id": "FS", "name": "International Friendlies", "category": "national_regular", "games": 1},
    ])

    points = calculate_season_points(snapshot, weighted_rating=8)

    assert points["status"] == "complete"
    assert points["bonus_points"] == 0
    assert points["bonuses"] == []


def test_july_2025_cwc_game_stays_in_the_2024_source_season_and_fotmob_history():
    game = {"gameInformation": {"gameId": "4506865", "competitionId": "KLUB", "competitionTypeId": 10,
                               "seasonId": 2024, "date": {"dateTimeUTC": "2025-07-01T01:00:00Z"},
                               "gameState": "extra_time"}}
    competition = {"KLUB": {"name": "FIFA Club World Cup", "typeId": 10}}
    assert _selected(game, Season(2024), competition)
    assert not _selected(game, Season(2025), competition)
    assert _selected(game, Season(2025, calendar=True), competition)
    tm = [{"id": "KLUB", "category": "continental", "team": "Manchester City",
           "matches": [{"id": "4506865", "date": "2025-07-01T01:00:00Z"}]}]
    source = [{"id": 4685772, "matchDate": {"utcTime": "2025-07-01T01:00:00Z"},
               "leagueId": 78, "leagueName": "FIFA Club World Cup", "teamId": 8456,
               "teamName": "Manchester City", "opponentTeamName": "Al Hilal", "stage": "1/8",
               "playedInMatch": True, "minutesPlayed": 90, "ratingProps": {"rating": 7.7}}]

    rows = _aggregate_matches(source, {}, Season(2024), tm)

    assert len(rows) == 1
    assert rows[0]["rated_games"] == rows[0]["games"] == 1
    assert rows[0]["matches"][0]["id"] == "4685772"
    assert rows[0]["rating"] == 7.7


def test_saved_cwc_alias_recalculates_without_import_and_preserves_source_data(tmp_path):
    tm, fm = club_sources()
    original = deepcopy((tm, fm))
    store = Store(tmp_path / "seasons.sqlite3")
    store.add_season(Season(2024))
    identity = store.save_player("2024-25", tm["profile"]["id"], combine(tm, fm), tm, fm)

    first = store.player(identity)
    reopened = Store(store.path).player(identity)

    assert first["weighted_rating"]["status"] == "complete"
    assert first["weighted_rating"]["total_weight"] == 1
    assert first["weighted_rating"]["rating"] == pytest.approx(7 / 1.0158)
    assert reopened["weighted_rating"] == first["weighted_rating"]
    assert reopened["updated_at"] == first["updated_at"]
    assert store.raw(identity) == original
