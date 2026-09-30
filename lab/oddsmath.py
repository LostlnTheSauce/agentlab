"""Pure odds arithmetic: American prices, no-vig probabilities, edge, grading, payouts."""

from __future__ import annotations

import math


def dec(american: int | float) -> float:
    a = float(american)
    if a == 0 or -100 < a < 100:
        raise ValueError(f"Invalid American price: {american}")
    return 1 + a / 100 if a > 0 else 1 + 100 / -a


def implied(american: int | float) -> float:
    return 1 / dec(american)


def american_from_dec(d: float) -> int:
    if d <= 1:
        raise ValueError("Decimal odds must exceed 1")
    return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))


def american_from_prob(p: float) -> int:
    return american_from_dec(1 / p)


def no_vig(prices: list[int]) -> list[float]:
    """Strip the bookmaker margin proportionally from a complete market."""
    raw = [implied(p) for p in prices]
    total = sum(raw)
    return [r / total for r in raw]


def edge(fair_prob: float, american: int) -> float:
    """Expected return per dollar if fair_prob is the true probability."""
    return fair_prob * dec(american) - 1


def min_price(fair_prob: float, margin: float = 0.01) -> int:
    """Worst American price that still carries `margin` expected value. Rounded conservatively."""
    need = (1 + margin) / fair_prob
    if need >= 2:
        return math.ceil((need - 1) * 100)
    return -math.floor(100 / (need - 1))


def price_at_least(american: int, floor: int) -> bool:
    """True when `american` pays at least as well as `floor`."""
    return dec(american) >= dec(floor) - 1e-9


def fmt(american: int | None) -> str:
    if american is None:
        return "—"
    return f"+{american}" if american > 0 else str(american)


def grade(market: str, selection: str, point: float | None, home: str, away: str, home_score: float, away_score: float) -> str:
    """Grade a full-game single. Returns win / loss / push."""
    if market == "h2h":
        if selection == "Draw":
            return "win" if home_score == away_score else "loss"
        mine, theirs = (home_score, away_score) if selection == home else (away_score, home_score)
        if selection not in (home, away):
            raise ValueError(f"Selection {selection!r} is not a team in this game")
        return "win" if mine > theirs else "loss" if mine < theirs else "push"
    if market == "spreads":
        if selection not in (home, away) or point is None:
            raise ValueError("Spread needs a team and a point")
        margin = (home_score - away_score if selection == home else away_score - home_score) + point
        return "win" if margin > 0 else "loss" if margin < 0 else "push"
    if market == "totals":
        if selection not in ("Over", "Under") or point is None:
            raise ValueError("Total needs Over/Under and a point")
        total = home_score + away_score
        if total == point:
            return "push"
        return "win" if (total > point) == (selection == "Over") else "loss"
    raise ValueError(f"Unsupported market {market}")


def grade_prop(selection: str, point: float, actual: float) -> str:
    if actual == point:
        return "push"
    return "win" if (actual > point) == (selection == "Over") else "loss"


def parlay_result(leg_results: list[str], leg_prices: list[int]) -> tuple[str, float | None]:
    """Returns (result, effective decimal odds). Pushed legs drop out of the parlay."""
    if any(r == "loss" for r in leg_results):
        return "loss", None
    live = [p for r, p in zip(leg_results, leg_prices) if r == "win"]
    if not live:
        return "push", None
    d = 1.0
    for p in live:
        d *= dec(p)
    return "win", d


def profit_cents(stake_cents: int, american: int | None, result: str, decimal_override: float | None = None) -> int:
    if result == "loss":
        return -stake_cents
    if result in ("push", "void"):
        return 0
    d = decimal_override if decimal_override else dec(american)
    return round(stake_cents * (d - 1))


def split_cents(total: int, n: int) -> list[int]:
    base, extra = divmod(total, n)
    return [base + (1 if i < extra else 0) for i in range(n)]
