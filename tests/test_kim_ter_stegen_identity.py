import asyncio
from copy import deepcopy

import httpx
import pytest

from season_ledger.domain import Season
from season_ledger.providers.fotmob import FotMobProvider


KIM_TM = {"id": "503482", "name": "Min-jae Kim", "birth_date": "1996-11-15",
          "clubs": [{"name": "FC Bayern Munich"}]}
KIM_FM = {"id": 828159, "name": "Kim Min-Jae", "birthDate": {"utcTime": "1996-11-15"},
          "primaryTeam": {"teamName": "Bayern München"}}
TER_STEGEN_TM = {"id": "74857", "name": "Marc-André ter Stegen", "birth_date": "1992-04-30",
                 "clubs": [{"name": "FC Barcelona"}]}
TER_STEGEN_FM = {"id": 184554, "name": "Marc ter Stegen", "birthDate": {"utcTime": "1992-04-30"},
                 "primaryTeam": {"teamName": "Ajax"},
                 "careerHistory": {"careerItems": {"senior": {"teamEntries": [{"team": "Barcelona"}]}}}}


def resolve(profile, profiles, searches, *, player_id=None):
    searched, hydrated, histories = [], [], []

    def handler(request):
        if request.url.path.endswith("search/suggest"):
            term = request.url.params["term"]
            searched.append(term)
            return httpx.Response(200, json=[{"suggestions": [
                {"type": "player", "id": str(identity), "name": profiles[str(identity)]["name"]}
                for identity in searches.get(term, [])
            ]}])
        if request.url.path.endswith("playerData"):
            identity = request.url.params["id"]
            hydrated.append(identity)
            return httpx.Response(200, json=profiles[identity])
        assert request.url.path.endswith("playerMatches")
        histories.append(request.url.params["url"])
        return httpx.Response(200, json={"matches": [], "previous": None})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await FotMobProvider(client).load(deepcopy(profile), Season(2023), player_id=player_id)

    return asyncio.run(run()), searched, hydrated, histories


def test_kim_fallback_reorders_search_and_deduplicates_profiles_across_queries():
    profiles = {
        "2007426": {**KIM_FM, "id": 2007426, "name": "Min-Jae Kim", "birthDate": {"utcTime": "2006-08-03"}},
        "251808": {**KIM_FM, "id": 251808, "name": "Min-Jae Kim", "birthDate": {"utcTime": "1989-09-12"}},
        "828159": KIM_FM,
    }
    result, searched, hydrated, histories = resolve(KIM_TM, profiles, {
        "Min-jae Kim": [2007426, 251808, 2007426],
        "Kim Min-jae": [251808, 828159, 828159],
    })

    assert result["status"] == "matched"
    assert result["player_id"] == "828159"
    assert searched == ["Min-jae Kim", "Kim Min-jae"]
    assert hydrated == ["2007426", "251808", "828159"]
    assert len(histories) == 1 and "/828159/matches?" in histories[0]


def test_ter_stegen_empty_full_name_search_uses_family_name_and_historical_barcelona():
    result, searched, hydrated, histories = resolve(TER_STEGEN_TM, {"184554": TER_STEGEN_FM}, {
        "ter Stegen": [184554],
    })

    assert result["status"] == "matched"
    assert result["player_id"] == "184554"
    assert searched == ["Marc-André ter Stegen", "ter Stegen"]
    assert hydrated == ["184554"]
    assert len(histories) == 1 and "/184554/matches?" in histories[0]


def test_confirmed_primary_result_does_not_trigger_fallback_search_or_extra_hydration():
    exact = {**KIM_FM, "name": "Min-jae Kim"}
    result, searched, hydrated, histories = resolve(KIM_TM, {"828159": exact}, {"Min-jae Kim": [828159]})

    assert result["status"] == "matched"
    assert searched == ["Min-jae Kim"]
    assert hydrated == ["828159"]
    assert len(histories) == 1


def test_confirmed_primary_ambiguity_stops_before_fallback_can_mask_a_duplicate():
    profiles = {"828159": {**KIM_FM, "name": "Min-jae Kim"},
                "828160": {**KIM_FM, "id": 828160, "name": "Min-jae Kim"}}
    result, searched, hydrated, histories = resolve(KIM_TM, profiles, {
        "Min-jae Kim": [828159, 828160], "Kim Min-jae": [828159],
    })

    assert result["status"] == "ambiguous"
    assert searched == ["Min-jae Kim"]
    assert hydrated == ["828159", "828160"]
    assert histories == []


def test_two_corroborated_reordered_names_stay_ambiguous():
    result, searched, hydrated, histories = resolve(KIM_TM, {
        "828159": KIM_FM, "828160": {**KIM_FM, "id": 828160},
    }, {"Kim Min-jae": [828159, 828160]})

    assert result["status"] == "ambiguous"
    assert searched == ["Min-jae Kim", "Kim Min-jae"]
    assert hydrated == ["828159", "828160"]
    assert histories == []


@pytest.mark.parametrize("profile", [
    {**KIM_TM, "birth_date": None}, {**KIM_TM, "birth_date": "1996-11-16"},
    {**KIM_TM, "clubs": []}, {**KIM_TM, "clubs": [{"name": "Napoli"}]},
])
def test_kim_reordering_requires_matching_birthday_and_club(profile):
    result, _, _, histories = resolve(profile, {"828159": KIM_FM}, {"Kim Min-jae": [828159]})

    assert result["status"] == "ambiguous"
    assert result["player_id"] is None
    assert histories == []


@pytest.mark.parametrize("name", ["Kim Jae", "Kim Min", "Min Kim Jae", "Kim Min-Jae U19"])
def test_partial_or_shuffled_given_name_tokens_are_not_treated_as_a_reordered_surname(name):
    result, _, _, histories = resolve(KIM_TM, {"828159": {**KIM_FM, "name": name}}, {"Kim Min-jae": [828159]})

    assert result["status"] == "ambiguous"
    assert result["player_id"] is None
    assert histories == []


def test_ter_stegen_fallback_still_requires_historical_club_evidence():
    candidate = {**TER_STEGEN_FM, "careerHistory": {}}
    result, searched, hydrated, histories = resolve(TER_STEGEN_TM, {"184554": candidate}, {"ter Stegen": [184554]})

    assert result["status"] == "ambiguous"
    assert searched == ["Marc-André ter Stegen", "ter Stegen", "Stegen Marc-André ter"]
    assert hydrated == ["184554"]
    assert histories == []


def test_empty_fallback_searches_are_bounded_and_remain_not_found():
    result, searched, hydrated, histories = resolve(TER_STEGEN_TM, {}, {})

    assert result["status"] == "not_found"
    assert searched == ["Marc-André ter Stegen", "ter Stegen", "Stegen Marc-André ter"]
    assert len(searched) == len(set(searched)) == 3
    assert hydrated == histories == []


@pytest.mark.parametrize("profile,expected", [(KIM_TM, "matched"), ({**KIM_TM, "clubs": []}, "not_found")])
def test_manual_reordered_name_uses_same_birthday_and_club_evidence(profile, expected):
    result, searched, hydrated, histories = resolve(profile, {"828159": KIM_FM}, {}, player_id="828159")

    assert result["status"] == expected
    assert searched == []
    assert hydrated == ["828159"]
    assert len(histories) == (1 if expected == "matched" else 0)
