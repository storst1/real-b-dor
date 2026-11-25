"""Additive, source-aware match weights for a player's seasonal FotMob rating."""

from copy import deepcopy
import math

from .domain import is_champions_league
from .competitions import is_supercup
from .match_context import normalize_team, resolve_match_context
from .ratings import POSITION_COEFFICIENTS, position_group


FORMULA_VERSION = "1"
GRAND_CLUBS = frozenset({
    "manchester city", "manchester united", "liverpool", "chelsea", "arsenal", "bayern",
    "barcelona", "real madrid", "atletico madrid", "psg", "inter", "milan", "juventus",
})
GRAND_NATIONAL_TEAMS = frozenset({"spain", "argentina", "brazil", "portugal", "france", "england", "italy", "netherlands"})
_CLUB_ALIASES = {"ac milan": "milan", "club atletico de madrid": "atletico madrid", "paris sg": "psg",
                 "mc": "manchester city", "mu": "manchester united"}
_NATIONAL_ALIASES = {"espana": "spain", "brasil": "brazil", "italia": "italy", "the netherlands": "netherlands", "holland": "netherlands"}
# These FotMob IDs were verified in the project's real player-history probes.
# Exact name aliases cover the complete user-provided list, including teams
# whose IDs have not been independently verified here.
_VERIFIED_GRAND_CLUB_IDS = frozenset({"9823", "8633", "9825"})  # Bayern, Real Madrid, Arsenal
_VERIFIED_GRAND_NATIONAL_IDS = frozenset({"6720", "8491"})  # Spain, England
_ORDINARY_DOMESTIC_CUP_TYPES = frozenset({8, 14, 15})
_EXPLICIT_STAGES = frozenset({"group", "league", "round_of_16", "quarterfinal", "semifinal", "final", "third_place", "playoff"})
_FOTMOB_COMPETITION_KINDS = frozenset({
    "league", "domestic_cup", "supercup", "champions_league", "continental", "world_cup",
    "euro_copa", "national_regular", "other_major", "other_national", "friendly",
})


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _rating(value) -> float | None:
    return float(value) if _number(value) and 0 < value <= 10 else None


def _identity(value) -> str | None:
    return (str(value).strip() or None) if type(value) in (str, int) else None


def _is_supercup(context: dict) -> bool:
    return context.get("competition_kind") == "supercup" or is_supercup(context)


def _major_kind(context: dict) -> str | None:
    if context.get("competition_provider") == "fotmob":
        kind = context.get("competition_kind")
        return kind if kind in {"world_cup", "euro_copa"} else None
    if context.get("category") != "national_major":
        return None
    identity = _identity(context.get("competition_id"))
    if identity:
        if identity in {"FIWC", "WM"}:
            return "world_cup"
        return "euro_copa" if identity in {"EURO", "COPA", "CA16"} else None
    name = normalize_team(context.get("competition"))
    if name in {"world cup", "fifa world cup"}:
        return "world_cup"
    return "euro_copa" if name in {"uefa euro", "euro", "european championship", "copa america", "copa america centenario"} else None


def _grand_club(context: dict) -> bool:
    name = normalize_team(context.get("opponent"))
    return (_CLUB_ALIASES.get(name, name) in GRAND_CLUBS
            or _identity(context.get("opponent_id")) in _VERIFIED_GRAND_CLUB_IDS)


def _grand_national(context: dict) -> bool:
    name = normalize_team(context.get("opponent"))
    return (_NATIONAL_ALIASES.get(name, name) in GRAND_NATIONAL_TEAMS
            or _identity(context.get("opponent_id")) in _VERIFIED_GRAND_NATIONAL_IDS)


def _regular_without_stage(context: dict) -> bool:
    if context.get("competition_provider") == "fotmob":
        return context.get("competition_kind") in {"league", "friendly"}
    if context.get("category") == "domestic_league" and context.get("competition_type_id") in {1, 2, 3, 4, 5, 6, 7}:
        return True
    return (context.get("competition_id") == "FS"
            or normalize_team(context.get("competition")) in {"friendlies", "international friendlies", "friendly", "international friendly"})


