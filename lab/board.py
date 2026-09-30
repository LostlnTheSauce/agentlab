"""The risk board: deterministic checks every pick must pass before the CEO sees it.

Mara  — price and evidence: still has value? quote fresh? books agree?
Barb  — money: stake limits, wallets, cold tipsters go paper-only.
Carl  — overlap: merges duplicate picks, flags conflicts and correlated bets.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import oddsmath as om
from .sources.odds import BOOK_NAMES
from .db import parse


BAD_PRICE = -0.03
FAIR_BAND = -0.015


def price_grade(edge: float) -> tuple[str, str]:
    if edge >= 0.01:
        return "beats", f"beats the market by {edge * 100:.2f}%."
    if edge >= FAIR_BAND:
        return "fair", f"fair price ({edge * 100:+.2f}% vs the no-vig market)."
    return "juice", f"full juice ({edge * 100:+.2f}% vs the no-vig market; fair is -1.50% or better). This one rides on the read, not the price."


def pick_key(p: dict) -> tuple:
    return (p["event_id"], p["market"], p["selection"], p.get("point"), p.get("player"))


def _side(p: dict) -> str | None:
    """Which team a pick is effectively backing, for correlation checks."""
    if p["market"] in ("h2h", "spreads") and p["selection"] not in ("Draw",):
        return p["selection"]
    return None


def review(new: list[dict], existing: list[dict], stats: dict, wallets: dict, settings, now: datetime) -> None:
    everyone = existing + new
    for p in new:
        notes: list[str] = []
        status = "cleared"
        paper_only = False

        def flag(msg):
            nonlocal status
            notes.append(msg)
            if status == "cleared":
                status = "flagged"

        def veto(msg):
            nonlocal status
            notes.append(msg)
            status = "vetoed"

        # --- Mara: price and evidence
        if parse(p["commence"]) <= now:
            veto("Mara: game has already started.")
        elif p.get("fair_prob") is None:
            veto("Mara: no market consensus to price this against.")
        elif p["edge"] < BAD_PRICE and p["agent"] != "coin":
            veto(f"Mara: bad price. {om.fmt(p['price'])} is {-p['edge'] * 100:.1f}% worse than fair {om.fmt(om.american_from_prob(p['fair_prob']))}, more than normal juice.")
        else:
            notes.append("Mara: " + price_grade(p["edge"])[1])
        if status != "vetoed":
            if p.get("quote_at"):
                age = (now - parse(p["quote_at"])).total_seconds() / 60
                if age > 45:
                    flag(f"Mara: the {BOOK_NAMES.get(p.get('book') or 'bovada', 'book')} quote is {age:.0f} minutes old. Re-check the price.")
            if p.get("estimated"):
                flag("Mara: fair price is estimated from a line gap, not direct quotes at this number.")
            elif (p.get("books") or 0) < settings.min_books:
                flag(f"Mara: only {p.get('books', 0)} books to compare against.")
            if (p.get("dispersion") or 0) > 0.06:
                flag("Mara: the comparison books disagree with each other more than usual.")

        # --- Barb: money
        st = stats.get(p["agent"], {})
        if p["agent"] == "coin":
            paper_only = True
            notes.append("Barb: benchmark, paper only.")
        elif st.get("graded", 0) >= 15 and st.get("units", 0) <= -3 and (st.get("clv") or 0) < 0:
            paper_only = True
            notes.append(f"Barb: {p['agent_name']} is on notice ({st['units']:+.1f}u, negative CLV). Paper only until they recover.")
        if p["stake_units"] > settings.max_units:
            p["stake_units"] = settings.max_units
            notes.append(f"Barb: stake capped at {settings.max_units}u.")
        stake = p["stake_units"] * settings.unit_dollars
        if not paper_only and wallets.get(p["agent"], 0) < stake:
            paper_only = True
            notes.append(f"Barb: {p['agent_name']}'s real wallet can't cover ${stake:.2f}. Paper only.")

        # --- Carl: overlap
        for q in everyone:
            if q is p or q["event_id"] != p["event_id"] or q.get("board_status") == "vetoed":
                continue
            if pick_key(q) == pick_key(p):
                leader = q.get("group_id") or q["id"]
                if q in new and new.index(q) > new.index(p):
                    continue
                p["group_id"] = leader
                notes.append(f"Carl: same bet as {q['agent_name']}. Merged; one real bet covers both.")
                break
            if q["market"] == p["market"] and q.get("player") == p.get("player") and q.get("point") is not None and p["market"] == "totals":
                if q["selection"] != p["selection"]:
                    flag(f"Carl: conflicts with {q['agent_name']}'s {q['selection']} {q['point']:g}.")
            elif _side(p) and _side(q):
                if _side(p) != _side(q):
                    flag(f"Carl: opposite side from {q['agent_name']}.")
                elif q["market"] != p["market"]:
                    flag(f"Carl: correlated with {q['agent_name']}'s {q['selection']} {q['market'].replace('h2h', 'moneyline').replace('spreads', 'spread')}.")
            elif p.get("player") and _side(q) or q.get("player") and _side(p):
                flag(f"Carl: player prop and team bet in the same game as {q['agent_name']}. They tend to move together.")

        p["board_status"] = status
        p["board_notes"] = notes
        p["paper_only"] = paper_only
    # count group sizes so the CEO can see consensus
    sizes: dict[str, int] = {}
    for p in everyone:
        lead = p.get("group_id") or p["id"]
        sizes[lead] = sizes.get(lead, 0) + 1
    for p in everyone:
        p["group_size"] = sizes.get(p.get("group_id") or p["id"], 1)


LEG_MIN = -0.025       # each parlay leg must be at least this close to fair
PARLAY_VETO = -0.045   # the whole parlay, compounded
PARLAY_REAL = -0.03    # combined edge needed before it can get real money


def _leg_txt(l: dict) -> str:
    if l["market"] == "spreads":
        return f"{l['selection']} {l['point']:+g}"
    if l["market"] == "totals":
        return f"{l['selection']} {l['point']:g}"
    return l["selection"]


def parlay_options(pool: list[dict], agent: dict, day: str, now: datetime, limit: int = 5) -> list[dict]:
    """Lydon's candidates: pairs of other tipsters' cleared straight picks from different games."""
    legs = [p for p in pool if p["board_status"] == "cleared" and not p.get("paper_only") and p["market"] in ("h2h", "spreads", "totals")
            and not p.get("player") and not p.get("estimated") and p.get("group_id") is None and p["agent"] not in ("coin", agent["id"])
            and p.get("fair_prob") and (p.get("edge") if p.get("edge") is not None else -1) >= LEG_MIN
            and parse(p["commence"]) > now + timedelta(minutes=15)]
    out = []
    for i, a in enumerate(legs):
        for b in legs[i + 1:]:
            if a["event_id"] == b["event_id"]:
                continue
            d = om.dec(a["price"]) * om.dec(b["price"])
            fair = a["fair_prob"] * b["fair_prob"]
            edge = fair * d - 1
            if edge < PARLAY_VETO:
                continue
            x, y = sorted((a, b), key=lambda l: l["commence"])
            out.append({
                "id": f"parlay-{day}-{x['id'][:5]}{y['id'][:5]}", "agent": agent["id"], "agent_name": agent["name"],
                "event_id": x["event_id"], "sport": x["sport"], "home": x["home"], "away": x["away"], "commence": x["commence"],
                "market": "parlay", "selection": f"{_leg_txt(x)} + {_leg_txt(y)}", "point": None, "player": None,
                "price": om.american_from_dec(d), "fair_prob": fair, "edge": edge, "min_price": None,
                "books": min(x["books"] or 0, y["books"] or 0), "estimated": True, "quote_at": None, "dispersion": None,
                "legs": [x["id"], y["id"]], "leg_picks": [x, y],
                "signal": (f"Leg 1: {x['agent_name']}'s {_leg_txt(x)} {om.fmt(x['price'])} ({x['away']} at {x['home']}), "
                           f"{x['edge'] * 100:+.1f}% vs fair. Their case: {x['reasoning'][:220]} "
                           f"Leg 2: {y['agent_name']}'s {_leg_txt(y)} {om.fmt(y['price'])} ({y['away']} at {y['home']}), "
                           f"{y['edge'] * 100:+.1f}% vs fair. Their case: {y['reasoning'][:220]} "
                           f"Combined: {om.fmt(om.american_from_dec(d))}, {edge * 100:+.1f}% vs fair."),
            })
    out.sort(key=lambda p: p["edge"], reverse=True)
    return out[:limit]


def finalize_parlay(p: dict) -> dict:
    """The board's verdict on the parlay Lydon chose."""
    notes = ["Mara: the price multiplies the two straight prices. Bovada's parlay price may differ slightly; enter what you actually get."]
    p["paper_only"] = p["edge"] < PARLAY_REAL
    if p["paper_only"]:
        notes.append(f"Barb: combined {p['edge'] * 100:+.1f}% vs fair. Parlay juice stacks up, so this one rides on paper (real money needs {PARLAY_REAL * 100:.0f}% or better).")
    else:
        notes.append(f"Mara: combined {p['edge'] * 100:+.1f}% vs fair, no worse than a normal single bet.")
    p.update(board_status="cleared", board_notes=notes, group_size=1, stake_units=1)
    p.pop("leg_picks", None)
    return p
