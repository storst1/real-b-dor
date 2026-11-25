from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.league_outcomes import apply_league_championship
from season_ledger.points import calculate_season_points
from season_ledger.store import Store


def mls_phases():
    """The real decisive Miami/Atlanta series: wins, not aggregate goals."""
    return [
        {"id": "MLS1", "name": "Major League Soccer", "category": "domestic_league",
         "competition_type_id": 1, "team": "Inter Miami CF", "team_id": "69261",
         "season_id": "2023", "season_ids": ["2023"], "games": 1,
         "goals": 0, "assists": 1, "minutes": 90,
         "place": "1", "competition_complete": None,
         "matches": [{"id": "4248397", "date": "2024-02-22T01:00:00+00:00",
                      "team_id": "69261", "team": "Inter Miami CF", "opponent": "Real Salt Lake City"}]},
        {"id": "POUS", "name": "MLS Cup Playoffs", "category": "domestic_league",
         "competition_type_id": 12, "team": "Inter Miami CF", "team_id": "69261",
         "season_id": "2023", "season_ids": ["2023"], "games": 3,
         "goals": 1, "assists": 1, "minutes": 270,
         "place": None, "stage_code": "1RE", "competition_complete": None,
         "matches": [
             {"id": "4479032", "date": "2024-10-26T00:30:00+00:00", "stage_code": "1RH",
              "competition_id": "POUS", "season_id": "2023", "team_id": "69261",
              "team": "Inter Miami CF", "opponent_id": "51663", "opponent": "Atlanta United FC",
              "goals_for": 2, "goals_against": 1, "game_state": "regularly_terminated"},
             {"id": "4481938", "date": "2024-11-02T23:00:00+00:00", "stage_code": "1RR",
              "competition_id": "POUS", "season_id": "2023", "team_id": "69261",
              "team": "Inter Miami CF", "opponent_id": "51663", "opponent": "Atlanta United FC",
              "goals_for": 1, "goals_against": 2, "game_state": "regularly_terminated"},
             {"id": "4481947", "date": "2024-11-10T01:00:00+00:00", "stage_code": "1RE",
              "competition_id": "POUS", "season_id": "2023", "team_id": "69261",
              "team": "Inter Miami CF", "opponent_id": "51663", "opponent": "Atlanta United FC",
              "goals_for": 2, "goals_against": 3, "game_state": "regularly_terminated"},
         ]},
    ]


def test_complete_saved_mls_series_confirms_elimination_without_mutating_sources():
    rows = mls_phases()
    original = deepcopy(rows)

    result = apply_league_championship(rows)

    assert rows == original
    metadata = result[0]["league_championship"]
    assert result[1]["league_championship"] == metadata
    assert metadata == {
        "status": "confirmed", "outcome": "not_winner", "competition_id": "POUS",
        "team_id": "69261", "season_id": "2023", "basis": "mls_best_of_three_elimination",
        "game_ids": ["4479032", "4481938", "4481947"], "opponent_id": "51663",
        "series_wins": 1, "series_losses": 2,
    }
    assert apply_league_championship(result) == result


def test_supporters_shield_or_conference_rank_does_not_award_an_mls_cup_bonus():
    rows = mls_phases()
    rows[0]["games"] = 15
    rows[0]["competition_complete"] = True
    copa = {"id": "COPA", "name": "Copa América", "category": "national_major",
            "games": 5, "place": "1", "final_outcome": "winner", "team": "Argentina"}

    result = calculate_season_points({"competitions": [*rows, copa]}, weighted_rating=8)

    assert result["status"] == "complete"
    assert result["pending"] == []
    assert result["bonus_points"] == 150
    assert result["total"] == 950
    assert [bonus["kind"] for bonus in result["bonuses"]] == ["euro_copa"]


def test_best_of_three_uses_match_wins_even_when_the_loser_has_more_aggregate_goals():
    rows = mls_phases()
    rows[0]["games"] = 15
    rows[1]["matches"][0].update(goals_for=10, goals_against=0)

    confirmed = apply_league_championship(rows)
    assert confirmed[0]["league_championship"]["outcome"] == "not_winner"
    assert confirmed[0]["league_championship"]["series_wins"] == 1
    assert confirmed[0]["league_championship"]["series_losses"] == 2
    result = calculate_season_points({"competitions": rows}, weighted_rating=8)

    assert result["status"] == "complete"
    assert result["bonus_points"] == 0
    assert result["pending"] == []


