"""Each tipster's strategy as a deterministic filter over today's candidates.

A strategy returns *options*: candidate bets plus a plain-English signal explaining why this agent
cares. The agent (LLM or offline fallback) then decides which options, if any, to actually pick.
Keeping the filter deterministic means no agent can bet on something outside its lane, and nobody
can invent a price.
"""

from __future__ import annotations

import random
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import oddsmath as om
from .db import parse
from .roster import NFL, CFB, MLB, NBA, SOCCER

MAX_OPTIONS = 6
# Bovada's cut means a typical bet prices around -2% against the no-vig consensus. Signal-based agents may bet
# anything up to normal juice; the risk board vetoes worse. Pure-value agents demand better.
JUICE = -0.03
KEYS = {NFL: {3: 0.045, 7: 0.03, 10: 0.015, 14: 0.012}, CFB: {3: 0.025, 7: 0.02, 10: 0.012, 14: 0.012}}
INJURY_WEIGHT = {"QB": 4.0}
STATUS_WEIGHT = {"out": 1.0, "doubtful": 0.7, "questionable": 0.25, "injured reserve": 0.0}


def priced(c: dict, min_edge: float = JUICE, max_disp: float = 0.08) -> bool:
    return c.get("fair_prob") is not None and c["edge"] >= min_edge and (c.get("dispersion") or 0) <= max_disp


def opt(c: dict, signal: str, score: float) -> dict:
    return {"cand": c, "signal": signal, "score": score}


def team_side(cands: list[dict], event_id: str, team: str, min_edge: float = JUICE) -> dict | None:
    """Best-priced way to back `team`: spread or moneyline."""
    best = None
    for c in cands:
        if c["event_id"] == event_id and c["selection"] == team and c["market"] in ("spreads", "h2h") and priced(c, min_edge):
            if best is None or c["edge"] > best["edge"]:
                best = c
    return best


def pct(p: float) -> str:
    return f"{p * 100:.0f}%"


def price_note(c: dict) -> str:
    if c.get("fair_prob") is None:
        return f"Bovada {om.fmt(c['price'])}"
    return f"Bovada {om.fmt(c['price'])} vs fair {om.fmt(om.american_from_prob(c['fair_prob']))} ({c['books']} books, edge {c['edge'] * 100:+.1f}%)"


def with_estimate(c: dict, fair: float) -> dict:
    c = dict(c)
    c.update(fair_prob=fair, edge=om.edge(fair, c["price"]), min_price=om.min_price(fair), estimated=True)
    return c


# ---------------------------------------------------------------- strategies

def rhea(cands, ctx, **_):
    out = []
    for ev_id, cx in ctx.items():
        if cx.get("sport") != NFL or not cx.get("injuries"):
            continue
        load = {}
        for team, rows in cx["injuries"].items():
            total, names = 0.0, []
            for r in rows:
                w = STATUS_WEIGHT.get(r["status"].lower(), 0.0) * INJURY_WEIGHT.get(r["pos"], 1.0)
                if w >= 0.7:
                    names.append(f"{r['name']} ({r['pos']}, {r['status']})")
                total += w
            load[team] = (total, names)
        home, away = cx["home"], cx["away"]
        lh, la = load.get(home, (0, [])), load.get(away, (0, []))
        diff = lh[0] - la[0]
        if abs(diff) < 2:
            continue
        healthy, hurt, hurt_names = (away, home, lh[1]) if diff > 0 else (home, away, la[1])
        c = team_side(cands, ev_id, healthy)
        if c:
            sig = f"{hurt} injury load {max(lh[0], la[0]):.1f} vs {min(lh[0], la[0]):.1f}. Missing: {', '.join(hurt_names[:4]) or 'depth players'}."
            out.append(opt(c, sig, abs(diff) + c["edge"] * 100))
    return out


def quinn(cands, ctx, agent, **_):
    return [opt(c, price_note(c), c["edge"] * 100) for c in cands
            if c["sport"] in agent["sports"] and c["market"] in ("h2h", "spreads", "totals")
            and priced(c, 0.01, 0.05) and c["books"] >= 4]


