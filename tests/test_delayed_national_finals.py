from copy import deepcopy

import pytest

from season_ledger.domain import Season
from season_ledger.providers.fotmob import _aggregate_matches
from season_ledger.providers.transfermarkt import aggregate_performances, category


def tm_game(identity, day, competition="AFAC", kind=11, edition=2022, stage="E", played=True):
    return {
        "gameInformation": {"gameId": identity, "date": {"dateTimeUTC": day + "T12:00:00Z"},
                            "competitionId": competition, "competitionTypeId": kind, "seasonId": edition,
                            "competitionGroupId": stage, "isNationalGame": True, "gameState": "regularly_terminated"},
        "clubsInformation": {"club": {"clubId": "3589"}},
        "statistics": {"generalStatistics": {"participationState": "played" if played else "absent"},
                       "goalStatistics": {"goalsScoredTotal": 0, "assists": 0},
                       "playingTimeStatistics": {"playedMinutes": 90 if played else 0}},
    }


def fm_game(identity, day, competition=290, name="Asian Cup", stage=None, played=True):
    return {"id": identity, "matchDate": {"utcTime": day + "T12:00:00Z"},
            "leagueId": competition, "leagueName": name, "teamId": 7804, "teamName": "South Korea",
            "playedInMatch": played, "minutesPlayed": 90 if played else 0, "goals": 0, "assists": 0,
            "ratingProps": {"rating": "7.5" if played else "0"}, "stage": stage}


def national_data():
    return {"careerHistory": {"careerItems": {"national team": {"teamEntries": [{"teamId": 7804}]}}}}


def test_real_delayed_asian_cup_keeps_kims_five_appearances_but_not_absent_semifinal():
    records = [tm_game("4078213", "2024-01-15"), tm_game("4078215", "2024-01-20"),
               tm_game("4078217", "2024-01-25"), tm_game("4274565", "2024-01-30", stage="AF"),
               tm_game("4280470", "2024-02-02", stage="VF"),
               tm_game("4281997", "2024-02-06", stage="HF", played=False)]
    original = deepcopy(records)
    # The verified AFAC ID must work even when optional competition labels are absent.
    result = aggregate_performances(records, Season(2023), {}, {"3589": {"name": "South Korea"}})

    assert records == original
    assert category({"competitionId": "AFAC", "competitionTypeId": 11}) == "national_major"
    assert len(result) == 1
    assert result[0]["games"] == 5
    assert result[0]["season_id"] == "2022"
    assert result[0]["category"] == "national_major"
    assert [match["id"] for match in result[0]["matches"]] == ["4078213", "4078215", "4078217", "4274565", "4280470"]
    assert result[0]["stage_history"] == ["E", "AF", "VF", "HF"]
    assert aggregate_performances(records, Season(2022), {}, {}) == []
    assert aggregate_performances(records, Season(2024), {}, {}) == []


def test_delayed_november_world_cup_uses_playing_date_instead_of_nominal_edition():
    record = tm_game("world-cup", "2022-11-23", competition="FIWC", edition=2021, stage="E")

    assert aggregate_performances([record], Season(2022), {}, {})[0]["games"] == 1
    assert aggregate_performances([record], Season(2021), {}, {}) == []
    assert aggregate_performances([record], Season(2023), {}, {}) == []
    fotmob = _aggregate_matches([fm_game(1, "2022-11-23", 77, "World Cup")], national_data(), Season(2022))
    assert fotmob[0]["games"] == 1


@pytest.mark.parametrize("tm_id,tm_kind,fm_id,name", [
    ("EURO", 11, 50, "EURO"), ("OLYM", 17, 66, "Summer Olympics"),
])
@pytest.mark.parametrize("day,belongs_to", [
    ("2023-07-01", 2022), ("2023-08-31", 2022), ("2023-09-01", 2023),
    ("2024-06-30", 2023), ("2024-07-01", 2023), ("2024-08-31", 2023), ("2024-09-01", 2024),
])
def test_tm_and_fotmob_final_tournaments_share_nonoverlapping_actual_date_windows(tm_id, tm_kind, fm_id, name, day, belongs_to):
    tm_record = tm_game("final-tournament", day, tm_id, tm_kind, edition=2000)
    fm_record = fm_game(1, day, fm_id, name)
    selected_tm, selected_fm = [], []
    for year in [2022, 2023, 2024]:
        season = Season(year)
        if aggregate_performances([tm_record], season, {}, {}):
            selected_tm.append(year)
        if _aggregate_matches([fm_record], national_data(), season):
            selected_fm.append(year)

    assert selected_tm == selected_fm == [belongs_to]


@pytest.mark.parametrize("competition,kind", [("FS", 11), ("EMQ", 19), ("U23FS", 17), ("U20", 20)])
def test_other_national_matches_keep_july_june_window_without_finals_extension(competition, kind):
    records = [tm_game("previous-july", "2023-07-01", competition, kind),
               tm_game("following-july", "2024-07-01", competition, kind)]

    result = aggregate_performances(records, Season(2023), {}, {})

    assert result[0]["games"] == 1
    assert result[0]["matches"][0]["id"] == "previous-july"


def test_calendar_year_keeps_january_december_dates_for_delayed_and_summer_finals():
    records = [tm_game("previous-december", "2023-12-31"), tm_game("january", "2024-01-01"),
               tm_game("summer", "2024-07-15"), tm_game("december", "2024-12-31"),
               tm_game("following-january", "2025-01-01")]

    result = aggregate_performances(records, Season(2024, calendar=True), {}, {})

    assert [match["id"] for match in result[0]["matches"]] == ["january", "summer", "december"]