@pytest.mark.parametrize("change", [
    "missing_game", "duplicate_id", "different_opponent", "missing_score", "tied_score",
    "unplayed", "different_season", "different_team", "invalid_date", "invalid_sequence",
    "advancing", "duplicate_phase", "unlinked_team", "unlinked_season", "mixed_seasons",
    "later_stage", "unconfirmed_shootout",
])
def test_partial_or_conflicting_mls_evidence_stays_unconfirmed_without_holding_season_points_pending(change):
    rows = mls_phases()
    rows[0]["games"] = 15
    matches = rows[1]["matches"]
    if change == "missing_game":
        matches.pop(0)
    elif change == "duplicate_id":
        matches[2]["id"] = matches[0]["id"]
    elif change == "different_opponent":
        matches[2]["opponent_id"] = "different-opponent"
    elif change == "missing_score":
        matches[2].pop("goals_against")
    elif change == "tied_score":
        matches[2]["goals_against"] = matches[2]["goals_for"]
    elif change == "unplayed":
        matches[2]["game_state"] = "scheduled"
    elif change == "different_season":
        matches[2]["season_id"] = "2024"
    elif change == "different_team":
        matches[2]["team_id"] = "different-team"
    elif change == "invalid_date":
        matches[2]["date"] = "not-a-date"
    elif change == "invalid_sequence":
        matches[0]["goals_for"], matches[0]["goals_against"] = 0, 1
    elif change == "advancing":
        matches[2]["goals_for"], matches[2]["goals_against"] = 4, 1
    elif change == "duplicate_phase":
        rows.append(deepcopy(rows[1]))
    elif change == "unlinked_team":
        rows[1]["team_id"] = "different-team"
    elif change == "unlinked_season":
        rows[1]["season_id"] = "2024"
        rows[1]["season_ids"] = ["2024"]
    elif change == "mixed_seasons":
        rows[1]["season_ids"] = ["2023", "2024"]
    elif change == "later_stage":
        rows[1]["stage_code"] = "HF"
    elif change == "unconfirmed_shootout":
        matches[2]["game_state"] = "penalty_shootout"

    assert all("league_championship" not in row for row in apply_league_championship(rows))
    result = calculate_season_points({"competitions": rows}, weighted_rating=8)
    assert result["status"] == "complete"
    assert result["bonus_points"] == 0
    assert result["total"] == 800
    assert result["pending"] == []


def test_forged_mls_outcome_metadata_is_discarded_without_holding_season_points_pending():
    rows = mls_phases()
    rows[0]["games"] = 15
    rows[1]["matches"] = []
    for row in rows:
        row["league_championship"] = {"status": "confirmed", "outcome": "not_winner"}

    assert all("league_championship" not in row for row in apply_league_championship(rows))
    result = calculate_season_points({"competitions": rows}, weighted_rating=8)

    assert result["status"] == "complete"
    assert result["bonus_points"] == 0
    assert result["total"] == 800
    assert result["pending"] == []


@pytest.mark.parametrize(("outcome", "scored", "conceded"), [
    ("winner", 2, 1), ("runner_up", 1, 2),
])
def test_confirmed_mls_playoff_final_overrides_table_rank_without_a_top_five_bonus(outcome, scored, conceded):
    rows = mls_phases()
    rows[0]["games"] = 15
    playoff = rows[1]
    playoff["matches"] = []
    playoff.update(stage_code="FF", final_outcome=outcome, final_result={
        "basis": "single_match_score", "game_ids": ["final-game"], "team_id": "69261",
        "opponent_id": "623", "team_goals": scored, "opponent_goals": conceded,
    })

    confirmed = apply_league_championship(rows)
    expected_outcome = "winner" if outcome == "winner" else "not_winner"
    assert all(row["league_championship"]["outcome"] == expected_outcome for row in confirmed)
    result = calculate_season_points({"competitions": rows}, weighted_rating=8)

    assert result["status"] == "complete"
    assert result["pending"] == []
    assert result["bonus_points"] == 0
    assert result["total"] == 800
    assert result["bonuses"] == []


def test_malformed_final_result_does_not_award_a_championship():
    rows = mls_phases()
    rows[0]["games"] = 15
    rows[1].update(stage_code="FF", final_outcome="winner", final_result={
        "basis": "single_match_score", "game_ids": ["final-game"], "team_id": "69261",
        "opponent_id": "623", "team_goals": 1, "opponent_goals": 2,
    })
    rows[1]["matches"] = []

    assert all("league_championship" not in row for row in apply_league_championship(rows))
    result = calculate_season_points({"competitions": rows}, weighted_rating=8)

    assert result["status"] == "complete"
    assert result["bonus_points"] == 0
    assert result["total"] == 800
    assert result["pending"] == []


