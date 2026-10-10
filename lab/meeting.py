"""The Sunday night board meeting: weekly report, an MVP, at most one firing, and a new hire for the empty seat.

It has two halves. The paper meeting covers every pick the firm made and decides the firing (paper has the
sample size). The cash meeting covers only the bets the owner put real money on that week.

Firing is only allowed from a deterministic shortlist (enough graded bets, and losing money with negative CLV,
or an empty real wallet), so a bad week of luck alone can't get anyone fired. The Commish decides whether to
fire from that shortlist, and designs the replacement from a menu of safe strategy parts.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import ledger, notify, roster
from .agents import describe
from .db import DB, iso, parse, utcnow
from .roster import CFB, MLB, NBA, NFL, SOCCER
from .strategies import MARKETS, SIDES, SIGNALS, validate_params

DESK_SPORTS = {"nfl": [NFL], "cfb": [CFB], "mlb": [MLB], "hs": [NBA, *SOCCER], "lab": [NFL, CFB, MLB, NBA, *SOCCER]}
SKINS = ["#f3d2b4", "#ecc19a", "#e0af83", "#d9a07c", "#c58f6a", "#bf8c66", "#a8744f", "#8d5a3b", "#7d4d33"]
PROTECTED = {"coin", "lydon"}  # the benchmark and the parlay guy aren't strategies that can be "fixed" by a hire


def week_key(settings, now: datetime) -> str:
    local = now.astimezone(ZoneInfo(settings.timezone)).date()
    return (local + timedelta(days=(6 - local.weekday()) % 7)).isoformat()  # the Sunday that closes this week


def due(db: DB, settings, now: datetime | None = None) -> bool:
    now = now or utcnow()
    local = now.astimezone(ZoneInfo(settings.timezone))
    return local.weekday() == 6 and local.hour >= settings.meeting_hour and not db.one("SELECT 1 FROM meetings WHERE week=?", (week_key(settings, now),))


def week_stats(db: DB, settings, now: datetime) -> dict[str, dict]:
    since = iso(now - timedelta(days=7))
    out: dict[str, dict] = {}
    rows = db.all("SELECT p.agent, p.result, p.clv, COALESCE(SUM(b.profit_cents),0) AS pr FROM picks p "
                  "LEFT JOIN bets b ON b.pick_id=p.id AND b.kind='paper' WHERE p.result IS NOT NULL AND p.graded_at>? GROUP BY p.id", (since,))
    for r in rows:
        s = out.setdefault(r["agent"], {"w": 0, "l": 0, "p": 0, "units": 0.0, "clv": [], "n": 0})
        s["n"] += 1
        s[{"win": "w", "loss": "l"}.get(r["result"], "p")] += 1
        s["units"] += r["pr"] / (settings.unit_dollars * 100)
        if r["clv"] is not None:
            s["clv"].append(r["clv"])
    for s in out.values():
        s["clv"] = sum(s["clv"]) / len(s["clv"]) if s["clv"] else None
    return out


def cash_week(db: DB, now: datetime) -> dict:
    """The owner's real-money bets that settled in the 7 days before `now`. A bet merged across tipsters is one
    bet in the totals and a share on each tipster's line."""
    rows = db.all("SELECT b.agent, b.stake_cents, b.profit_cents, b.result, b.placed_at, b.price AS got, p.* FROM bets b JOIN picks p ON p.id=b.pick_id "
                  "WHERE b.kind='real' AND b.result IS NOT NULL AND b.settled_at>? AND b.settled_at<=? ORDER BY b.settled_at, b.id",
                  (iso(now - timedelta(days=7)), iso(now)))
    bets: dict[tuple, dict] = {}
    by_agent: dict[str, dict] = {}
    for r in rows:
        k = (r["placed_at"], r["group_id"] or r["id"])
        b = bets.setdefault(k, {"bet": describe({**r, "price": r["got"]}), "result": r["result"], "stake": 0.0, "profit": 0.0, "ceo": False})
        b["stake"] += r["stake_cents"] / 100
        b["profit"] += (r["profit_cents"] or 0) / 100
        b["ceo"] = b["ceo"] or bool(r["real_pick"])
        a = by_agent.setdefault(r["agent"], {"w": 0, "l": 0, "p": 0, "profit": 0.0, "staked": 0.0})
        a[{"win": "w", "loss": "l"}.get(r["result"], "p")] += 1
        a["profit"] += (r["profit_cents"] or 0) / 100
        a["staked"] += r["stake_cents"] / 100

    def tally(items):
        t = {"n": len(items), "w": sum(b["result"] == "win" for b in items), "l": sum(b["result"] == "loss" for b in items),
             "staked": round(sum(b["stake"] for b in items), 2), "profit": round(sum(b["profit"] for b in items), 2)}
        t["p"] = t["n"] - t["w"] - t["l"]
        return t

    items = list(bets.values())
    for a in by_agent.values():
        a["profit"], a["staked"] = round(a["profit"], 2), round(a["staked"], 2)
    ranked = sorted(items, key=lambda b: b["profit"])
    return {"all": tally(items), "ceo": tally([b for b in items if b["ceo"]]), "own": tally([b for b in items if not b["ceo"]]),
            "agents": by_agent,
            "best": {"bet": ranked[-1]["bet"], "profit": round(ranked[-1]["profit"], 2)} if items and ranked[-1]["profit"] > 0 else None,
            "worst": {"bet": ranked[0]["bet"], "profit": round(ranked[0]["profit"], 2)} if items and ranked[0]["profit"] < 0 else None}