def stormy(cands, ctx, **_):
    out = []
    for c in cands:
        cx = ctx.get(c["event_id"], {})
        w = cx.get("weather")
        if c["sport"] not in (NFL, CFB) or c["market"] != "totals" or c["selection"] != "Under" or not w or cx.get("indoor"):
            continue
        wind, gust, rain = w.get("wind_mph") or 0, w.get("gust_mph") or 0, w.get("precip_pct") or 0
        if (wind >= 15 or gust >= 25 or rain >= 70) and priced(c):
            sig = f"Kickoff forecast: wind {wind:.0f} mph, gusts {gust:.0f}, {rain:.0f}% rain, {w.get('temp_f', 0):.0f}°F. {price_note(c)}"
            out.append(opt(c, sig, wind + gust / 2 + rain / 10 + c["edge"] * 100))
    return out


def connie(cands, ctx, agent, moves=None, **_):
    out = []
    moves = moves or {}
    for c in cands:
        if c["sport"] not in agent["sports"] or c["market"] not in ("h2h", "spreads") or not priced(c):
            continue
        # follow the move: the market has shifted toward this side since the line opened (that is mostly sharp money)
        mv = moves.get((c["event_id"], c["selection"]))
        if mv is not None and mv >= 0.025:
            out.append(opt(c, f"Market has moved {mv * 100:+.1f} pts toward {c['selection']} since open. {price_note(c)}", mv * 100 + c["edge"] * 100))
    return out


TZ_OFFSET = {"ET": 0, "CT": 1, "MT": 2, "PT": 3}


def ray(cands, ctx, **_):
    out = []
    for ev_id, cx in ctx.items():
        if cx.get("sport") != NFL:
            continue
        home, away = cx["home"], cx["away"]
        reasons, fade = [], None
        kick_et = parse(cx["commence"]).astimezone(ZoneInfo("America/New_York"))
        away_tz, venue_tz = cx.get("away_tz"), cx.get("venue_tz")
        if away_tz in ("PT", "MT") and venue_tz == "ET" and kick_et.hour < 14:
            fade = away
            reasons.append(f"{away} body clock says {kick_et.hour - TZ_OFFSET[away_tz]}:{kick_et.minute:02d} AM at kickoff")
        rest = cx.get("rest") or {}
        rh, ra = rest.get(home), rest.get(away)
        if rh is not None and ra is not None and abs(rh - ra) >= 3:
            tired = home if rh < ra else away
            if fade in (None, tired):
                fade = tired
                reasons.append(f"rest {rh} days ({home}) vs {ra} days ({away})")
        if not fade:
            continue
        c = team_side(cands, ev_id, away if fade == home else home)
        if c:
            out.append(opt(c, f"Fade {fade}: {'; '.join(reasons)}. {price_note(c)}", len(reasons) * 3 + c["edge"] * 100))
    return out


def june(cands, ctx, **_):
    out = []
    for c in cands:
        cx = ctx.get(c["event_id"], {})
        pred = cx.get("predictor")
        if c["sport"] != CFB or c["market"] != "h2h" or not pred or c.get("fair_prob") is None:
            continue
        mine = pred["home"] if c["selection"] == cx.get("home") else pred["away"]
        gap = mine - c["fair_prob"]
        if gap >= 0.05:
            side = team_side(cands, c["event_id"], c["selection"])
            if side:
                rec = f"{cx.get('home')} {cx.get('home_record') or '?'}, {cx.get('away')} {cx.get('away_record') or '?'}"
                out.append(opt(side, f"ESPN predictor gives {c['selection']} {pct(mine)} vs market {pct(c['fair_prob'])}. Records: {rec}. {price_note(side)}", gap * 100 + side["edge"] * 100))
    return out


def chaos(cands, ctx, **_):
    return [opt(c, f"Road dog {c['selection']} +{c['point']:g}. {price_note(c)}", c["edge"] * 100 + c["point"] / 5)
            for c in cands if c["sport"] == CFB and c["market"] == "spreads" and c["selection"] == c["away"]
            and c["point"] is not None and 3 <= c["point"] <= 17 and priced(c, -0.02)]


