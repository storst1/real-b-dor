"""Use complete FotMob context directly; fill its gaps from Transfermarkt."""

from datetime import date, datetime, timezone
import re
import unicodedata

from .competitions import is_supercup


_VARIANTS = str.maketrans({"ø": "o", "ł": "l", "đ": "d", "ı": "i", "æ": "ae", "œ": "oe"})
_CLUB_PREFIXES = {"fc", "cf", "afc", "fk", "pfc", "sc", "as", "cr", "rsc", "ss", "ssc", "sv", "vfl", "vfb", "tsg"}
_TEAM_ALIASES = {
    "bayern munich": "bayern", "bayern munchen": "bayern",
    "bayer 04 leverkusen": "bayer leverkusen",
    "1 fc union berlin": "union berlin", "1 fsv mainz 05": "mainz 05",
    "1 fc heidenheim 1846": "heidenheim", "heidenheim 1846": "heidenheim",
    "1 fc saarbrucken": "saarbrucken",
    "darmstadt 98": "darmstadt", "1899 hoffenheim": "hoffenheim",
    "kobenhavn": "copenhagen",
    "bsc young boys": "young boys",
    "fluminense football club": "fluminense",
    "bk hacken": "hacken", "teutonia 05 ottensen": "teutonia ottensen",
    "red bull salzburg": "salzburg", "rb salzburg": "salzburg",
    "union st gilloise": "union saint gilloise",
    "d c united": "dc united", "los angeles galaxy": "la galaxy",
    "real salt lake city": "real salt lake",
    "argentina olympic team": "argentina u23", "france olympic team": "france u23",
    "ukraine olympic team": "ukraine u23", "iraq olympic team": "iraq u23",
    "morocco olympic team": "morocco u23",
    "democratic republic of the congo": "dr congo", "republic of the congo": "congo",
    "feyenoord rotterdam": "feyenoord",
    "istiqlol dushanbe": "istiklol", "al tai": "al taee",
    "ca osasuna": "osasuna", "celta de vigo": "celta vigo", "rcd mallorca": "mallorca",
    "ud las palmas": "las palmas", "ud almeria": "almeria", "real betis balompie": "real betis",
    "tottenham hotspur": "tottenham", "spurs": "tottenham",
    "paris saint germain": "psg", "paris saint germain fc": "psg",
    "internazionale": "inter", "inter milan": "inter", "internazionale milano": "inter",
    "ac milan": "milan",
    "man city": "manchester city", "man united": "manchester united", "man utd": "manchester united",
    "atletico de madrid": "atletico madrid", "athletic bilbao": "athletic club",
    "borussia dortmund": "dortmund", "rasenballsport leipzig": "rb leipzig",
    "olympique marseille": "marseille", "olympique lyon": "lyon", "olympique lyonnais": "lyon",
    "girondins bordeaux": "bordeaux", "racing club de lens": "lens", "rc lens": "lens",
    "us revel": "revel",
    "sporting clube de portugal": "sporting cp", "sporting lisbon": "sporting cp",
    "sl benfica": "benfica", "porto": "porto", "rangers": "rangers",
    "ajax amsterdam": "ajax", "celtic glasgow": "celtic", "rangers glasgow": "rangers",
    "sparta praha": "sparta prague", "slavia praha": "slavia prague",
    "red star belgrade": "crvena zvezda", "dinamo moscow": "dynamo moscow",
    "turkiye": "turkey", "czech republic": "czechia", "korea republic": "south korea",
    "united states": "usa", "united states of america": "usa", "ir iran": "iran",
    "republic of ireland": "ireland",
    "bosnia and herzegovina": "bosnia herzegovina",
}
_KNOCKOUT_STAGES = {"round_of_16", "quarterfinal", "semifinal", "final", "playoff", "third_place"}
# Provider IDs verified in saved player histories. They remain FotMob IDs in
# the resolved context; they are never compared with Transfermarkt IDs.
_FOTMOB_COMPETITIONS = {
    "47": ("league", "domestic_league"),
    "54": ("league", "domestic_league"),
    "87": ("league", "domestic_league"),
    "42": ("champions_league", "continental"),
    "74": ("supercup", "continental"),
    "78": ("continental", "continental"),
    "130": ("league", "domestic_league"),
    "141": ("domestic_cup", "domestic_cup"),
    "297": ("continental", "continental"),
    "66": ("other_national", "other"),
    "536": ("league", "domestic_league"),
    "132": ("domestic_cup", "domestic_cup"),
    "138": ("domestic_cup", "domestic_cup"),
    "139": ("supercup", "domestic_cup"),
    "247": ("supercup", "domestic_cup"),
    "8924": ("supercup", "domestic_cup"),
    "44": ("euro_copa", "national_major"),
    "50": ("euro_copa", "national_major"),
    "77": ("world_cup", "national_major"),
    "290": ("other_major", "national_major"),
    "114": ("friendly", "national_regular"),
    "10199": ("national_regular", "national_regular"),
    "10607": ("national_regular", "national_regular"),
}
_FOTMOB_NAMES = {
    "premier league": ("league", "domestic_league"),
    "bundesliga": ("league", "domestic_league"),
    "laliga": ("league", "domestic_league"),
    "la liga": ("league", "domestic_league"),
    "serie a": ("league", "domestic_league"),
    "ligue 1": ("league", "domestic_league"),
    "eredivisie": ("league", "domestic_league"),
    "primeira liga": ("league", "domestic_league"),
    "mls": ("league", "domestic_league"),
    "major league soccer": ("league", "domestic_league"),
    "saudi pro league": ("league", "domestic_league"),
    "fa cup": ("domestic_cup", "domestic_cup"),
    "copa del rey": ("domestic_cup", "domestic_cup"),
    "dfb pokal": ("domestic_cup", "domestic_cup"),
    "coppa italia": ("domestic_cup", "domestic_cup"),
    "coupe de france": ("domestic_cup", "domestic_cup"),
    "efl cup": ("domestic_cup", "domestic_cup"),
    "europa league": ("continental", "continental"),
    "uefa europa league": ("continental", "continental"),
    "conference league": ("continental", "continental"),
    "uefa conference league": ("continental", "continental"),
    "uefa europa conference league": ("continental", "continental"),
    "fifa club world cup": ("continental", "continental"),
    "club world cup": ("continental", "continental"),
    "concacaf champions cup": ("continental", "continental"),
    "concacaf champions league": ("continental", "continental"),
    "world cup": ("world_cup", "national_major"),
    "fifa world cup": ("world_cup", "national_major"),
    "euro": ("euro_copa", "national_major"),
    "uefa euro": ("euro_copa", "national_major"),
    "european championship": ("euro_copa", "national_major"),
    "copa america": ("euro_copa", "national_major"),
    "asian cup": ("other_major", "national_major"),
    "afc asian cup": ("other_major", "national_major"),
    "friendlies": ("friendly", "national_regular"),
    "international friendlies": ("friendly", "national_regular"),
}


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalize_team(value) -> str:
    """Normalize known spellings without dropping youth/women's suffixes."""
    value = unicodedata.normalize("NFKD", str(value or "").casefold().translate(_VARIANTS))
    value = "".join(char for char in value if not unicodedata.combining(char))
    words = re.findall(r"[^\W_]+", value.replace("’", "").replace("'", ""))
    while words and words[0] in _CLUB_PREFIXES:
        words = words[1:]
    while words and words[-1] in _CLUB_PREFIXES:
        words = words[:-1]
    return _TEAM_ALIASES.get(" ".join(words), " ".join(words))