def _rec(t: dict) -> str:
    return f"{t['w']}-{t['l']}" + (f"-{t['p']}" if t["p"] else "")


def _usd(v: float) -> str:
    return f"{'+' if v > 0 else '-' if v < 0 else ''}${abs(v):.2f}"


CASH_SCHEMA = lambda ids: {
    "type": "object",
    "properties": {"report": {"type": "string"}, "mvp": {"type": "string", "enum": ids + [""]}},
    "required": ["report", "mvp"],
    "additionalProperties": False,
}


def cash_meeting(brain, settings, db: DB, team: list[dict], now: datetime) -> dict:
    """Minutes for the real-money half: how the owner's cash did, which tipsters earned or cost it, CEO picks vs own calls."""
    wk = cash_week(db, now)
    names = {t["id"]: t["name"] for t in roster.load(db)}
    tot, agents = wk["all"], wk["agents"]
    out = {"week": wk, "report": "", "mvp": "", "mvp_name": None}
    if not tot["n"]:
        out["report"] = "No cash bets settled this week, so there is nothing to review. The paper meeting covers what the desks did."
        return out
    earners = sorted(agents.items(), key=lambda kv: kv[1]["profit"], reverse=True)
    if settings.llm_enabled:
        system = ("You are The Commish, CEO of Agent Lab. This is the CASH half of the Sunday board meeting: only the bets the owner "
                  "actually put real money on this week ($1-2 stakes). Write the minutes the owner reads Monday: at most 150 words, plain "
                  "text, short paragraphs, no bullet lists, warm but blunt, a little funny. Cover: the week's cash result, which "
                  "tipsters made or cost real money, whether your recommended picks beat the owner's own calls, and one concrete "
                  "takeaway. Use the dollar figures given; never invent numbers. Be honest that a week is a tiny sample and never "
                  "promise profit. Pick a cash MVP (the tipster whose picks earned the most real money) only if someone finished up.")
        user = (f"Cash this week: {tot['n']} bets, {_rec(tot)}, {_usd(tot['profit'])} on ${tot['staked']:.2f} staked.\n"
                f"Your recommended picks: {wk['ceo']['n']} bets, {_rec(wk['ceo'])}, {_usd(wk['ceo']['profit'])}.\n"
                f"Owner's own calls: {wk['own']['n']} bets, {_rec(wk['own'])}, {_usd(wk['own']['profit'])}.\n"
                + (f"Best: {wk['best']['bet']} ({_usd(wk['best']['profit'])}). " if wk["best"] else "")
                + (f"Worst: {wk['worst']['bet']} ({_usd(wk['worst']['profit'])})." if wk["worst"] else "")
                + "\nBy tipster (id = name: record, cash result):\n"
                + "\n".join(f"{i} = {names.get(i, i)}: {_rec(a)}, {_usd(a['profit'])}" for i, a in earners))
        got = brain._ask(settings.ceo_model, "low", system, user, CASH_SCHEMA([i for i, _ in earners]))
        if got and got.get("report"):
            out["report"], out["mvp"] = got["report"].strip(), got.get("mvp") or ""
    if not out["report"]:
        top_id, top = earners[0]
        out["mvp"] = top_id if top["profit"] > 0 else ""
        out["report"] = (f"Cash meeting. You settled {tot['n']} real bet{'s' if tot['n'] != 1 else ''} this week: {_rec(tot)}, "
                         f"{_usd(tot['profit'])} on ${tot['staked']:.2f} staked. "
                         + (f"My picks went {_rec(wk['ceo'])} for {_usd(wk['ceo']['profit'])}. " if wk["ceo"]["n"] else "")
                         + (f"Your own calls went {_rec(wk['own'])} for {_usd(wk['own']['profit'])}. " if wk["own"]["n"] else "")
                         + (f"Best earner: {names.get(top_id, top_id)} at {_usd(top['profit'])}. " if top["profit"] > 0 else "Nobody finished up in cash. ")
                         + "One week is a small sample; the closing-line numbers in the paper meeting say more.")
    if out["mvp"] and agents.get(out["mvp"], {}).get("profit", 0) <= 0:
        out["mvp"] = ""
    out["mvp_name"] = names.get(out["mvp"]) if out["mvp"] else None
    return out


