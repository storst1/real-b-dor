from dataclasses import dataclass
from copy import deepcopy
from datetime import date
import math
import re
import unicodedata
from urllib.parse import urlsplit


CATEGORIES = [
    {"key": "domestic_league", "label": "Чемпионат"},
    {"key": "continental", "label": "Лига чемпионов"},
    {"key": "national_regular", "label": "Сборная · остальные"},
    {"key": "national_major", "label": "ЧМ / Евро / Копа"},
    {"key": "domestic_cup", "label": "Кубки страны"},
    {"key": "other", "label": "Другие турниры"},
]


@dataclass(frozen=True)
class Season:
    start_year: int
    calendar: bool = False

    def __post_init__(self):
        if type(self.start_year) is not int or not 1900 <= self.start_year <= date.today().year + 1:
            raise ValueError("Укажите год сезона от 1900 до следующего года.")
        if type(self.calendar) is not bool:
            raise ValueError("Неверный формат сезона.")

    @property
    def key(self) -> str:
        return str(self.start_year) if self.calendar else f"{self.start_year}-{str(self.start_year + 1)[-2:]}"

    @property
    def label(self) -> str:
        return str(self.start_year) if self.calendar else f"{self.start_year}/{str(self.start_year + 1)[-2:]}"

    @property
    def start(self) -> date:
        return date(self.start_year, 1 if self.calendar else 7, 1)

    @property
    def end(self) -> date:
        return date(self.start_year, 12, 31) if self.calendar else date(self.start_year + 1, 6, 30)

    @property
    def national_end(self) -> date:
        return self.end if self.calendar else date(self.start_year + 1, 8, 31)

    @property
    def cutoff(self) -> date:
        return min(self.end, date.today())

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "start_year": self.start_year,
                "calendar": self.calendar, "start": self.start.isoformat(), "end": self.end.isoformat(),
                "national_end": self.national_end.isoformat(), "cutoff": self.cutoff.isoformat()}

    @classmethod
    def parse(cls, key: str):
        match = re.fullmatch(r"(\d{4})(?:-(\d{2}))?", key)
        if match is None:
            raise ValueError("Неверный сезон.")
        result = cls(int(match[1]), calendar=match[2] is None)
        if result.key != key:
            raise ValueError("Неверный сезон.")
        return result


def current_season() -> Season:
    today = date.today()
    return Season(today.year if today.month >= 7 else today.year - 1)


def age_on(birth_date: str | None, as_of: date) -> int | None:
    if not isinstance(birth_date, str):
        return None
    try:
        born = date.fromisoformat(birth_date[:10])
    except ValueError:
        return None
    if born > as_of:
        return None
    return as_of.year - born.year - ((as_of.month, as_of.day) < (born.month, born.day))


