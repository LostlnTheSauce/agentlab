"""Plain-English "what needs to happen" for a bet."""

from __future__ import annotations

import math

UNITS = {"baseball_mlb": "runs", "soccer_epl": "goals", "soccer_usa_mls": "goals", "soccer_uefa_champs_league": "goals"}
PROP_WORDS = {"player_receptions": "catches", "player_reception_yds": "receiving yards", "player_pass_yds": "passing yards",
              "player_rush_yds": "rushing yards"}


def _short(team: str) -> str:
    return team  # full names: shortening breaks "Boston Red Sox" and makes "Los Angeles" ambiguous


def _u(n: int, unit: str) -> str:
    return unit[:-1] if n == 1 else unit


def _n(x: float) -> str:
    return f"{x:g}"


def needs(market: str, selection: str, point, sport: str = "", player: str | None = None) -> str:
    unit = UNITS.get(sport, "points")
    soccer = sport.startswith("soccer_")
    if market == "parlay":
        return "Every leg has to win. If one leg pushes, the parlay pays on the rest."
    if player:
        what = PROP_WORDS.get(market, market.replace("player_", "").replace("_", " "))
        if point is None:
            return f"{player} {selection.lower()} {what}."
        if float(point).is_integer():
            line = int(point)
            return (f"{player} needs {line + 1}+ {what} (exactly {line} is a push)." if selection == "Over"
                    else f"{player} needs {line - 1} or fewer {what} (exactly {line} is a push).")
        return (f"{player} needs {math.ceil(point)}+ {what}." if selection == "Over"
                else f"{player} needs {math.floor(point)} or fewer {what}.")
    if market == "h2h":
        if selection == "Draw":
            return "Needs a tie after regular time (90 minutes plus stoppage)."
        if soccer:
            return f"{_short(selection)} must win in regular time. A draw loses."
        return f"{_short(selection)} must win."
    if market == "spreads" and point is not None:
        team, p = _short(selection), float(point)
        if p < 0:
            if p.is_integer():
                return f"{team} must win by {int(-p) + 1}+ {unit}. Winning by exactly {int(-p)} is a push (money back)."
            return f"{team} must win by {math.ceil(-p)} or more {_u(math.ceil(-p), unit)}."
        if p.is_integer():
            if p == 0:
                return f"{team} must win. A tie is a push (money back)."
            return f"{team} can lose by up to {int(p) - 1} {_u(int(p) - 1, unit)}, or win. Losing by exactly {int(p)} is a push."
        lose_by = math.floor(p)
        return f"{team} can lose by up to {lose_by} {_u(lose_by, unit)}, or win." if lose_by >= 1 else f"{team} must win or tie."
    if market == "totals" and point is not None:
        p = float(point)
        if p.is_integer():
            line = int(p)
            return (f"Combined score must be {line + 1}+ {unit}. Exactly {line} is a push." if selection == "Over"
                    else f"Combined score must be {line - 1} or fewer {unit}. Exactly {line} is a push.")
        return (f"Combined score must be {math.ceil(p)} or more {unit}." if selection == "Over"
                else f"Combined score must be {math.floor(p)} or fewer {unit}.")
    return ""
