import pytest
from datetime import date

from season_ledger.domain import Season, age_on, aggregate, combine, fotmob_id, transfermarkt_id


def test_age_is_measured_at_selected_season_cutoff_and_birthday_boundary():
    assert age_on("1993-07-28", Season(2023).cutoff) == 30
    assert age_on("1993-07-28T00:00:00Z", date(2024, 7, 27)) == 30
    assert age_on("1993-07-28", date(2024, 7, 28)) == 31
    assert age_on("2004-02-29", date(2023, 2, 28)) == 18
    assert age_on("2004-02-29", date(2023, 3, 1)) == 19
    assert age_on(None, date(2024, 6, 30)) is None
    assert age_on("unknown", date(2024, 6, 30)) is None
    assert age_on("2025-01-01", date(2024, 6, 30)) is None


def test_season_calendar_and_split_and_canonical_keys():
    split = Season.parse("2023-24")
    assert split.label == "2023/24"
    assert split.end.isoformat() == "2024-06-30"
    assert split.national_end.isoformat() == "2024-08-31"
    assert Season.parse("2024").end.isoformat() == "2024-12-31"
    for key in ("2023-25", "2023/24", "2024-024", "abcd"):
        with pytest.raises(ValueError):
            Season.parse(key)


def test_profile_urls_cannot_target_arbitrary_hosts():
    assert transfermarkt_id("https://www.transfermarkt.com/harry-kane/profil/spieler/132098") == "132098"
    assert transfermarkt_id("https://www.transfermarkt.de/harry-kane/profil/spieler/132098?x=1") == "132098"
    for url in ("https://evil.com/harry-kane/profil/spieler/132098", "https://www.transfermarkt.com.evil.com/a/profil/spieler/132098", "https://user@www.transfermarkt.com/a/profil/spieler/132098", "file:///a/profil/spieler/132098", "https://www.transfermarkt.com:443/a/profil/spieler/132098"):
        with pytest.raises(ValueError):
            transfermarkt_id(url)
    assert fotmob_id("https://www.fotmob.com/players/194165/harry-kane") == "194165"
    assert fotmob_id("194165") == "194165"


def test_rating_weights_only_rated_appearances_and_unknown_is_not_zero():
    result = aggregate([{"games": 10, "goals": 0, "assists": None, "minutes": 800, "rating": 8, "rated_games": 2},
                        {"games": 5, "goals": 3, "assists": 1, "minutes": 300, "rating": 6, "rated_games": 4}])
    assert result["games"] == 15
    assert result["goals"] == 3
    assert result["assists"] is None
    assert result["rating"] == 6.67
    assert result["rated_games"] == 6
    assert aggregate([{"rating": 8}, {"rating": 6}])["rating"] is None


def test_combine_does_not_duplicate_competitions_or_overwrite_transfermarkt_counts():
    tm = {"profile": {"id": "1", "name": "Test"}, "competitions": [
        {"id": "CL", "name": "UEFA Champions League", "category": "continental", "team": "FC Bayern München", "games": 12, "goals": 8, "assists": 4, "minutes": 1000}]}
    fm = {"status": "matched", "competitions": [
        {"id": "42", "name": "Champions League", "category": "continental", "team": "Bayern Munich", "games": 11, "rating": 7.5, "rated_games": 10}]}
    result = combine(tm, fm)
    assert len(result["competitions"]) == 1
    assert result["total"]["games"] == 12
    assert result["total"]["rating"] == 7.5
    assert result["total"]["rated_games"] == 10
    assert len(result["warnings"]) == 1


def test_ambiguous_competition_is_not_silently_assigned():
    tm = {"profile": {"id": "1", "name": "Test"}, "competitions": [
        {"id": "CL", "name": "Champions League", "category": "continental", "team": "A", "games": 3},
        {"id": "EL", "name": "Europa League", "category": "continental", "team": "A", "games": 3}]}
    fm = {"competitions": [{"name": "Unknown", "category": "continental", "team": "A", "rating": 8, "rated_games": 2}]}
    result = combine(tm, fm)
    assert result["total"]["rating"] is None
    assert "не сопоставлены" in result["warnings"][0]


def test_same_category_is_not_proof_of_same_tournament_but_dates_are():
    tm = {"profile": {"id": "1", "name": "Test"}, "competitions": [
        {"id": "CL", "name": "Champions League", "category": "continental", "team": "A", "games": 3,
         "matches": [{"date": "2024-04-01T12:00:00Z"}]}]}
    fm = {"competitions": [{"name": "UEFA Super Cup", "category": "continental", "team": "A", "rating": 8, "rated_games": 2,
                            "matches": [{"date": "2023-08-01"}]}]}
    assert combine(tm, fm)["total"]["rating"] is None
    fm["competitions"][0].update(name="Different provider name", matches=[{"date": "2024-04-01"}])
    assert combine(tm, fm)["total"]["rating"] == 8


def test_match_dates_bridge_different_team_spellings_but_not_different_editions():
    tm = {"profile": {"id": "1", "name": "Test"}, "competitions": [
        {"id": "CL", "name": "UEFA Champions League", "category": "continental", "team": "Bayer 04 Leverkusen", "games": 3,
         "matches": [{"date": "2024-04-01T12:00:00Z"}]}]}
    fm = {"competitions": [{"name": "Champions League", "category": "continental", "team": "Bayer Leverkusen", "rating": 8, "rated_games": 1,
                            "matches": [{"date": "2024-04-01"}]}]}
    assert combine(tm, fm)["total"]["rating"] == 8
    fm["competitions"][0].update(team="Bayer 04 Leverkusen", matches=[{"date": "2023-01-01"}])
    assert combine(tm, fm)["total"]["rating"] is None