def theo(cands, ctx, agent, **_):
    out = []
    for c in cands:
        if c["sport"] not in agent["sports"] or c["market"] != "totals" or c["point"] is None or c.get("consensus_point") is None:
            continue
        diff = c["consensus_point"] - c["point"]  # positive: Bovada lower than market
        if (c["selection"] == "Over" and diff >= 1) or (c["selection"] == "Under" and diff <= -1):
            cc = c if c.get("fair_prob") is not None else with_estimate(c, min(0.5 + 0.025 * abs(diff), 0.6))
            if cc["edge"] >= 0.0 and c["price"] >= -125:
                note = " (fair estimated from line gap)" if cc["estimated"] else ""
                out.append(opt(cc, f"Bovada total {c['point']:g}, market {c['consensus_point']:g}: {abs(diff):g}-point gap favors the {c['selection'].lower()}. {price_note(cc)}{note}", abs(diff) * 3 + cc["edge"] * 100))
    return out


def pete(cands, ctx, agent, **_):
    out = []
    for c in cands:
        if c["sport"] not in agent["sports"] or c["market"] != "spreads" or c["point"] is None or c.get("consensus_point") is None:
            continue
        p, q = c["point"], c["consensus_point"]
        if p <= q:
            continue
        crossed = [k for k in KEYS[c["sport"]] if q <= k < p or q < -k <= p]
        if not crossed:
            continue
        value = sum(KEYS[c["sport"]][k] for k in crossed)
        cc = c if c.get("fair_prob") is not None else with_estimate(c, min(0.5 + value, 0.6))
        if cc["edge"] >= 0.0:
            out.append(opt(cc, f"Bovada {c['selection']} {p:+g} vs market {q:+g}, through the {', '.join(map(str, crossed))}. {price_note(cc)}", value * 100 + cc["edge"] * 100))
    return out


def mateo(cands, ctx, **_):
    out = []
    for c in cands:
        cx = ctx.get(c["event_id"], {})
        pit = cx.get("pitchers") or {}
        if c["sport"] != MLB or c["market"] != "h2h" or not priced(c) or len(pit) < 2:
            continue
        me, them = pit.get(c["selection"]), pit.get(c["away"] if c["selection"] == c["home"] else c["home"])
        if not me or not them or me["ip"] < 30 or them["ip"] < 30:
            continue
        fip_gap, whip_gap = them["fip"] - me["fip"], them["whip"] - me["whip"]
        if fip_gap >= 0.75 and whip_gap >= 0.1:
            sig = f"{me['name']} (FIP {me['fip']}, WHIP {me['whip']}) vs {them['name']} (FIP {them['fip']}, WHIP {them['whip']}). {price_note(c)}"
            out.append(opt(c, sig, fip_gap * 3 + c["edge"] * 100))
    return out


def bill(cands, ctx, **_):
    out = []
    for c in cands:
        cx = ctx.get(c["event_id"], {})
        pen = cx.get("bullpen") or {}
        if c["sport"] != MLB or c["market"] != "h2h" or not priced(c):
            continue
        opp = c["away"] if c["selection"] == c["home"] else c["home"]
        mine, theirs = pen.get(c["selection"]), pen.get(opp)
        if mine is None or theirs is None:
            continue
        if theirs >= 10 and mine <= 7:
            out.append(opt(c, f"{opp} bullpen threw {theirs:g} IP in the last 3 days; {c['selection']} only {mine:g}. {price_note(c)}", theirs - mine + c["edge"] * 100))
    return out


def blue(cands, ctx, agent, **_):
    return [opt(c, f"Under {c['point']:g}. {price_note(c)}", c["edge"] * 100)
            for c in cands if c["sport"] in agent["sports"] and c["market"] == "totals" and c["selection"] == "Under" and priced(c, -0.012)]


def pat(cands, ctx, **_):
    out = []
    for c in cands:
        cx = ctx.get(c["event_id"], {})
        w = cx.get("weather")
        if c["sport"] != MLB or c["market"] != "totals" or not w or cx.get("indoor") or not priced(c):
            continue
        t, wind = w.get("temp_f") or 70, w.get("wind_mph") or 0
        side = "Over" if t >= 85 else "Under" if t <= 55 else None
        if side and c["selection"] == side:
            out.append(opt(c, f"First pitch forecast {t:.0f}°F, wind {wind:.0f} mph at {cx.get('venue') or 'the park'}. {price_note(c)}", abs(t - 70) / 3 + c["edge"] * 100))
    return out