def _match_weight(context: dict) -> tuple[float | None, list[dict], list[str]]:
    rules = [{"key": "base", "label": "Базовый вес", "addition": 1.0}]
    issues = []

    def add(key, label, addition):
        rules.append({"key": key, "label": label, "addition": addition})

    if context.get("match_context_status") != "matched":
        issues.append("Недостаточно данных для определения контекста матча.")
    fotmob = context.get("competition_provider") == "fotmob"
    competition_kind = context.get("competition_kind")
    if fotmob and competition_kind not in _FOTMOB_COMPETITION_KINDS:
        issues.append("Тип турнира FotMob не определён; правила веса матча неизвестны.")
    if fotmob and competition_kind == "champions_league" and _identity(context.get("competition_id")) != "42":
        issues.append("Идентификатор основной Лиги чемпионов FotMob не подтверждён.")
    if not normalize_team(context.get("opponent")) and _identity(context.get("opponent_id")) not in (_VERIFIED_GRAND_CLUB_IDS | _VERIFIED_GRAND_NATIONAL_IDS):
        issues.append("Соперник не определён; надбавка за соперника из списка грандов неизвестна.")
    supercup = _is_supercup(context)
    stage = context.get("stage")
    if stage not in _EXPLICIT_STAGES and not supercup and not (stage is None and _regular_without_stage(context)):
        issues.append("Стадия матча не определена; вес плей-офф или финала неизвестен.")
    if (context.get("category") == "domestic_cup" and context.get("competition_type_id") is None
            and not supercup and not (fotmob and competition_kind == "domestic_cup")):
        issues.append("Тип национального кубка не определён; исключения для кубков и суперкубков неизвестны.")
    main_cl = (context.get("category") == "continental"
               and ((_identity(context.get("competition_id")) == "42" and competition_kind == "champions_league")
                    if fotmob else is_champions_league({"id": context.get("competition_id"), "name": context.get("competition")})))
    if main_cl:
        add("champions_league", "Матч основной Лиги чемпионов", .5)
        if context.get("is_knockout") is True:
            add("champions_league_knockout", "Плей-офф Лиги чемпионов", 2.5)
        elif context.get("is_knockout") is not False:
            issues.append("Не определено, относится ли матч Лиги чемпионов к плей-офф.")
    ordinary_cup = (competition_kind == "domestic_cup" if fotmob
                    else context.get("competition_type_id") in _ORDINARY_DOMESTIC_CUP_TYPES)
    if stage == "semifinal" and not supercup and not ordinary_cup:
        add("semifinal", "Полуфинал", 1.0)
    if stage == "final" and not supercup:
        add("final", "Финал", 2.0)
    major = _major_kind(context)
    if _grand_club(context):
        add("grand_club_opponent", "Соперник — клуб из списка грандов", .5)
    elif major and _grand_national(context):
        add("grand_national_opponent", "Соперник — сборная из списка грандов", .5)
    if major == "euro_copa":
        add("euro_copa", "Матч Евро или Копа Америка", .5)
    elif major == "world_cup":
        add("world_cup", "Матч чемпионата мира", 1.5)
    if major and stage == "final":
        add("major_final", "Финал ЧМ, Евро или Копа Америка", .5)
    return (None if issues else math.fsum(rule["addition"] for rule in rules)), rules, issues


def _position_coefficient(snapshot: dict) -> float | None:
    metadata = snapshot.get("rating_adjustment")
    coefficient = metadata.get("coefficient") if isinstance(metadata, dict) else POSITION_COEFFICIENTS.get(position_group(snapshot.get("position")))
    return float(coefficient) if _number(coefficient) and coefficient > 0 else None