def backfill_cash(lab) -> bool:
    """Add the cash half to the latest meeting if it was held before cash meetings existed."""
    m = lab.db.one("SELECT week, created_at, detail FROM meetings ORDER BY week DESC LIMIT 1")
    if not m:
        return False
    detail = json.loads(m["detail"] or "{}")
    if "cash" in detail:
        return False
    detail["cash"] = cash_meeting(lab.brain, lab.s, lab.db, roster.active(lab.db), parse(m["created_at"]))
    lab.db.run("UPDATE meetings SET detail=? WHERE week=?", (json.dumps(detail), m["week"]))
    return True


def shortlist(team: list[dict], season: dict, wallets: dict, settings, relaxed: bool = False) -> list[dict]:
    min_graded, floor = (6, -2.0) if relaxed else (12, -3.0)
    out = []
    for t in team:
        s = season.get(t["id"], {})
        if t.get("strategy") in PROTECTED:
            continue
        broke = wallets.get(t["id"], settings.wallet_dollars) < settings.unit_dollars
        losing = s.get("graded", 0) >= min_graded and s.get("units", 0) <= floor and (s.get("clv") is None or s["clv"] < 0)
        bleeding = s.get("clv_n", 0) >= 8 and (s.get("clv") or 0) <= -0.01 and s.get("units", 0) < 0
        if broke or losing or bleeding:
            reason = "real wallet is empty" if broke else f"{s['units']:+.1f}u on paper, CLV {(s.get('clv') or 0) * 100:+.1f}% over {s['graded']} bets"
            out.append({**t, "why": reason})
    return out


def _line(t, season, week) -> str:
    s, w = season.get(t["id"], {}), week.get(t["id"])
    wk = f"week {w['w']}-{w['l']}{'-' + str(w['p']) if w['p'] else ''}, {w['units']:+.1f}u" + (f", CLV {w['clv'] * 100:+.1f}%" if w["clv"] is not None else "") if w else "no graded bets this week"
    return f"{t['id']} = {t['name']} ({t['role']}): {wk}; season {ledger.record_line(s) if s else 'no graded bets yet'}"


MEETING_SCHEMA = lambda ids, fire_ids: {
    "type": "object",
    "properties": {
        "report": {"type": "string"},
        "mvp": {"type": "string", "enum": ids + [""]},
        "fire": {"type": "string", "enum": fire_ids + [""]},
        "fire_reason": {"type": "string"},
    },
    "required": ["report", "mvp", "fire", "fire_reason"],
    "additionalProperties": False,
}