def bev(cands, ctx, **_):
    out = []
    for ev_id, cx in ctx.items():
        if cx.get("sport") != NBA:
            continue
        rest = cx.get("rest") or {}
        rh, ra = rest.get(cx["home"]), rest.get(cx["away"])
        if rh is None or ra is None:
            continue
        if min(rh, ra) <= 1 and max(rh, ra) >= 2:
            tired = cx["home"] if rh <= 1 else cx["away"]
            fresh = cx["away"] if tired == cx["home"] else cx["home"]
            c = team_side(cands, ev_id, fresh)
            if c:
                out.append(opt(c, f"{tired} on a back-to-back, {fresh} rested {max(rh, ra)} days. {price_note(c)}", 3 + c["edge"] * 100))
    return out


def dmitri(cands, ctx, **_):
    return [opt(c, f"Draw fair {pct(c['fair_prob'])}. {price_note(c)}", c["edge"] * 100)
            for c in cands if c["sport"] in SOCCER and c["market"] == "h2h" and c["selection"] == "Draw"
            and priced(c, -0.02) and c["fair_prob"] >= 0.25]


def greta(cands, ctx, **_):
    return [opt(c, price_note(c), c["edge"] * 100)
            for c in cands if c["sport"] in SOCCER and c["selection"] != "Draw" and priced(c, -0.01, 0.06)]


# No-vig math overrates longshots (books shade big underdogs more than favorites), so past about +300 the "edge" is
# mostly an artifact.
URSULA_MAX = 300


def ursula(cands, ctx, agent, **_):
    return [opt(c, f"{c['selection']} at {om.fmt(c['price'])}. {price_note(c)}", c["edge"] * 100 + c["price"] / 100)
            for c in cands if c["sport"] in agent["sports"] and c["market"] == "h2h" and c["selection"] != "Draw"
            and 120 <= c["price"] <= URSULA_MAX and priced(c, -0.02)]


def goblin(cands, ctx, props=None, **_):
    return [opt(c, f"{c['player']} {c['selection']} {c['point']:g} ({c['market'].replace('player_', '').replace('_', ' ')}). {price_note(c)}", c["edge"] * 100)
            for c in (props or []) if priced(c, 0.0, 0.06)]


def coin(cands, ctx, agent, day="", **_):
    pool = [c for c in cands if c.get("fair_prob") is not None and c["market"] in ("h2h", "spreads", "totals")]
    if not pool:
        return []
    c = random.Random(f"{day}-coin").choice(sorted(pool, key=lambda x: x["id"]))
    return [opt(c, "Heads.", 0)]


# ------------------------------------------------------------------ new hires: a strategy built from safe parts

SIGNALS = ("none", "wind", "heat", "cold", "injuries", "rest", "predictor", "pitching", "line_move")
SIDES = ("any", "favorite", "underdog", "home", "away", "over", "under")
MARKETS = ("h2h", "spreads", "totals")


def _signal(kind: str, c: dict, cx: dict, moves: dict) -> str | None:
    """Plain-English reason this candidate fits the signal, or None."""
    w = cx.get("weather") or {}
    opp = c["away"] if c["selection"] == c["home"] else c["home"]
    if kind == "none":
        return price_note(c)
    if kind == "wind" and not cx.get("indoor") and ((w.get("wind_mph") or 0) >= 15 or (w.get("gust_mph") or 0) >= 25):
        return f"Wind {w.get('wind_mph', 0):.0f} mph, gusts {w.get('gust_mph', 0):.0f}."
    if kind == "heat" and not cx.get("indoor") and (w.get("temp_f") or 0) >= 85:
        return f"{w['temp_f']:.0f}°F at game time."
    if kind == "cold" and not cx.get("indoor") and w.get("temp_f") is not None and w["temp_f"] <= 40:
        return f"{w['temp_f']:.0f}°F at game time."
    if kind == "injuries" and cx.get("injuries") and c["market"] != "totals":
        def load(team):
            return sum(STATUS_WEIGHT.get(r["status"].lower(), 0) * INJURY_WEIGHT.get(r["pos"], 1.0) for r in cx["injuries"].get(team, []))
        mine, theirs = load(c["selection"]), load(opp)
        if theirs - mine >= 2:
            return f"{opp} injury load {theirs:.1f} vs {mine:.1f}."
    if kind == "rest" and cx.get("rest") and c["market"] != "totals":
        mine, theirs = cx["rest"].get(c["selection"]), cx["rest"].get(opp)
        if mine is not None and theirs is not None and mine - theirs >= 2:
            return f"{c['selection']} rested {mine} days, {opp} {theirs}."
    if kind == "predictor" and cx.get("predictor") and c["market"] == "h2h" and c.get("fair_prob"):
        mine = cx["predictor"]["home"] if c["selection"] == c["home"] else cx["predictor"]["away"]
        if mine - c["fair_prob"] >= 0.05:
            return f"ESPN predictor {pct(mine)} vs market {pct(c['fair_prob'])}."
    if kind == "pitching" and cx.get("pitchers") and c["market"] != "totals":
        me, them = cx["pitchers"].get(c["selection"]), cx["pitchers"].get(opp)
        if me and them and them["fip"] - me["fip"] >= 0.75:
            return f"{me['name']} FIP {me['fip']} vs {them['name']} {them['fip']}."
    if kind == "line_move":
        mv = moves.get((c["event_id"], c["selection"]))
        if mv is not None and mv >= 0.025:
            return f"Market moved {mv * 100:+.1f} pts toward {c['selection']} since open."
    return None


