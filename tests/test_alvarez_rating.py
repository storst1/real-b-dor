from copy import deepcopy

import pytest

from season_ledger.domain import Season, combine
from season_ledger.providers.fotmob import _aggregate_matches
from season_ledger.providers.transfermarkt import aggregate_performances
from season_ledger.ratings import apply_position_adjustment
from season_ledger.weighting import calculate_weighted_rating
from season_ledger.points import calculate_season_points


OLYMPIC_COMPETITIONS = {"OLYM": {"id": "OLYM", "name": "Olympic Games", "typeId": 17}}
OLYMPIC_CLUBS = {
    "53819": {"name": "Argentina Olympic Team"},
    "53820": {"name": "Morocco Olympic Team"},
    "53808": {"name": "Iraq Olympic Team"},
    "114122": {"name": "Ukraine Olympic Team"},
    "53860": {"name": "France Olympic Team"},
}
OLYMPIC_ROWS = [
    ("4317054", "4451294", "2024-07-24", "B", "53820", "Morocco U23", 6.1, 90, 0),
    ("4317062", "4451296", "2024-07-27", "B", "53808", "Iraq U23", 7.8, 90, 2),
    ("4317070", "4451298", "2024-07-30", "B", "114122", "Ukraine U23", 6.9, 81, 0),
    ("4397267", "4451337", "2024-08-02", "VF", "53860", "France U23", 6.4, 90, 0),
]


def tm_records():
    return [{
        "gameInformation": {
            "gameId": tm_id, "date": {"dateTimeUTC": day + "T13:00:00+00:00"},
            "competitionId": "OLYM", "competitionTypeId": 17, "competitionGroupId": code,
            "seasonId": 2023, "isNationalGame": True, "gameState": "regularly_terminated",
        },
        "clubsInformation": {"club": {"clubId": "53819"}, "opponent": {"clubId": opponent_id}},
        "statistics": {
            "generalStatistics": {"participationState": "played"},
            "goalStatistics": {"goalsScoredTotal": 0, "assists": assists},
            "playingTimeStatistics": {"playedMinutes": minutes},
        },
    } for tm_id, _, day, code, opponent_id, _, _, minutes, assists in OLYMPIC_ROWS]


def fm_records():
    return [{
        "id": fm_id, "matchDate": {"utcTime": day + "T13:00:00+00:00"},
        "leagueId": 66, "leagueName": "Summer Olympics", "teamId": 317232, "teamName": "Argentina U23",
        "opponentTeamId": {"Morocco U23": 316155, "Iraq U23": 318825,
                           "Ukraine U23": 1625332, "France U23": 1249010}[opponent],
        "opponentTeamName": opponent,
        "playedInMatch": True, "minutesPlayed": minutes, "goals": 0,
        "assists": 1 if assists else 0, "ratingProps": {"rating": str(rating)},
        "stage": "1/4" if code == "VF" else None,
    } for _, fm_id, day, code, opponent_id, opponent, rating, minutes, assists in OLYMPIC_ROWS]


def olympic_fotmob_data():
    return {"careerHistory": {"careerItems": {"national team": {
        "teamEntries": [{"teamId": 317232, "team": "Argentina U23"}],
    }}}}


@pytest.mark.parametrize("season,expected", [
    (Season(2023), 4), (Season(2024), 0),
    (Season(2024, calendar=True), 4), (Season(2023, calendar=True), 0),
])
def test_real_olympic_matches_belong_to_preceding_club_season_only(season, expected):
    original = tm_records()
    before = deepcopy(original)

    result = aggregate_performances(original, season, OLYMPIC_COMPETITIONS, OLYMPIC_CLUBS)

    assert original == before
    assert sum(row["games"] for row in result) == expected
    if expected:
        assert result[0]["id"] == "OLYM"
        assert result[0]["category"] == "other"
        assert result[0]["team_id"] == "53819"
        assert result[0]["games"] == 4
        assert result[0]["minutes"] == 351
        assert result[0]["goals"] == 0
        assert result[0]["assists"] == 2
        assert result[0]["stage_history"] == ["B", "VF"]


def test_olympic_extension_does_not_extend_other_under_23_matches_or_allow_september():
    september = tm_records()[0]
    september["gameInformation"]["date"]["dateTimeUTC"] = "2024-09-01T13:00:00Z"
    ordinary_youth = deepcopy(tm_records()[0])
    ordinary_youth["gameInformation"]["competitionId"] = "U23FS"

    result = aggregate_performances([september, ordinary_youth], Season(2023), OLYMPIC_COMPETITIONS, OLYMPIC_CLUBS)

    assert result == []


@pytest.mark.parametrize("season,expected", [(Season(2023), 4), (Season(2024), 0)])
def test_fotmob_olympics_use_same_summer_boundaries_without_dropping_other_category(season, expected):
    competitions = aggregate_performances(tm_records(), season, OLYMPIC_COMPETITIONS, OLYMPIC_CLUBS)

    result = _aggregate_matches(fm_records(), olympic_fotmob_data(), season, competitions)

    assert sum(row["games"] for row in result) == expected
    if expected:
        assert result[0]["id"] == "66"
        assert result[0]["category"] == "other"
        assert result[0]["rated_games"] == 4
        assert all(match["is_national"] is True for match in result[0]["matches"])


def test_alvarez_olympic_games_join_real_tm_history_and_keep_weights_without_senior_major_bonuses():
    season = Season(2023)
    tm_competitions = aggregate_performances(tm_records(), season, OLYMPIC_COMPETITIONS, OLYMPIC_CLUBS)
    fm_competitions = _aggregate_matches(fm_records(), olympic_fotmob_data(), season, tm_competitions)
    snapshot = combine({"profile": {"id": "576024", "name": "Julián Álvarez", "position": "CF"},
                        "competitions": tm_competitions},
                       {"status": "matched", "competitions": fm_competitions})
    weighted = calculate_weighted_rating(apply_position_adjustment(snapshot))
    points = calculate_season_points(snapshot, weighted_rating=weighted["rating"])

    assert snapshot["warnings"] == []
    assert weighted["status"] == "complete"
    assert weighted["rated_games"] == weighted["included_games"] == 4
    assert weighted["total_weight"] == 4
    assert weighted["rating"] == pytest.approx(27.2 / 4)
    assert [match["context"]["stage"] for match in weighted["matches"]] == ["group", "group", "group", "quarterfinal"]
    assert all([rule["key"] for rule in match["weight_rules"]] == ["base"] for match in weighted["matches"])
    assert snapshot["total"]["rated_games"] == snapshot["total"]["games"] == 4
    assert snapshot["groups"]["national_major"]["games"] == 0
    assert snapshot["groups"]["national_regular"]["games"] == 0
    assert points["status"] == "complete"
    assert points["bonus_points"] == 0
    assert points["total"] == pytest.approx(680)