def calculate_weighted_rating(snapshot: dict, *, cap_at_ten: bool = True) -> dict:
    """Weight genuine FotMob matches; never substitute a competition average.

    Each source rating is first divided by the position coefficient and, by
    default, capped at ten. The mean then repeats each corrected rating by its
    additive match weight. Unknown rated-match context prevents a final mean.
    """
    if type(cap_at_ten) is not bool:
        raise ValueError("Параметр ограничения оценки должен быть логическим значением.")
    competitions = snapshot.get("competitions")
    competitions = [row for row in competitions if isinstance(row, dict)] if isinstance(competitions, list) else []
    coefficient = _position_coefficient(snapshot)
    matches, unresolved, seen = [], [], set()
    missing_source_samples = deepcopy(snapshot.get("unmatched_fotmob_competitions", []))
    duplicates = 0
    for parent in competitions:
        history = parent.get("fotmob_matches")
        history_rows = [row for row in history if isinstance(row, dict)] if isinstance(history, list) else []
        expected = parent.get("rated_games")
        available = len({_identity(row.get("id")) for row in history_rows if _rating(row.get("rating")) is not None})
        missing_rating_field = any("rating" not in row for row in history_rows)
        missing = ((not history_rows and ((_number(expected) and expected > 0) or _rating(parent.get("rating")) is not None))
                   or missing_rating_field or (_number(expected) and expected > available))
        if missing:
            missing_source_samples.append({
                "code": "missing_source_sample", "competition_id": parent.get("id"), "competition": parent.get("name"),
                "expected_rated_games": expected if _number(expected) else None, "available_rated_games": available,
                "reason": "Сохранена неполная матчeвая история FotMob; требуется обновить источник.",
            })
        for match in history if isinstance(history, list) else []:
            if not isinstance(match, dict):
                continue
            identity = _identity(match.get("id"))
            if identity is not None and identity in seen:
                duplicates += 1
                continue
            if identity is not None:
                seen.add(identity)
            raw = _rating(match.get("rating"))
            context = resolve_match_context(match, competitions, parent=parent)
            weight, rules, issues = _match_weight(context)
            if identity is None:
                issues.append("У матча отсутствует ID FotMob; исключить повторный учёт невозможно.")
                weight = None
            uncapped = raw / coefficient if raw is not None and coefficient is not None else None
            corrected = min(10.0, uncapped) if cap_at_ten and uncapped is not None else uncapped
            included = raw is not None and weight is not None
            if raw is not None and issues:
                unresolved.append(identity)
            matches.append({**deepcopy(match), "weight": weight, "weight_rules": rules,
                            "raw_rating": raw, "adjusted_rating": corrected,
                            "uncapped_adjusted_rating": uncapped,
                            "cap_applied": bool(cap_at_ten and uncapped is not None and uncapped > 10),
                            "included": included, "context": deepcopy(context), "context_issues": issues})
    included = [row for row in matches if row["included"]]
    denominator = math.fsum(row["weight"] for row in included)
    raw_mean = math.fsum(row["raw_rating"] * row["weight"] for row in included) / denominator if denominator else None
    adjusted_mean = (math.fsum(row["adjusted_rating"] * row["weight"] for row in included) / denominator
                     if denominator and coefficient is not None else None)
    uncapped_mean = (math.fsum(row["uncapped_adjusted_rating"] * row["weight"] for row in included) / denominator
                    if denominator and coefficient is not None else None)
    rated_games = sum(row["raw_rating"] is not None for row in matches)
    warnings = []
    incomplete = bool(unresolved or missing_source_samples)
    if missing_source_samples:
        status = "missing_history"
        warnings.append("Матчевая история FotMob сохранена не полностью. Обновите данные игрока; взвешенная оценка не рассчитана.")
    elif unresolved:
        status = "incomplete_context"
        warnings.append(f"Для {len(unresolved)} матчей с оценкой не определён полный контекст. Взвешенная оценка не рассчитана.")
    elif not rated_games:
        status = "missing_history"
        warnings.append("Нет сохранённых матчевых оценок FotMob. Обновите данные игрока.")
    elif coefficient is None:
        status = "missing_position"
    else:
        status = "complete"
    if coefficient is None and rated_games:
        warnings.append("Позиция игрока не сопоставлена с коэффициентом; доступна только исходная взвешенная оценка.")
    return {
        "formula_version": FORMULA_VERSION, "status": status,
        "rating": adjusted_mean if status == "complete" else None,
        "raw_rating": raw_mean if not incomplete else None,
        "uncapped_rating": uncapped_mean if not incomplete else None,
        "position_coefficient": coefficient,
        "cap_applied": any(row["cap_applied"] for row in included), "cap_at_ten": cap_at_ten,
        "rated_games": rated_games, "unrated_games": len(matches) - rated_games,
        "included_games": len(included), "history_games": len(matches), "duplicate_games": duplicates,
        "total_weight": denominator, "unresolved_match_ids": unresolved,
        "missing_source_samples": missing_source_samples,
        "partial_raw_rating": raw_mean if incomplete else None,
        "partial_rating": adjusted_mean if incomplete else None,
        "matches": matches, "warnings": warnings,
    }
