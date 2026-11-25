"""FotMob identities and season ratings, using the sibling project's HTTP approach.

The profile's season summaries can include an entire qualification campaign, or
put a summer EURO in the following club season. We therefore aggregate dated
playerMatches rows instead of relabelling those summaries.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
import math
import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from season_ledger.domain import Season


DEFAULT_BASE_URL = "https://www.fotmob.com/api/data/"
_ID = re.compile(r"[1-9][0-9]{0,19}")
_CONTINENTAL_IDS = {42, 73, 78, 10216}
_MAJOR_IDS = {44, 50, 77, 289, 290, 297}
_FRIENDLY_IDS = {489, 9408, 9675}
_VARIANTS = str.maketrans({"ø": "o", "ł": "l", "đ": "d", "ı": "i", "æ": "ae", "œ": "oe"})


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _array(value) -> list:
    return value if isinstance(value, list) else []


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _id(value) -> str | None:
    value = str(value) if type(value) in (str, int) else ""
    return value if _ID.fullmatch(value) else None


def _name(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold().translate(_VARIANTS))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", text.replace("’", "").replace("'", "")))


def _number(value) -> float | None:
    if type(value) not in (int, float, str):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _integer(value) -> int | None:
    value = _number(value)
    return int(value) if value is not None and value >= 0 and value.is_integer() else None


def _rating(value) -> float | None:
    value = _number(value)
    return value if value is not None and 0 < value <= 10 else None


def _day(value) -> date | None:
    value = _object(value).get("utcTime") if isinstance(value, dict) else value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return None


def _team_name(value) -> str:
    value = _name(value)
    words = value.split()
    if words and words[0] in {"fc", "cf", "afc", "pfc", "sc", "as", "cr"}:
        words = words[1:]
    if words and words[-1] in {"fc", "cf", "afc", "pfc", "sc"}:
        words = words[:-1]
    value = " ".join(words)
    aliases = {
        "bayern munich": "bayern", "bayern munchen": "bayern", "fc bayern munchen": "bayern",
        "fc bayern munich": "bayern",
        "tottenham hotspur": "tottenham", "real madrid cf": "real madrid",
        "fc barcelona": "barcelona", "paris saint germain": "psg",
    }
    return aliases.get(value, value)


def _surname_tokens(value) -> set[str]:
    words = _name(value).split()
    # A single-name profile such as Saka can supply a family-name token. Do not
    # use shared given names or surname particles as identity evidence.
    family = words[1:] if len(words) > 1 else words
    particles = {"de", "da", "do", "das", "dos", "del", "la", "las", "los", "van", "von",
                 "di", "el", "al", "bin", "ben", "jr", "junior", "ii", "iii"}
    return {word for word in family if len(word) >= 3 and word not in particles}


def _shared_surname(first, second) -> bool:
    first_words, second_words = _name(first).split(), _name(second).split()
    endings = {words[-1] for words in (first_words, second_words) if words}
    return bool(_surname_tokens(first) & _surname_tokens(second) & endings)


def _profile_clubs(profile: dict) -> set[str]:
    return {name for row in _array(profile.get("clubs")) if isinstance(row, dict)
            and (name := _team_name(row.get("name")))}


def _fotmob_clubs(data: dict) -> set[str]:
    result = set()
    if name := _team_name(_object(data.get("primaryTeam")).get("teamName")):
        result.add(name)
    items = _object(_object(data.get("careerHistory")).get("careerItems"))
    senior = _object(items.get("senior"))
    for row in [*_array(senior.get("teamEntries")), *_array(senior.get("seasonEntries"))]:
        if isinstance(row, dict) and (name := _team_name(row.get("team"))):
            result.add(name)
    return result


def _national_ids(data: dict) -> set[str]:
    result = set()
    items = _object(_object(data.get("careerHistory")).get("careerItems"))
    for key, value in items.items():
        if "national" in key.casefold():
            for row in _array(_object(value).get("teamEntries")):
                if identity := _id(_object(row).get("teamId")):
                    result.add(identity)
    return result


def _category(row: dict, national_teams: set[str]) -> str:
    name = _name(row.get("leagueName"))
    league_id = _integer(row.get("leagueId"))
    national = (_id(row.get("teamId")) in national_teams or league_id in _MAJOR_IDS or league_id == 114
                or "nations league" in name or "euro qualif" in name
                or ("world cup qualif" in name and "club" not in name))
    qualification = any(s in name for s in ("qualif", "nations league", "friendlies", "friendly"))
    major = league_id in _MAJOR_IDS or any(s in name for s in (
        "world cup", "copa america", "euro", "africa cup", "asian cup", "gold cup",
        "ofc nations cup", "olympic",
    ))
    if national:
        return "national_major" if major and not qualification else "national_regular"
    if league_id in _FRIENDLY_IDS or "friendl" in name or "audi cup" in name:
        return "other"
    if league_id in _CONTINENTAL_IDS or any(s in name for s in (
        "champions league", "europa league", "conference league", "libertadores",
        "sudamericana", "club world cup", "afc cup", "caf confederation", "recopa",
        "uefa super cup", "intercontinental", "concacaf champions",
    )):
        return "continental"
    if any(s in name for s in ("cup", "copa", "coupe", "pokal", "taca", "supercop", "super cup")):
        return "domestic_cup"
    return "domestic_league"


def _in_season(day: date, category: str, season: Season) -> bool:
    if category == "national_major" and not season.calendar:
        # July/August finals belong to the preceding club season in both sources.
        return date(season.start_year, 9, 1) <= day <= season.national_end
    return season.start <= day <= season.end


def _competition_days(competitions: list[dict] | None, *, clubs_only: bool = False) -> set[date]:
    days = set()
    for competition in competitions or []:
        if clubs_only and competition.get("category") in {"national_regular", "national_major"}:
            continue
        for match in _array(competition.get("matches")):
            if isinstance(match, dict) and (day := _day(match.get("date"))) is not None:
                days.add(day)
    return days


def _search_players(payload) -> list[dict]:
    rows = []
    for group in _array(payload):
        rows.extend(_array(_object(group).get("suggestions")))
    if isinstance(payload, dict):
        rows.extend(_array(payload.get("players")))
        rows.extend(_array(payload.get("squadMember")))
    result = {}
    for item in rows:
        row = _object(item)
        if row.get("type", "player") != "player" or row.get("isCoach") is True:
            continue
        if (identity := _id(row.get("id"))) and _text(row.get("name")):
            result.setdefault(identity, row)
    return list(result.values())


def _candidate(data: dict) -> dict:
    return {
        "id": _id(data.get("id")), "name": _text(data.get("name")),
        "team": _text(_object(data.get("primaryTeam")).get("teamName")),
        "birth_date": value.isoformat() if (value := _day(data.get("birthDate"))) else None,
    }


def _previous_before(value, player_id: str) -> int | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("FotMob вернул некорректную следующую страницу матчей.")
    url = urlsplit(value)
    if (url.scheme != "https" or url.hostname != "pub.fotmob.com" or url.port is not None
            or url.username or url.path != f"/prod/db/api/player/{player_id}/matches"):
        raise ValueError("FotMob вернул неожиданный адрес следующей страницы.")
    values = parse_qs(url.query).get("before", [])
    if len(values) != 1 or not re.fullmatch(r"[0-9]{1,12}", values[0]):
        raise ValueError("FotMob вернул некорректный курсор матчей.")
    return int(values[0])


def _match_history_row(row: dict) -> dict:
    """Keep source match context for future weights without changing coverage."""
    day = _day(row.get("matchDate"))
    kickoff = None
    raw_kickoff = _object(row.get("matchDate")).get("utcTime")
    if isinstance(raw_kickoff, str):
        try:
            parsed = datetime.fromisoformat(raw_kickoff)
            if parsed.tzinfo is not None:
                kickoff = parsed.astimezone(UTC).isoformat()
        except ValueError:
            pass
    page_url = _text(row.get("matchPageUrl"))
    return {
        "id": _id(row.get("id")), "date": day.isoformat() if day else None,
        "kickoff": kickoff, "source": "FotMob",
        "rating": _rating(_object(row.get("ratingProps")).get("rating")),
        "minutes": _integer(row.get("minutesPlayed")),
        "goals": _integer(row.get("goals")), "assists": _integer(row.get("assists")),
        "team_id": _id(row.get("teamId")), "team": _text(row.get("teamName")),
        "opponent_id": _id(row.get("opponentTeamId")), "opponent": _text(row.get("opponentTeamName")),
        "competition_id": _id(row.get("leagueId")), "competition": _text(row.get("leagueName")),
        "is_home": row.get("isHomeTeam") if type(row.get("isHomeTeam")) is bool else None,
        "home_score": _integer(row.get("homeScore")), "away_score": _integer(row.get("awayScore")),
        "stage": _text(row.get("stage")),
        "url": f"https://www.fotmob.com{page_url}" if page_url and page_url.startswith("/matches/") else None,
    }


def _aggregate_matches(matches: list[dict], data: dict, season: Season,
                       season_competitions: list[dict] | None = None) -> list[dict]:
    groups = defaultdict(list)
    national_teams = _national_ids(data)
    club_days = _competition_days(season_competitions, clubs_only=True)
    seen = set()
    for row in matches:
        identity = _id(row.get("id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        day = _day(row.get("matchDate"))
        category = _category(row, national_teams)
        club = category not in {"national_regular", "national_major"}
        # Source season IDs, rather than June 30, identify delayed club seasons
        # such as the 2019/20 Champions League, completed in August 2020.
        included = (day in club_days if club and not season.calendar and season_competitions is not None
                    else day is not None and _in_season(day, category, season))
        if day is None or not included:
            continue
        # Unused substitutes carry zero minutes and a synthetic rating of zero.
        played = row.get("playedInMatch")
        rating = _rating(_object(row.get("ratingProps")).get("rating"))
        if played is False or (played is not True and not ((_integer(row.get("minutesPlayed")) or 0) > 0 or rating is not None)):
            continue
        # Exhibition club games do not belong in the season competition table.
        if category == "other":
            continue
        league_id, team_id = _id(row.get("leagueId")), _id(row.get("teamId"))
        if not league_id or not team_id or not _text(row.get("leagueName")) or not _text(row.get("teamName")):
            continue
        groups[(league_id, team_id, category)].append(row)
    result = []
    for (league_id, team_id, category), rows in groups.items():
        ratings = [_rating(_object(row.get("ratingProps")).get("rating")) for row in rows]
        ratings = [r for r in ratings if r is not None]
        item = {
            "id": league_id, "name": rows[0]["leagueName"], "category": category,
            "team": rows[0]["teamName"], "team_id": team_id, "games": len(rows),
            "rating": sum(ratings) / len(ratings) if ratings else None,
            "rated_games": len(ratings), "place": None,
            "matches": [_match_history_row(row) for row in rows],
        }
        for metric, field in (("goals", "goals"), ("assists", "assists"), ("minutes", "minutesPlayed")):
            values = [_integer(row.get(field)) for row in rows]
            item[metric] = sum(values) if all(value is not None for value in values) else None
        result.append(item)
    return sorted(result, key=lambda row: (row["category"], row["name"], row["team"]))


class FotMobProvider:
    def __init__(self, client: httpx.AsyncClient, base_url: str = DEFAULT_BASE_URL):
        self.client = client
        self.base_url = base_url.rstrip("/")

    async def _get(self, route: str, **params):
        response = await self.client.get(f"{self.base_url}/{route}", params=params, follow_redirects=False)
        response.raise_for_status()
        return response.json()

    async def _profile(self, player_id: str) -> dict:
        data = await self._get("playerData", id=player_id)
        if not isinstance(data, dict) or _id(data.get("id")) != player_id or not _text(data.get("name")) or data.get("isCoach") is True:
            raise ValueError("FotMob вернул некорректный профиль игрока.")
        return data

    async def _matches(self, player_id: str, season: Season,
                       season_competitions: list[dict] | None = None) -> list[dict]:
        competition_days = _competition_days(season_competitions)
        start = min({season.start, *competition_days})
        end = min(max({season.national_end, *competition_days}), date.today())
        before = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC).timestamp())
        matches = []
        seen_cursors = set()
        for _ in range(20):
            if before in seen_cursors:
                raise ValueError("FotMob повторяет страницу матчей; загрузка остановлена.")
            seen_cursors.add(before)
            # Build the sole permitted upstream URL ourselves. Never follow a
            # URL supplied by the user or an unvalidated upstream cursor.
            url = f"https://pub.fotmob.com/prod/db/api/player/{player_id}/matches?" + urlencode({
                "before": before, "leagueId": "all", "teamId": "all",
            })
            page = await self._get("playerMatches", url=url)
            if not isinstance(page, dict) or not isinstance(page.get("matches"), list):
                raise ValueError("FotMob вернул некорректную историю матчей.")
            rows = page["matches"]
            if any(not isinstance(row, dict) or _day(row.get("matchDate")) is None for row in rows):
                raise ValueError("В истории FotMob отсутствуют даты матчей.")
            matches.extend(rows)
            if rows and min(_day(row["matchDate"]) for row in rows) < start:
                return matches
            next_before = _previous_before(page.get("previous"), player_id)
            if next_before is None:
                return matches
            if not rows or next_before >= before:
                raise ValueError("FotMob вернул некорректный порядок страниц матчей.")
            before = next_before
        raise ValueError("История FotMob слишком велика; сезон загружен не полностью.")

    async def load(self, profile: dict, season: Season, player_id: str | None = None) -> dict:
        result = {"player_id": None, "player_name": None, "profile_url": None,
                  "competitions": [], "candidates": [], "status": "not_found", "warnings": []}
        try:
            if player_id is not None:
                if not _id(player_id):
                    raise ValueError("Некорректный ID игрока FotMob.")
                data = await self._profile(player_id)
                candidate = _candidate(data)
                result["candidates"] = [candidate]
                tm_birth = _day(profile.get("birth_date"))
                fm_birth = _day(data.get("birthDate"))
                if tm_birth is not None and fm_birth is not None and tm_birth != fm_birth:
                    result["warnings"].append("Дата рождения выбранного игрока FotMob не совпадает с Transfermarkt.")
                    return result
                same_name = _name(profile.get("name")) == _name(data.get("name"))
                supported_alias = (tm_birth is not None and tm_birth == fm_birth
                                   and _shared_surname(profile.get("name"), data.get("name")))
                if not same_name and not supported_alias:
                    result["warnings"].append("Выбранный профиль FotMob не удалось сопоставить с игроком Transfermarkt.")
                    return result
            else:
                search = await self._get("search/suggest", term=profile.get("name", ""), hits="20", lang="en")
                suggestions = _search_players(search)
                if not suggestions:
                    result["warnings"].append("Игрок не найден в FotMob. Можно указать ссылку на его профиль вручную.")
                    return result
                hydrated = []
                for suggestion in suggestions[:10]:
                    candidate_data = await self._profile(_id(suggestion["id"]))
                    hydrated.append(candidate_data)
                result["candidates"] = [_candidate(row) for row in hydrated]
                exact = [row for row in hydrated if _name(row.get("name")) == _name(profile.get("name"))]
                tm_birth = _day(profile.get("birth_date"))
                clubs = _profile_clubs(profile)
                if tm_birth:
                    matching = [row for row in hydrated if _day(row.get("birthDate")) == tm_birth
                                and (row in exact or (_shared_surname(profile.get("name"), row.get("name"))
                                     and clubs & _fotmob_clubs(row)))]
                else:
                    matching = [row for row in exact if clubs & _fotmob_clubs(row)]
                if len(matching) != 1:
                    result["status"] = "ambiguous"
                    result["warnings"].append("Профиль FotMob нужно выбрать вручную: однозначного совпадения имени и даты рождения нет.")
                    return result
                data = matching[0]
                player_id = _id(data["id"])
            result.update(player_id=player_id, player_name=data["name"],
                          profile_url=f"https://www.fotmob.com/players/{player_id}")
            season_competitions = profile.get("season_competitions")
            matches = await self._matches(player_id, season, season_competitions)
            result["competitions"] = _aggregate_matches(matches, data, season, season_competitions)
            result["status"] = "matched"
            if not result["competitions"]:
                result["warnings"].append("В FotMob нет сыгранных матчей за выбранный сезон.")
        except (httpx.HTTPError, ValueError) as error:
            result["status"] = "unavailable"
            if isinstance(error, httpx.HTTPStatusError):
                code = error.response.status_code
                message = "FotMob временно ограничил запросы. Повторите обновление позже." if code == 429 else f"FotMob недоступен (HTTP {code})."
            elif isinstance(error, httpx.HTTPError):
                message = "Не удалось соединиться с FotMob. Повторите обновление позже."
            else:
                message = str(error)
            result["warnings"].append(message)
        return result