def messi_sources():
    rows = mls_phases()
    # A compact saved source sample: one regular match, all three playoff
    # games, one CONCACAF match, and the Copa final.
    rows[1]["category"] = "other"  # Legacy TM classification before repair.
    rows.extend([
        {"id": "CCL", "name": "CONCACAF Champions Cup", "category": "continental",
         "competition_type_id": 10, "team": "Inter Miami CF", "team_id": "69261",
         "season_id": "2023", "games": 1, "goals": 1, "assists": 0, "minutes": 90,
         "matches": [{"id": "ccl-tm", "date": "2024-03-08T02:00:00+00:00",
                      "team": "Inter Miami CF", "opponent": "Nashville SC", "stage_code": "AFH"}]},
        {"id": "COPA", "name": "Copa América", "category": "national_major",
         "competition_type_id": 11, "team": "Argentina", "team_id": "3437",
         "season_id": "2023", "games": 1, "goals": 0, "assists": 0, "minutes": 66,
         "place": "1", "final_outcome": "winner",
         "matches": [{"id": "copa-tm", "date": "2024-07-15T01:20:00+00:00",
                      "team": "Argentina", "opponent": "Colombia", "stage_code": "FF"}]},
    ])
    def fm_game(identity, day, rating, opponent, competition_id, competition, stage=None):
        return {"id": identity, "date": day, "rating": rating, "opponent": opponent,
                "team": "Inter Miami CF", "competition_id": competition_id,
                "competition": competition, "stage": stage}
    mls_matches = [fm_game("4386586", "2024-02-22", 8.4, "Real Salt Lake", "130", "MLS")]
    mls_matches.extend(fm_game(identity, day, rating, "Atlanta United", "130", "MLS", "1/8")
                       for identity, day, rating in [
                           ("4662560", "2024-10-26", 8.9), ("4662944", "2024-11-02", 7.7),
                           ("4671556", "2024-11-10", 8.2),
                       ])
    ccl_match = fm_game("4435180", "2024-03-08", 8.4, "Nashville SC", "297", "CONCACAF Champions Cup", "1/8")
    ccl_match.update(category="national_major", is_national=True)  # Saved provider ID bug.
    copa_match = fm_game("copa-fm", "2024-07-15", 7.5, "Colombia", "44", "Copa America", "final")
    copa_match["team"] = "Argentina"
    fm = {"status": "matched", "player_id": "30981", "competitions": [
        {"id": "130", "name": "MLS", "category": "domestic_league", "team": "Inter Miami CF",
         "games": 4, "rating": 8.3, "rated_games": 4, "matches": mls_matches},
        {"id": "297", "name": "CONCACAF Champions Cup", "category": "national_major", "team": "Inter Miami CF",
         "games": 1, "rating": 8.4, "rated_games": 1, "matches": [ccl_match]},
        {"id": "44", "name": "Copa America", "category": "national_major", "team": "Argentina",
         "games": 1, "rating": 7.5, "rated_games": 1, "matches": [copa_match]},
    ]}
    tm = {"profile": {"id": "28003", "name": "Lionel Messi", "position": "RW"}, "competitions": rows}
    return tm, fm


def test_saved_messi_sources_restore_concacaf_and_split_merged_mls_without_losing_ratings(tmp_path):
    tm, fm = messi_sources()
    original = deepcopy((tm, fm))
    snapshot = combine(tm, fm)
    rows = {row["id"]: row for row in snapshot["competitions"]}

    assert (tm, fm) == original
    assert rows["MLS1"]["rated_games"] == 1
    assert rows["POUS"]["rated_games"] == 3
    assert rows["CCL"]["rated_games"] == 1
    assert len({match["id"] for row in rows.values() for match in row.get("fotmob_matches", [])}) == 6
    assert snapshot["warnings"] == []

    path = tmp_path / "seasons.sqlite3"
    store = Store(path)
    store.add_season(Season(2023))
    identity = store.save_player("2023-24", "28003", snapshot, tm, fm)
    player = store.player(identity)
    reopened = Store(path).player(identity)
    weighted = player["weighted_rating"]
    assert weighted["status"] == "complete"
    assert weighted["included_games"] == weighted["rated_games"] == 6
    assert weighted["total_weight"] == 9
    assert weighted["rating"] == pytest.approx((8.4 + 8.9 + 7.7 + 8.2 + 8.4 + 7.5 * 4) / 9 / 1.0096)
    assert player["season_points"]["status"] == "complete"
    assert player["season_points"]["bonus_points"] == 0
    assert player["season_points"]["total"] == pytest.approx(weighted["rating"] * 100)
    assert reopened["weighted_rating"] == weighted
    assert reopened["updated_at"] == player["updated_at"]
    assert store.raw(identity) == original