def canonical_stage(value) -> str | None:
    """Map explicit stages; a matchday number is never a knockout round."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    code = value.upper()
    # Verified Transfermarkt competitionGroups: 1R–7R are numbered cup
    # rounds; E marks a replay/decider and H/R the first/second leg.
    # These explicit provider codes are distinct from generic matchdays.
    if re.fullmatch(r"[1-7]R(?:E|H|R)?", code):
        return "playoff"
    stages = {"FF": "final", "HF": "semifinal", "VF": "quarterfinal", "AF": "round_of_16", "16F": "playoff", "PO": "playoff",
              "PD": "third_place"}
    if code in stages:
        return stages[code]
    if code.endswith(("H", "R")) and code[:-1] in stages:
        return stages[code[:-1]]
    if re.fullmatch(r"[A-P]", code):
        return "group"
    normalized = value.casefold().replace("_", " ").replace("–", "-").replace("—", "-")
    normalized = " ".join(normalized.split())
    normalized = re.sub(r"\s+(?:1st|2nd|first|second)\s+leg$", "", normalized)
    normalized = re.sub(r"\s*\((?:1st|2nd|first|second)\s+leg\)$", "", normalized)
    normalized = normalized.replace("-", " ")
    normalized = " ".join(normalized.split())
    aliases = {
        "final": "final", "finals": "final", "финал": "final",
        "semi final": "semifinal", "semi finals": "semifinal", "semifinal": "semifinal", "semifinals": "semifinal",
        "1/2": "semifinal", "1/2 final": "semifinal", "1/2 finals": "semifinal", "полуфинал": "semifinal",
        "quarter final": "quarterfinal", "quarter finals": "quarterfinal", "quarterfinal": "quarterfinal", "quarterfinals": "quarterfinal",
        "1/4": "quarterfinal", "1/4 final": "quarterfinal", "1/4 finals": "quarterfinal", "1/4 финала": "quarterfinal",
        "round of 16": "round_of_16", "last 16": "round_of_16", "1/8": "round_of_16",
        "1/8 final": "round_of_16", "1/8 finals": "round_of_16", "1/8 финала": "round_of_16",
        "round of 32": "playoff", "1/16": "playoff", "1/16 final": "playoff", "1/16 finals": "playoff",
        "round of 64": "playoff", "1/32": "playoff", "1/32 final": "playoff", "1/32 finals": "playoff",
        "playoff": "playoff", "playoffs": "playoff", "play off": "playoff", "play offs": "playoff",
        "knockout round play offs": "playoff", "knockout play offs": "playoff",
        "third place play off": "third_place", "third place match": "third_place",
        "3rd place play off": "third_place", "3rd place match": "third_place",
        "third place": "third_place", "3rd place": "third_place", "матч за третье место": "third_place",
        "bronze": "third_place", "bronze final": "third_place", "bronze medal match": "third_place",
        "group": "group", "group stage": "group", "group phase": "group", "групповой этап": "group",
        "regular": "league", "regular season": "league", "league": "league",
        "league phase": "league", "league stage": "league", "лиговый этап": "league",
    }
    if normalized in aliases:
        return aliases[normalized]
    if re.fullmatch(r"group (?:[a-z]|[0-9]{1,2})", normalized):
        return "group"
    return None


def _day(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("utcTime")
    if not isinstance(value, str):
        return None
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).date().isoformat()
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _integer(value) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    return None


def _identity(value) -> str | None:
    if type(value) not in (str, int):
        return None
    return _text(str(value))


def _fotmob_context(match: dict) -> dict | None:
    """Resolve only weights whose necessary facts are present in FotMob.

    A missing cup/major stage is not a group stage. In particular FotMob 42
    covers CL qualifying too, so a generic playoff is not enough to call it
    the main Champions League. These cases still use the TM fallback below.
    """
    identity = _identity(match.get("competition_id", match.get("leagueId")))
    name = _text(match.get("competition")) or _text(match.get("leagueName"))
    normalized_name = normalize_team(name)
    raw_stage = _text(match.get("stage"))
    stage = canonical_stage(raw_stage)
    opponent = _text(match.get("opponent")) or _text(match.get("opponentTeamName"))
    if not opponent:
        return None
    definition = _FOTMOB_COMPETITIONS.get(identity)
    if definition is None:
        if is_supercup({"competition": name}):
            category = match.get("category")
            if category not in {"continental", "domestic_cup"}:
                category = "continental" if normalized_name.startswith("uefa ") or normalized_name.startswith("recopa") else "domestic_cup"
            definition = ("supercup", category)
        else:
            definition = _FOTMOB_NAMES.get(normalized_name)
    if definition is None and normalized_name in {"champions league", "uefa champions league"}:
        return None
    if definition is None and stage is not None:
        # New imports retain the category derived from FotMob's own profile
        # and tournament. For unfamiliar formats an explicit stage is needed.
        category = match.get("category")
        kind = {"domestic_league": "league", "domestic_cup": "domestic_cup",
                "continental": "continental", "national_regular": "national_regular"}.get(category)
        if kind:
            definition = kind, category
    if definition is None:
        return None
    kind, category = definition
    if raw_stage and stage is None and kind != "supercup":
        return None
    if kind == "champions_league":
        if "qualif" in normalized_name or stage not in {"group", "league", "round_of_16", "quarterfinal", "semifinal", "final"}:
            return None
    elif kind not in {"league", "friendly", "supercup"} and stage is None:
        return None
    return {
        "competition_provider": "fotmob", "competition_id": identity,
        "competition": name, "category": category, "competition_kind": kind,
        "competition_type_id": None, "stage_code": None, "stage": stage,
        "is_knockout": (True if stage in _KNOCKOUT_STAGES else False
                        if stage in {"group", "league"} or kind in {"league", "friendly"} and stage is None else None),
        "opponent": opponent,
        "opponent_id": _identity(match.get("opponent_id", match.get("opponentTeamId"))),
        "is_national": category in {"national_regular", "national_major"} or kind == "other_national",
        "match_context_status": "matched",
    }


def _parent_identity(competition: dict, parent: dict | None) -> bool:
    return bool(parent and _identity(competition.get("id")) == _identity(parent.get("id"))
                and normalize_team(competition.get("team")) == normalize_team(parent.get("team")))


def _national(competition: dict, fotmob_match: dict) -> bool | None:
    category = competition.get("category")
    kind = _integer(competition.get("competition_type_id"))
    if category in {"national_regular", "national_major"} or kind in {11, 17, 19, 20}:
        return True
    if category in {"domestic_league", "domestic_cup", "continental"} or kind in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 18, 21, 22, 23, 24}:
        return False
    return fotmob_match.get("is_national") if type(fotmob_match.get("is_national")) is bool else None


def _context(fotmob_match: dict, competition: dict | None, tm_match: dict | None, status: str) -> dict:
    competition = competition or {}
    tm_match = tm_match or {}
    code = _text(tm_match.get("stage_code"))
    stage = canonical_stage(code) if code else canonical_stage(fotmob_match.get("stage"))
    return {
        "competition_id": _identity(competition.get("id")),
        "competition": _text(competition.get("name")) or _text(fotmob_match.get("competition")) or _text(fotmob_match.get("leagueName")),
        "category": _text(competition.get("category")),
        "competition_type_id": (_integer(tm_match.get("competition_type_id"))
                                if _integer(tm_match.get("competition_type_id")) is not None
                                else _integer(competition.get("competition_type_id"))),
        "stage_code": code,
        "stage": stage,
        "is_knockout": True if stage in _KNOCKOUT_STAGES else False if stage in {"group", "league"} else None,
        "opponent": (_text(fotmob_match.get("opponent")) or _text(fotmob_match.get("opponentTeamName"))
                     or _text(tm_match.get("opponent"))),
        "opponent_id": _identity(fotmob_match.get("opponent_id", fotmob_match.get("opponentTeamId"))),
        "is_national": _national(competition, fotmob_match),
        "match_context_status": status,
    }


def resolve_match_context(fotmob_match: dict, competitions: list[dict], *, parent: dict | None = None) -> dict:
    """Use sufficient FotMob data without comparing matches across sources.

    When FotMob lacks facts needed for a weight, find an unambiguous dated TM
    match. The parent alone is a legacy fallback only without dated records.
    """
    if context := _fotmob_context(fotmob_match):
        # FotMob 130 merges the regular season and playoffs. A blank playoff
        # stage cannot silently become an ordinary league game (weight one).
        mls_missing_stage = (context.get("competition_id") == "130" and context.get("stage") is None
                             and (not parent or str(parent.get("id")) != "MLS1"))
        if not mls_missing_stage:
            return context
    day = _day(fotmob_match.get("kickoff")) or _day(fotmob_match.get("date")) or _day(fotmob_match.get("matchDate"))
    team = normalize_team(fotmob_match.get("team") or fotmob_match.get("teamName"))
    opponent = normalize_team(fotmob_match.get("opponent") or fotmob_match.get("opponentTeamName"))
    matches = []
    seen = set()
    date_candidates = 0
    for competition in competitions:
        if not isinstance(competition, dict):
            continue
        for tm_match in competition.get("matches", []):
            if not isinstance(tm_match, dict) or not day or _day(tm_match.get("date")) != day:
                continue
            date_candidates += 1
            tm_team = normalize_team(tm_match.get("team") or competition.get("team"))
            tm_opponent = normalize_team(tm_match.get("opponent"))
            team_match = bool(team and tm_team and team == tm_team)
            opponent_match = bool(opponent and tm_opponent and opponent == tm_opponent)
            if opponent and tm_opponent and opponent != tm_opponent:
                continue
            in_parent = _parent_identity(competition, parent)
            if not team_match and not opponent_match and not in_parent:
                continue
            fingerprint = (_identity(competition.get("id")), _identity(tm_match.get("team_id")) or tm_team,
                           _identity(tm_match.get("id")), day, tm_opponent, _text(tm_match.get("stage_code")))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            matches.append((int(team_match) + int(opponent_match), competition, tm_match))
    if matches:
        best = max(row[0] for row in matches)
        selected = [row for row in matches if row[0] == best]
        if len(selected) == 1:
            _, competition, tm_match = selected[0]
            return _context(fotmob_match, competition, tm_match, "matched")
        return _context(fotmob_match, None, None, "ambiguous")
    if parent and not date_candidates:
        dated_parent = any(isinstance(row, dict) and _day(row.get("date")) for row in parent.get("matches", []))
        parent_team = normalize_team(parent.get("team"))
        if not dated_parent and (not team or not parent_team or team == parent_team):
            return _context(fotmob_match, parent, None, "matched")
    return _context(fotmob_match, None, None, "unmatched")