def custom(cands, ctx, agent, moves=None, **_):
    prm = agent.get("params") or {}
    sports = set(prm.get("sports") or agent.get("sports") or [])
    markets = set(prm.get("markets") or MARKETS)
    side = prm.get("side", "any")
    lo, hi = int(prm.get("min_price", -300)), int(prm.get("max_price", 500))
    min_edge = float(prm.get("min_edge", -0.02))
    kind = prm.get("signal", "none")
    out = []
    for c in cands:
        if c["sport"] not in sports or c["market"] not in markets or c["selection"] == "Draw" or not priced(c, min_edge):
            continue
        if not (om.dec(lo) <= om.dec(c["price"]) <= om.dec(hi)):
            continue
        fav = c["price"] < 0 or (c.get("point") is not None and c["market"] == "spreads" and c["point"] < 0)
        if side == "favorite" and (c["market"] == "totals" or not fav) or side == "underdog" and (c["market"] == "totals" or fav):
            continue
        if side in ("home", "away") and c["selection"] != c[side] or side in ("over", "under") and c["selection"].lower() != side:
            continue
        why = _signal(kind, c, ctx.get(c["event_id"], {}), moves or {})
        if why:
            out.append(opt(c, why if kind == "none" else f"{why} {price_note(c)}", c["edge"] * 100 + (0 if kind == "none" else 2)))
    return out


def validate_params(prm: dict, sports_allowed: list[str]) -> dict:
    """Clamp a designed strategy to things the code can actually evaluate."""
    sp = [x for x in (prm.get("sports") or []) if x in sports_allowed] or list(sports_allowed)
    mk = [x for x in (prm.get("markets") or []) if x in MARKETS] or list(MARKETS)
    lo, hi = int(prm.get("min_price", -300)), int(prm.get("max_price", 500))
    lo, hi = max(-400, min(lo, 400)), max(-150, min(hi, 1000))
    if -100 < lo < 100:
        lo = -100
    if -100 < hi < 100:
        hi = 100
    if om.dec(lo) > om.dec(hi):
        lo, hi = hi, lo
    return {"sports": sp, "markets": mk, "side": prm.get("side") if prm.get("side") in SIDES else "any",
            "min_price": lo, "max_price": hi, "min_edge": min(max(float(prm.get("min_edge", -0.02)), -0.03), 0.02),
            "signal": prm.get("signal") if prm.get("signal") in SIGNALS else "none"}


STRATEGIES = {
    "rhea": rhea, "quinn": quinn, "stormy": stormy, "connie": connie, "ray": ray, "june": june, "chaos": chaos,
    "theo": theo, "pete": pete, "mateo": mateo, "bill": bill, "blue": blue, "pat": pat, "bev": bev,
    "dmitri": dmitri, "greta": greta, "goblin": goblin, "ursula": ursula, "coin": coin, "custom": custom,
}


def options_for(agent: dict, cands: list[dict], ctx: dict, *, taken: set, **extra) -> list[dict]:
    fn = STRATEGIES.get(agent.get("strategy", agent["id"]))
    if not fn:
        return []
    raw = fn(cands, ctx, agent=agent, **extra)
    best: dict[str, dict] = {}
    for o in sorted(raw, key=lambda o: -o["score"]):
        ev = o["cand"]["event_id"]
        if ev in taken or ev in best:
            continue
        best[ev] = o
    return list(best.values())[:MAX_OPTIONS]