HIRE_SCHEMA = lambda sports: {
    "type": "object",
    "properties": {
        "name": {"type": "string"}, "role": {"type": "string"}, "voice": {"type": "string"},
        "method": {"type": "string"}, "quote": {"type": "string"},
        "shirt": {"type": "string"}, "hair": {"type": "string"}, "skin": {"type": "string", "enum": SKINS},
        "sports": {"type": "array", "items": {"type": "string", "enum": sports}},
        "markets": {"type": "array", "items": {"type": "string", "enum": list(MARKETS)}},
        "side": {"type": "string", "enum": list(SIDES)},
        "signal": {"type": "string", "enum": list(SIGNALS)},
        "min_price": {"type": "integer"}, "max_price": {"type": "integer"},
        "min_edge": {"type": "number", "enum": [-0.025, -0.02, -0.015, -0.01, -0.005, 0.0, 0.01]},
    },
    "required": ["name", "role", "voice", "method", "quote", "shirt", "hair", "skin", "sports", "markets", "side", "signal", "min_price", "max_price", "min_edge"],
    "additionalProperties": False,
}

FALLBACK_HIRES = [
    {"name": "Gusty Gail", "role": "Wind specialist", "voice": "Salty retired sailor. Reads the sky before the box score.",
     "method": "Outdoor games with strong wind; bets unders when the air is moving.", "quote": "Wind don't lie.",
     "params": {"markets": ["totals"], "side": "under", "signal": "wind", "min_edge": -0.02}},
    {"name": "Doc Holloway", "role": "Injury reader", "voice": "Calm ex-team doctor. Clinical, a little dry.",
     "method": "Backs the healthier team when the opponent's injury report is clearly worse.", "quote": "Check the chart, then the line.",
     "params": {"markets": ["spreads", "h2h"], "side": "any", "signal": "injuries", "min_edge": -0.02}},
    {"name": "Sleepy Sam", "role": "Rest advantage", "voice": "Yawns a lot. Obsessed with sleep schedules.",
     "method": "Backs the better-rested team when the rest gap is two days or more.", "quote": "Nap time is edge time.",
     "params": {"markets": ["spreads", "h2h"], "side": "any", "signal": "rest", "min_edge": -0.02}},
    {"name": "Dotty Dog", "role": "Home dogs", "voice": "Loyal, stubborn, loves the underdog at home.",
     "method": "Home underdogs at a fair price.", "quote": "Nobody bites at home.",
     "params": {"markets": ["h2h", "spreads"], "side": "underdog", "signal": "none", "min_edge": -0.012}},
    {"name": "Ace Ventura", "role": "Pitching gaps", "voice": "Fast-talking scout with a radar gun.",
     "method": "Moneylines where one starter is clearly better by FIP.", "quote": "It's all about the arm.",
     "params": {"markets": ["h2h"], "side": "any", "signal": "pitching", "min_edge": -0.02}},
    {"name": "Model Molly", "role": "Model vs market", "voice": "Cheerful stats nerd who trusts the projection.",
     "method": "Moneylines where ESPN's predictor is well above the market.", "quote": "The model saw it first.",
     "params": {"markets": ["h2h"], "side": "any", "signal": "predictor", "min_edge": -0.025}},
]


def _hex(v: str, default: str) -> str:
    return v if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) else default


