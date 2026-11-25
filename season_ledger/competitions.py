"""Shared competition identity checks for source rows and match contexts."""

from collections.abc import Mapping
import re
import unicodedata


SUPERCUP_TYPE_IDS = frozenset({9, 13, 23})
# Verified in this application's Transfermarkt records: Germany, UEFA,
# England's Community Shield, and Spain, respectively. Never infer a code
# from a generic "SC" substring or from a numeric FotMob competition ID.
_SUPERCUP_CODES = frozenset({"DFL", "USC", "GBCS", "SUC"})
_SUPERCUP_NAME = re.compile(
    r"(?:^| )(?:super ?(?:cup|copa|coppa|coupe|taca)|"
    r"(?:community|charity) ?shield|trophee des champions)(?: |$)"
)
# Exact official aliases, including the editions listed in KNVB's history.
# https://www.knvb.com/dutch-football/mens-football/johan-cruyff-shield
# https://ligue1.com/en/articles/l1_article_1554-
_NAMED_SUPERCUP = re.compile(
    r"(?:johan cruijff schaal|johan cruyff shield|ptt telecom cup|challenge des champions)"
    r"(?: (?:[ivxlcdm]+|\d{4}))?"
)

# FotMob IDs confirmed by stored match histories. These repair old derived
# categories too; numeric source IDs are never Transfermarkt type IDs.
_FOTMOB_CATEGORIES = {"66": "other", "130": "domestic_league", "141": "domestic_cup",
                      "290": "national_major", "297": "continental"}
_FOTMOB_CATEGORY_NAMES = {
    "mls": "domestic_league", "major league soccer": "domestic_league",
    "coppa italia": "domestic_cup", "italy cup": "domestic_cup",
    "concacaf champions cup": "continental", "concacaf champions league": "continental",
    "summer olympics": "other", "olympic games": "other",
    "asian cup": "national_major", "afc asian cup": "national_major",
}


def _name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(re.findall(r"[^\W_]+", "".join(c for c in text if not unicodedata.combining(c))))


def fotmob_competition_category(row: Mapping) -> str | None:
    """Return a verified category override for raw or stored FotMob rows."""
    identity = row.get("leagueId", row.get("competition_id", row.get("id")))
    if type(identity) in (str, int) and (category := _FOTMOB_CATEGORIES.get(str(identity))):
        return category
    name = row.get("leagueName", row.get("competition", row.get("name")))
    return _FOTMOB_CATEGORY_NAMES.get(_name(name)) if isinstance(name, str) else None


def is_supercup(row: Mapping) -> bool:
    """Recognise supercups without reclassifying or mutating the record.

    Accepts stored rows (name/id/competition_type_id), resolved match contexts
    (competition/competition_id/competition_kind), and raw TM type aliases.
    Type identifiers here are Transfermarkt types, never FotMob league IDs.
    """
    if not isinstance(row, Mapping):
        return False
    records = [row]
    for key in ("competition", "gameInformation"):
        nested = row.get(key)
        if isinstance(nested, Mapping):
            records.append(nested)
    for record in records:
        if record.get("competition_kind") == "supercup":
            return True
        for key in ("competition_type_id", "competitionTypeId", "type_id", "typeId"):
            kind = record.get(key)
            if type(kind) is int and kind in SUPERCUP_TYPE_IDS:
                return True
            if isinstance(kind, str) and kind.strip() in {"9", "13", "23"}:
                return True
        for key in ("id", "competition_id", "competitionId", "code", "competition_code"):
            code = record.get(key)
            if isinstance(code, str) and code.strip().upper() in _SUPERCUP_CODES:
                return True
        for key in ("name", "competition", "competition_name", "competitionName", "label"):
            name = record.get(key)
            if not isinstance(name, str):
                continue
            name = _name(name)
            if _SUPERCUP_NAME.search(name) or _NAMED_SUPERCUP.fullmatch(name):
                return True
            # "Recopa de Europa" can mean the old Cup Winners' Cup, so the
            # word "recopa" alone is not a safe substring match.
            if name == "recopa" or re.search(r"(?:^| )recopa (?:sudamericana|sul americana)(?: |$)", name):
                return True
    return False