def transfermarkt_id(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError("Вставьте ссылку на профиль Transfermarkt.")
    # Clipboard URLs sometimes retain a Markdown closing parenthesis.
    url = url.strip().rstrip(")\\")
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        raise ValueError("Некорректная ссылка Transfermarkt.") from None
    allowed = {"transfermarkt.com", "transfermarkt.co.uk", "transfermarkt.de", "transfermarkt.ru",
               "www.transfermarkt.com", "www.transfermarkt.co.uk", "www.transfermarkt.de", "www.transfermarkt.ru"}
    match = re.fullmatch(r"/(?:[^/]+/)?(?:profil|leistungsdaten|marktwertverlauf)/spieler/([1-9]\d{0,19})(?:/.*)?/?", parsed.path)
    if parsed.scheme not in {"https", "http"} or hostname not in allowed or port is not None or parsed.username or not match:
        raise ValueError("Нужна ссылка на профиль игрока: https://www.transfermarkt.com/имя/profil/spieler/132098")
    return match[1]


def fotmob_id(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    value = value.strip()
    if re.fullmatch(r"[1-9]\d{0,12}", value):
        return value
    try:
        parsed = urlsplit(value)
        match = re.match(r"/(?:[a-z]{2}/)?players/([1-9]\d{0,12})(?:/|$)", parsed.path)
        if parsed.scheme == "https" and parsed.hostname in {"fotmob.com", "www.fotmob.com"} and not parsed.username and parsed.port is None and match:
            return match[1]
    except ValueError:
        pass
    raise ValueError("Укажите ID или ссылку на профиль игрока в FotMob.")


def _key(value) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    value = " ".join(re.findall(r"[^\W_]+", "".join(c for c in value if not unicodedata.combining(c))))
    aliases = {
        "uefa champions league": "champions league", "uefa europa league": "europa league",
        "uefa conference league": "conference league", "uefa europa conference league": "conference league",
        "european championship": "euro", "euro 2024": "euro", "euro 2020": "euro",
        "world cup 2022": "world cup", "fifa world cup": "world cup", "euros": "euro",
        "bayern munich": "bayern", "bayern munchen": "bayern", "fc bayern munchen": "bayern",
        "fc bayern munich": "bayern", "real madrid cf": "real madrid", "tottenham hotspur": "tottenham",
        "fc barcelona": "barcelona", "paris saint germain": "psg", "internazionale": "inter",
    }
    return aliases.get(value, value)


def _numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def is_champions_league(row: dict, *, provider: str = "transfermarkt") -> bool:
    """Identify the UEFA main competition without matching names of other cups."""
    identifiers = {"transfermarkt": "CL", "fotmob": "42"}
    if provider not in identifiers:
        return False
    identity = row.get("id")
    if identity is not None and str(identity).strip():
        return str(identity).strip() == identifiers[provider]
    # Names are only a fallback for legacy records without a provider ID.
    # Qualifiers and AFC/CAF/youth/women's competitions have different names.
    return _key(row.get("name")) == "champions league"


def contributes_to_group(row: dict, group: str) -> bool:
    return row.get("category") == group and (group != "continental" or is_champions_league(row))


def aggregate(rows: list[dict]) -> dict:
    result = {}
    for metric in ("games", "goals", "assists", "minutes"):
        values = [r.get(metric) for r in rows]
        result[metric] = sum(values) if all(_numeric(v) for v in values) else None
    rated = [r for r in rows if _numeric(r.get("rating")) and 0 < r["rating"] <= 10]
    known = [r for r in rated if _numeric(r.get("rated_games")) and r["rated_games"] > 0]
    # Unknown rating sample sizes are never replaced with appearance counts.
    if rated and len(known) == len(rated):
        denominator = sum(r["rated_games"] for r in known)
        result["rating"] = round(sum(r["rating"] * r["rated_games"] for r in known) / denominator, 2)
        result["rated_games"] = denominator
    elif len(rated) == 1:
        result["rating"] = rated[0]["rating"]
        result["rated_games"] = rated[0].get("rated_games")
    else:
        result["rating"] = None
        result["rated_games"] = None
    places = list(dict.fromkeys(str(r["place"]) for r in rows if r.get("place") is not None))
    result["place"] = " / ".join(places) or None
    return result


def apply_competition_scope(snapshot: dict) -> dict:
    """Rebuild the CL column while retaining every competition in the total."""
    result = deepcopy(snapshot)
    competitions = result.get("competitions")
    if not isinstance(competitions, list):
        return result
    rows = [row for row in competitions if isinstance(row, dict)]
    for row in rows:
        row["is_champions_league"] = is_champions_league(row)
    result["groups"] = {category["key"]: aggregate([row for row in rows if contributes_to_group(row, category["key"])])
                        for category in CATEGORIES}
    result["total"] = aggregate(rows)
    return result


def combine(tm: dict, fm: dict) -> dict:
    """Transfermarkt owns counts; FotMob supplies ratings, matched without duplicating rows."""
    warnings = [*tm.get("warnings", []), *fm.get("warnings", [])]
    competitions = []
    for item in tm.get("competitions", []):
        competitions.append({**item, "rating": None, "rated_games": None, "sources": ["Transfermarkt"]})
    used = set()
    for rating in fm.get("competitions", []):
        category_rows = [(i, row) for i, row in enumerate(competitions) if i not in used
                         and row.get("category") == rating.get("category")]
        candidates = [(i, row) for i, row in category_rows
                      if _key(row.get("team")) == _key(rating.get("team")) or not rating.get("team")]
        exact = [(i, r) for i, r in candidates if _key(r.get("name")) == _key(rating.get("name"))]
        # Provider IDs differ. Dated appearances provide evidence when their
        # competition names differ; a shared category alone does not.
        dates = {str(m.get("date", ""))[:10] for m in rating.get("matches", []) if m.get("date")}
        overlapping = [(i, r) for i, r in category_rows if dates.intersection(
            str(m.get("date", ""))[:10] for m in r.get("matches", []) if m.get("date"))]
        # Match dates also bridge different provider spellings of the club.
        # If both sources supply dates, names alone cannot overrule disjoint
        # records (e.g. two editions of the same cup).
        has_tm_dates = any(r.get("matches") for _, r in category_rows)
        selected = overlapping if dates and has_tm_dates else exact
        if len(selected) > 1:
            selected_exact = [(i, r) for i, r in selected if (i, r) in exact]
            if selected_exact:
                selected = selected_exact
        if len(selected) != 1:
            if rating.get("rating") is not None:
                warnings.append(f"Оценка FotMob для «{rating.get('name', 'Турнир')}» не включена: турнир или команда не сопоставлены однозначно.")
            continue
        i, row = selected[0]
        used.add(i)
        row.update(rating=rating.get("rating"), rated_games=rating.get("rated_games"))
        row["fotmob_matches"] = rating.get("matches", [])
        row["sources"].append("FotMob")
        if rating.get("place") is not None and row.get("place") is None:
            row["place"] = str(rating["place"])
        if rating.get("games") is not None and row.get("games") != rating["games"]:
            warnings.append(f"«{row['name']}»: Transfermarkt — {row.get('games')} матчей, FotMob — {rating['games']}. В таблице матчи Transfermarkt; оценка — по доступным матчам FotMob.")
    profile = tm["profile"]
    return apply_competition_scope({**profile, "transfermarkt_id": str(profile["id"]),
            "transfermarkt_url": f"https://www.transfermarkt.com/-/profil/spieler/{profile['id']}",
            "fotmob_id": fm.get("player_id"), "fotmob_url": fm.get("profile_url"),
            "fotmob_status": fm.get("status", "unavailable"), "fotmob_candidates": fm.get("candidates", []),
            "market_value": tm.get("market_value"), "competitions": competitions,
            "warnings": list(dict.fromkeys(warnings))})