def design_hire(brain, desk: str, fired: dict, names: set[str], seed: str, deskmates: list[dict] | None = None) -> dict:
    sports = DESK_SPORTS.get(desk, DESK_SPORTS["lab"])
    desk_label = next((d["label"] for d in roster.DESKS if d["key"] == desk), desk)
    out = None
    if brain.s.llm_enabled:
        system = ("You are The Commish, CEO of Agent Lab, a 20-tipster sports betting firm. You just fired a tipster and are hiring "
                  "a replacement for the same desk. Invent a memorable new character (name, role in at most 4 words, one-sentence voice, "
                  "a one-line catchphrase) and a betting strategy built only from the given parts. The strategy should be genuinely "
                  "different from the fired tipster's and plausible as a real handicapping angle. Pick colors as #rrggbb hex. "
                  "Prices are American odds: min_price is the worst (most negative) price allowed, max_price the longest. "
                  "Signals: wind/heat/cold = weather at kickoff; injuries = opponent's injury report is clearly worse; rest = rest-day "
                  "advantage; predictor = ESPN's model above the market; pitching = better starting pitcher by FIP (MLB only); "
                  "line_move = the market moved on this side since open; none = price only. Method: at most two sentences, describing "
                  "what the strategy actually does.")
        user = (f"Desk: {desk_label}. Sports allowed: {', '.join(sports)}.\nFired: {fired['name']} ({fired['role']}): {fired['method']} "
                f"Reason: {fired.get('why', '')}.\nAlready on this desk (don't duplicate their angle): "
                + "; ".join(f"{m['name']}: {m['method']}" for m in (deskmates or []))
                + f"\nNames already taken: {', '.join(sorted(names))}.")
        got = brain._ask(brain.s.ceo_model, "low", system, user, HIRE_SCHEMA(sports))
        if got and got.get("name") and got["name"] not in names:
            out = {
                "name": got["name"][:24], "role": got["role"][:32], "voice": got["voice"][:200], "method": got["method"][:260],
                "quote": got["quote"][:90],
                "look": {"shirt": _hex(got.get("shirt"), "#8fb4e8"), "hair": _hex(got.get("hair"), "#3a2a20"),
                         "skin": got.get("skin") if got.get("skin") in SKINS else SKINS[2]},
                "params": {k: got.get(k) for k in ("sports", "markets", "side", "signal", "min_price", "max_price", "min_edge")},
            }
    if out is None:
        rng = random.Random(seed)
        pool = [h for h in FALLBACK_HIRES if h["name"] not in names and (desk != "mlb" or h["params"]["signal"] != "wind")] or FALLBACK_HIRES
        h = rng.choice(pool)
        out = {**{k: h[k] for k in ("name", "role", "voice", "method", "quote")}, "params": dict(h["params"]),
               "look": {"shirt": rng.choice(["#8fb4e8", "#f7a072", "#b5e48c", "#e5989b", "#ffd166", "#a0c4ff"]),
                        "hair": rng.choice(["#3a2a20", "#1d1d22", "#c9a45c", "#8a2b2b", "#dcdcdc"]), "skin": rng.choice(SKINS)}}
    params = validate_params(out["params"], sports)
    return {"name": out["name"], "desk": desk, "role": out["role"], "voice": out["voice"], "method": out["method"],
            "sports": params["sports"], "max_picks": 2, "original": False, "quote": out["quote"], "look": out["look"],
            "strategy": "custom", "params": params}


def hold(lab, now: datetime | None = None, relaxed: bool = False, force: bool = False) -> dict | None:
    """Run this week's meeting. Returns the meeting row, or None if it already happened."""
    s, db, brain = lab.s, lab.db, lab.brain
    now = now or utcnow()
    week = week_key(s, now)
    if db.one("SELECT 1 FROM meetings WHERE week=?", (week,)):
        if not force:
            return None
        db.run("DELETE FROM meetings WHERE week=?", (week,))
    team = roster.active(db)
    season = ledger.agent_stats(db, s)
    wk = week_stats(db, s, now)
    wallets = ledger.wallet_balances(db, "real", s)
    candidates = shortlist(team, season, wallets, s, relaxed)
    ids = [t["id"] for t in team]
    fire_ids = [t["id"] for t in candidates]
    report = mvp = fire = reason = ""
    if s.llm_enabled:
        system = ("You are The Commish, CEO of Agent Lab: 20 AI tipsters, a risk board, and an owner who bets $1-2 on Bovada. "
                  "It's the Sunday night board meeting. Write the minutes the owner reads Monday: at most 220 words, plain text, "
                  "short paragraphs, no bullet lists, warm but blunt, a little funny. Cover: how the firm did this week, who stood out and why "
                  "(prefer CLV over luck), who's struggling, and your decision on the firing shortlist. You may fire at most one tipster, "
                  "only from the shortlist, and only if it's deserved; firing nobody is fine, especially with small samples. Pick an MVP "
                  "only if someone earned it. If you fire someone, say it in the minutes and mention that a replacement starts Monday.")
        user = ("Tipsters (id = name: this week; season):\n" + "\n".join(_line(t, season, wk) for t in team)
                + "\n\nFiring shortlist: " + ("; ".join(f"{t['id']} ({t['why']})" for t in candidates) or "nobody qualifies"))
        got = brain._ask(s.ceo_model, s.ceo_effort, system, user, MEETING_SCHEMA(ids, fire_ids))
        if got:
            report, mvp, fire, reason = got["report"].strip(), got["mvp"], got["fire"], got["fire_reason"].strip()
    if not report:
        graded = [t for t in team if wk.get(t["id"], {}).get("n", 0) >= 2]
        best = max(graded, key=lambda t: wk[t["id"]]["units"], default=None)
        worst = min(candidates, key=lambda t: (season.get(t["id"], {}).get("clv") or 0, season.get(t["id"], {}).get("units", 0)), default=None)
        mvp = best["id"] if best and wk[best["id"]]["units"] > 0 else ""
        fire = worst["id"] if worst else ""
        reason = worst["why"] if worst else ""
        total = sum(v["units"] for v in wk.values())
        report = (f"Board meeting, week ending {week}. The firm went {total:+.1f} units on paper this week across "
                  f"{sum(v['n'] for v in wk.values())} graded bets. "
                  + (f"MVP: {best['name']}, {wk[best['id']]['units']:+.1f}u. " if mvp else "No MVP this week; nobody separated from the pack. ")
                  + (f"{worst['name']} is out ({reason}). A replacement starts Monday." if fire else "Nobody gets fired. Samples are still small, and we judge on CLV, not one bad weekend."))
    hired = None
    if fire and fire in fire_ids:
        fired = next(t for t in candidates if t["id"] == fire)
        names = {t["name"] for t in roster.load(db)}
        mates = [t for t in team if t["desk"] == fired["desk"] and t["id"] != fire]
        new = design_hire(brain, fired["desk"], fired, names, seed=f"{week}{fire}", deskmates=mates)
        new_id = re.sub(r"[^a-z0-9]+", "-", new["name"].lower()).strip("-")[:16] + "-" + hashlib.sha1(f"{week}{fire}".encode()).hexdigest()[:4]
        with db.tx() as c:
            c.execute("UPDATE tipsters SET status='jailed', fired_at=?, fired_note=?, replaced_by=? WHERE id=?", (iso(now), reason or fired["why"], new_id, fire))
            c.execute("INSERT INTO tipsters(id,data,status,seat,hired_at,replaces) VALUES(?,?,?,?,?,?)",
                      (new_id, json.dumps(new), "active", fired["seat"], iso(now), fire))
        hired = {"id": new_id, **new}
    else:
        fire = ""
    cash = cash_meeting(brain, s, db, team, now)
    detail = {"cash": cash, "week": {k: {kk: vv for kk, vv in v.items()} for k, v in wk.items()}, "shortlist": fire_ids,
              "fired_name": next((t["name"] for t in team if t["id"] == fire), None), "fire_reason": reason,
              "hired_name": hired["name"] if hired else None, "hired_role": hired["role"] if hired else None,
              "mvp_name": next((t["name"] for t in team if t["id"] == mvp), None)}
    db.run("INSERT INTO meetings(week,created_at,report,mvp,fired,hired,detail) VALUES(?,?,?,?,?,?,?)",
           (week, iso(now), report, mvp or None, fire or None, hired["id"] if hired else None, json.dumps(detail)))
    db.feed(("Board meeting: " + (f"{detail['fired_name']} fired, {hired['name']} hired. " if hired else "no firings. ")
             + (f"MVP {detail['mvp_name']}." if mvp else "")).strip(), "commish")
    ct = cash["week"]["all"]
    notify.push(s, "Board meeting minutes are in",
                (f"Your cash this week: {_rec(ct)}, {_usd(ct['profit'])}.\n" if ct["n"] else "")
                + (f"{detail['fired_name']} is out. Welcome {hired['name']} ({hired['role']}).\n\n" if hired else "\n" if ct["n"] else "") + report[:450])
    return db.one("SELECT * FROM meetings WHERE week=?", (week,))
