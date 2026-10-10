"""Everything the web UI needs in one JSON document."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta

from . import ledger
from . import oddsmath as om
from .agents import describe
from .db import DB, iso, local_day, parse, utcnow
from .board import pick_kind
from .explain import needs
from .research import briefing
from . import roster
from .sources.odds import book_name, short, daily_cap

LEAGUE = {"americanfootball_nfl": "NFL", "americanfootball_ncaaf": "College football", "baseball_mlb": "MLB",
          "basketball_nba": "NBA", "soccer_epl": "Premier League", "soccer_usa_mls": "MLS", "soccer_uefa_champs_league": "Champions League"}


def pick_view(p: dict, ctx: dict, members: dict, real: dict, settings, names: dict, checks: dict) -> dict:
    notes = json.loads(p["board_notes"] or "[]") if isinstance(p["board_notes"], str) else p["board_notes"]
    lead = p["group_id"] or p["id"]
    rb = real.get(p["id"])
    return {
        "id": p["id"], "agent": p["agent"], "created_at": p["created_at"], "agent_name": names.get(p["agent"], {}).get("name", p["agent"]),
        "sport": short(p["sport"]), "league": LEAGUE.get(p["sport"], short(p["sport"])),
        "game": f"{p['away']} @ {p['home']}", "commence": p["commence"], "bet": describe(p).rsplit(" ", 1)[0],
        "needs": needs(p["market"], p["selection"], p["point"], p["sport"], p.get("player")),
        "market": p["market"], "price": p["price"], "price_txt": om.fmt(p["price"]),
        "fair_txt": om.fmt(om.american_from_prob(p["fair_prob"])) if p["fair_prob"] else "—",
        "edge": p["edge"], "min_price": p["min_price"], "min_txt": om.fmt(p["min_price"]),
        "stake_units": p["stake_units"], "stake_dollars": round(p["stake_units"] * settings.unit_dollars, 2),
        "confidence": p["confidence"], "reasoning": p["reasoning"], "signal": p["signal"],
        "board": p["board_status"], "notes": notes, "estimated": bool(p["estimated"]), "books": p["books"],
        "group": [names.get(a, {}).get("name", a) for a in members.get(lead, []) if a != p["agent"]],
        "merged": bool(p["group_id"]), "real_pick": bool(p["real_pick"]), "ceo_rank": p["ceo_rank"],
        "paper_only": bool(p["paper_only"]), "late": bool(p["late"]), "decision": p["decision"],
        "result": p["result"], "clv": p["clv"], "actual": p["actual"], "kind": pick_kind(p, settings),
        "research": briefing(ctx) if ctx else "", "local_day": p["local_day"],
        "real_bet": rb, "check": checks.get(p["id"]),
        "book": p.get("book") or "bovada", "book_name": book_name(p.get("book") or "bovada"),
        "links": json.loads(p["links"]) if p.get("links") else {},
        "prices": [{"book": k, "name": book_name(k), "price": v, "txt": om.fmt(v)}
                   for k, v in sorted((json.loads(p["prices"]) if p.get("prices") else {}).items(), key=lambda kv: -om.dec(kv[1]))],
    }


def build_state(settings, db: DB) -> dict:
    now = utcnow()
    day = local_day(settings.timezone, now)
    stats = ledger.agent_stats(db, settings)
    everyone = roster.load(db)
    names = {t["id"]: t for t in everyone}
    paper_w = ledger.wallet_balances(db, "paper", settings)
    real_w = ledger.wallet_balances(db, "real", settings)

    since = iso(now - timedelta(days=3))
    picks = db.all("SELECT * FROM picks WHERE local_day=? OR (result IS NULL AND commence>?) ORDER BY ceo_rank IS NULL, ceo_rank, edge DESC",
                   (day, since))
    ev_ids = list({p["event_id"] for p in picks})
    ctx = {}
    if ev_ids:
        for r in db.all("SELECT id, context FROM events WHERE id IN (%s)" % ",".join("?" * len(ev_ids)), ev_ids):
            ctx[r["id"]] = json.loads(r["context"] or "{}")
    members = defaultdict(list)
    for p in picks:
        members[p["group_id"] or p["id"]].append(p["agent"])
    real = {}
    for b in db.all("SELECT pick_id, SUM(stake_cents) s, MAX(price) price, SUM(COALESCE(profit_cents,0)) pr, MAX(result) r, MAX(book) book FROM bets WHERE kind='real' GROUP BY pick_id"):
        real[b["pick_id"]] = {"stake": b["s"] / 100, "price": b["price"], "profit": b["pr"] / 100, "result": b["r"], "book": book_name(b["book"] or "bovada")}
    # each merged tipster holds a share; show the whole bet on every card in the group
    by_lead = defaultdict(list)
    for p in picks:
        by_lead[p["group_id"] or p["id"]].append(p["id"])
    for ids in by_lead.values():
        total = round(sum(real[i]["stake"] for i in ids if i in real), 2)
        for i in ids:
            if i in real:
                real[i] = {**real[i], "total": total}
    checks = {}
    if picks:
        keys = [f"recheck:{p['id']}" for p in picks]
        for r in db.all("SELECT key, value FROM kv WHERE key IN (%s)" % ",".join("?" * len(keys)), keys):
            checks[r["key"].split(":", 1)[1]] = json.loads(r["value"])
    views = [pick_view(p, ctx.get(p["event_id"]), members, real, settings, names, checks) for p in picks]

    # firm totals
    def totals(kind):
        r = db.one("SELECT COUNT(*) n, SUM(result='win') w, SUM(result='loss') l, SUM(result IN ('push','void')) p, "
                   "COALESCE(SUM(CASE WHEN result IS NOT NULL THEN profit_cents END),0) pr, COALESCE(SUM(CASE WHEN result IS NOT NULL THEN stake_cents END),0) st, "
                   "COALESCE(SUM(CASE WHEN result IS NULL THEN stake_cents END),0) op FROM bets WHERE kind=?", (kind,))
        return {"bets": r["n"], "w": r["w"] or 0, "l": r["l"] or 0, "p": r["p"] or 0, "profit": r["pr"] / 100,
                "staked": r["st"] / 100, "open": r["op"] / 100, "roi": (r["pr"] / r["st"]) if r["st"] else None}

    clv = db.one("SELECT AVG(line_move) c, COUNT(line_move) n FROM picks WHERE line_move IS NOT NULL")
    seeded = settings.wallet_dollars * len(everyone)  # every hire comes with a fresh wallet

    def equity(kind):
        # running profit after each settled bet, in game-time order (merged tipster shares count as one bet)
        rows = db.all("SELECT MIN(p.commence) t, SUM(b.profit_cents) pr, MIN(b.pick_id) pid, MAX(b.result) res FROM bets b JOIN picks p ON p.id=b.pick_id "
                      "WHERE b.kind=? AND b.result IS NOT NULL GROUP BY b.placed_at, COALESCE(p.group_id, p.id) ORDER BY t, MIN(b.id)", (kind,))
        run, out = 0, []
        for r in rows:
            run += r["pr"] or 0
            point = {"t": r["t"], "v": round(run / 100, 2)}
            if kind == "real":  # what the bet was, for the chart's hover readout
                p = db.one("SELECT * FROM picks WHERE id=?", (r["pid"],))
                point.update(bet=describe(p).rsplit(" ", 1)[0] if p else "", price=om.fmt(p["price"]) if p else "",
                             d=round((r["pr"] or 0) / 100, 2), res=r["res"])
            out.append(point)
        return out

    breakdown = splits(db, settings)
    history = tipster_history(db, settings, [t["id"] for t in everyone])
    agents = []
    for a in everyone:
        s = stats[a["id"]]
        today = [v for v in views if v["agent"] == a["id"] and v["local_day"] == day]
        agents.append({
            "id": a["id"], "paper": {k: s[k] for k in ("w", "l", "p", "graded", "units", "roi", "clv", "open", "last10", "picks_total")},
            "paper_wallet": round(paper_w[a["id"]], 2), "real_wallet": round(real_w[a["id"]], 2),
            "real": {"profit": s["real_profit_cents"] / 100, "w": s["real_w"], "l": s["real_l"], "p": s["real_p"], "open": s["real_open"],
                     "staked": s["real_staked_cents"] / 100, "roi": (s["real_profit_cents"] / s["real_staked_cents"]) if s["real_staked_cents"] else None,
                     "last10": s["real_last10"], "clv": s["real_clv"]},
            "today": [{"id": v["id"], "bet": v["bet"], "price": v["price_txt"], "board": v["board"]} for v in today],
            "status": "JAILED" if a["status"] == "jailed" else status_of(a, s, real_w[a["id"]], settings),
            "splits": breakdown.get(a["id"], []), "recent": history.get(a["id"], []),
        })

    memo = db.one("SELECT text, created_at FROM memos WHERE local_day=?", (day,))
    runs = db.all("SELECT id, kind, started_at, finished_at, status, detail FROM runs ORDER BY id DESC LIMIT 8")
    for r in runs:
        d = json.loads(r.pop("detail") or "{}")
        d.pop("trace", None)
        r["detail"] = d
    spend = {"today": 0.0, "week": 0.0}
    week_ago = iso(now - timedelta(days=7))
    for r in db.all("SELECT local_day, started_at, detail FROM runs WHERE started_at>?", (week_ago,)):
        c = (json.loads(r["detail"] or "{}").get("llm") or {}).get("cost", 0) or 0
        spend["week"] += c
        if r["local_day"] == day:
            spend["today"] += c
    credit = db.one("SELECT remaining, used FROM credits WHERE remaining IS NOT NULL ORDER BY id DESC LIMIT 1") or {}
    today_credits = db.one("SELECT COALESCE(SUM(cost),0) c FROM credits WHERE local_day=?", (day,))["c"]
    real_bets = db.all("SELECT b.pick_id, b.agent, b.stake_cents, b.price, b.result, b.profit_cents, b.placed_at, b.book, p.event_id, p.home, p.away, p.market, p.selection, p.point, p.player, p.commence "
                       "FROM bets b JOIN picks p ON p.id=b.pick_id WHERE b.kind='real' ORDER BY b.placed_at DESC LIMIT 60")
    grouped = {}
    for b in real_bets:
        g = grouped.setdefault((b["placed_at"], b["price"], b["event_id"], b["market"], b["selection"], b["point"]), {
            "pick_id": b["pick_id"], "bet": describe({**b}).rsplit(" ", 1)[0], "price": om.fmt(b["price"]), "game": f"{b['away']} @ {b['home']}",
            "commence": b["commence"], "placed_at": b["placed_at"], "result": b["result"], "stake": 0, "profit": 0, "agents": [],
            "book": book_name(b["book"] or "bovada")})
        g["stake"] += b["stake_cents"] / 100
        g["profit"] += (b["profit_cents"] or 0) / 100
        g["agents"].append(names.get(b["agent"], {}).get("name", b["agent"]))
    cooler = db.all("SELECT day, kind, created_at, lines FROM watercooler ORDER BY id DESC LIMIT 6")
    for c in cooler:
        c["lines"] = json.loads(c["lines"])
    meetings = db.all("SELECT week, created_at, report, mvp, fired, hired, detail FROM meetings ORDER BY week DESC LIMIT 8")
    for m in meetings:
        m["detail"] = json.loads(m["detail"] or "{}")
        m["detail"].pop("week", None)
    stale = db.all("SELECT id FROM picks WHERE result IS NULL AND commence<?", (iso(now - timedelta(hours=8)),))

    return {
        "now": now.isoformat(), "day": day, "roster": roster.public_roster(db), "agents": agents, "meetings": meetings, "cooler": cooler, "picks": views,
        "memo": memo, "runs": runs, "feed": db.all("SELECT at, agent, text FROM feed ORDER BY id DESC LIMIT 30"),
        "paper": {**totals("paper"), "seeded": seeded, "balance": round(sum(paper_w.values()), 2), "clv": clv["c"], "clv_n": clv["n"], "equity": equity("paper")},
        "real": {**totals("real"), "seeded": seeded, "balance": round(sum(real_w.values()), 2), "equity": equity("real"), "bets": list(grouped.values())},
        "mine": my_bets(db, settings, names, day),
        "credits": {"today": today_credits, "cap": daily_cap(settings, db), "remaining": credit.get("remaining"), "used": credit.get("used")},
        "claude": {"today": round(spend["today"], 2), "week": round(spend["week"], 2), "per_day": round(spend["week"] / 7, 2),
                   "tipster_model": settings.tipster_model, "ceo_model": settings.ceo_model},
        "needs_grading": [s["id"] for s in stale],
        "config": {"unit": settings.unit_dollars, "wallet": settings.wallet_dollars, "max_real": settings.max_real_per_day,
                   "llm": settings.llm_enabled, "odds": bool(settings.odds_api_key), "alerts": bool(settings.ntfy_topic),
                   "slate_hour": settings.slate_hour, "rescan_hours": settings.rescan_hours, "tz": settings.timezone,
                   "sports": [short(x) for x in settings.sports], "meeting_hour": settings.meeting_hour,
                   "books": [{"key": k, "name": book_name(k)} for k in settings.my_books]},
    }


MARKET_LABEL = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total", "parlay": "Parlay"}


def splits(db: DB, settings) -> dict[str, list[dict]]:
    """Each tipster's graded record by sport and bet type."""
    rows = db.all("SELECT p.agent, p.sport, p.market, p.player, p.result, p.line_move AS clv, COALESCE(SUM(b.profit_cents),0) pr FROM picks p "
                  "LEFT JOIN bets b ON b.pick_id=p.id AND b.kind='paper' WHERE p.result IS NOT NULL GROUP BY p.id")
    acc: dict[tuple, dict] = {}
    for r in rows:
        label = "Player prop" if r["player"] else MARKET_LABEL.get(r["market"], r["market"])
        k = (r["agent"], short(r["sport"]) if r["market"] != "parlay" else "Mixed", label)
        a = acc.setdefault(k, {"sport": k[1], "market": k[2], "w": 0, "l": 0, "p": 0, "units": 0.0, "clv_sum": 0.0, "clv_n": 0})
        a[{"win": "w", "loss": "l"}.get(r["result"], "p")] += 1
        a["units"] += r["pr"] / (settings.unit_dollars * 100)
        if r["clv"] is not None:
            a["clv_sum"] += r["clv"]
            a["clv_n"] += 1
    out: dict[str, list[dict]] = defaultdict(list)
    for (agent, _, _), a in acc.items():
        out[agent].append({"sport": a["sport"], "market": a["market"], "w": a["w"], "l": a["l"], "p": a["p"],
                           "units": round(a["units"], 2), "clv": a["clv_sum"] / a["clv_n"] if a["clv_n"] else None})
    for v in out.values():
        v.sort(key=lambda x: -(x["w"] + x["l"] + x["p"]))
    return out


def final_txt(home: str, away: str, home_score, away_score) -> str | None:
    if home_score is None or away_score is None:
        return None
    h, a = f"{home_score:g}", f"{away_score:g}"
    if home_score == away_score:
        return f"{away} {a}, {home} {h} (tie)"
    return f"{home} {h}, {away} {a}" if home_score > away_score else f"{away} {a}, {home} {h}"


def tipster_history(db: DB, settings, agent_ids: list[str], limit: int = 10) -> dict[str, list[dict]]:
    """Each tipster's recent picks (open first, then most recently graded), with final scores and
    whether you put real money on them (merged picks count the whole bet)."""
    from .explain import needs as _needs
    lead_of = {}
    yours: dict[str, dict] = {}
    for r in db.all("SELECT b.pick_id, p.group_id, b.stake_cents, b.profit_cents, b.result FROM bets b JOIN picks p ON p.id=b.pick_id WHERE b.kind='real'"):
        lead = r["group_id"] or r["pick_id"]
        lead_of[r["pick_id"]] = lead
        y = yours.setdefault(lead, {"stake": 0.0, "profit": 0.0, "result": r["result"]})
        y["stake"] += r["stake_cents"] / 100
        y["profit"] += (r["profit_cents"] or 0) / 100
    out = {}
    for aid in agent_ids:
        rows = db.all("SELECT p.*, e.home_score, e.away_score, e.status AS ev_status FROM picks p LEFT JOIN events e ON e.id=p.event_id "
                      "WHERE p.agent=? ORDER BY (p.result IS NULL) DESC, CASE WHEN p.result IS NULL THEN p.commence END, p.graded_at DESC LIMIT ?",
                      (aid, limit))
        items = []
        for p in rows:
            lead = p["group_id"] or p["id"]
            y = yours.get(lead) or yours.get(lead_of.get(p["id"], ""))
            items.append({
                "id": p["id"], "bet": describe(p).rsplit(" ", 1)[0], "price_txt": om.fmt(p["price"]), "game": f"{p['away']} @ {p['home']}",
                "commence": p["commence"], "result": p["result"], "real_pick": bool(p["real_pick"]), "board": p["board_status"],
                "final": final_txt(p["home"], p["away"], p["home_score"], p["away_score"]) if p["market"] != "parlay" else None,
                "needs": _needs(p["market"], p["selection"], p["point"], p["sport"], p.get("player")) if not p["result"] else None,
                "you": {"stake": round(y["stake"], 2), "profit": round(y["profit"], 2), "result": y["result"]} if y else None,
            })
        out[aid] = items
    return out


def price_move(db: DB, key: tuple, price: int, placed_at: str, close: tuple, status: str) -> dict | None:
    """How the market has moved since you placed a bet, from the prices saved at every scan (no extra credits).
    A price that got worse after you bet means the market moved toward your side."""
    event_id, market, selection, point = key
    same = db.one("SELECT bov_price, fair_prob, seen_at FROM lines WHERE event_id=? AND market=? AND selection=? AND point IS ? "
                  "ORDER BY seen_at DESC, id DESC LIMIT 1", (event_id, market, selection, point))
    latest = db.one("SELECT point, bov_price, fair_prob, seen_at FROM lines WHERE event_id=? AND market=? AND selection=? "
                    "ORDER BY seen_at DESC, id DESC LIMIT 1", (event_id, market, selection))
    out = {"then_txt": om.fmt(price), "point": point}
    if status != "open" and close[0]:
        out.update(kind="closed", now_txt=om.fmt(close[0]), fair_txt=om.fmt(om.american_from_prob(close[1])) if close[1] else None,
                   value=om.edge(close[1], price) if close[1] else None)
        return out
    if not latest or latest["seen_at"] <= placed_at:
        out.update(kind="none")
        return out
    out["as_of"] = latest["seen_at"]
    if point is not None and latest["point"] is not None and latest["point"] != point and (not same or same["seen_at"] < latest["seen_at"]):
        # the number itself moved: a bigger number is better for the bettor (+7 beats +6.5, -3 beats -3.5; totals flip for unders)
        better_for_me = point > latest["point"] if not (market == "totals" and selection == "Over") else point < latest["point"]
        out.update(kind="line", line_now=latest["point"], now_txt=om.fmt(latest["bov_price"]) if latest["bov_price"] else None,
                   direction="for" if better_for_me else "against")
        return out
    row = same or latest
    shift = om.implied(row["bov_price"]) - om.implied(price) if row["bov_price"] else 0.0
    out.update(kind="price", now_txt=om.fmt(row["bov_price"]) if row["bov_price"] else None,
               fair_txt=om.fmt(om.american_from_prob(row["fair_prob"])) if row["fair_prob"] else None,
               value=om.edge(row["fair_prob"], price) if row["fair_prob"] else None,
               direction="for" if shift > 0.004 else "against" if shift < -0.004 else "flat")
    return out


def my_bets(db: DB, settings, names: dict, day: str) -> dict:
    """Every real bet you placed (merged tipsters' shares combined), plus totals split by
    CEO-recommended vs. your own calls."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(settings.timezone)
    rows = db.all("SELECT b.pick_id, b.agent, b.stake_cents, b.price, b.result, b.profit_cents, b.placed_at, b.settled_at, b.book, "
                  "p.home, p.away, p.market, p.selection, p.point, p.player, p.commence, p.sport, p.real_pick, p.ceo_rank, p.clv, "
                  "p.board_status, p.local_day, p.event_id, p.legs, p.close_price, p.close_fair FROM bets b JOIN picks p ON p.id=b.pick_id WHERE b.kind='real' ORDER BY b.placed_at DESC, b.id")
    groups: dict[tuple, dict] = {}
    for r in rows:
        k = (r["placed_at"], r["price"], r["event_id"], r["market"], r["selection"], r["point"])
        g = groups.get(k)
        if g is None:
            g = groups[k] = {
                "id": r["pick_id"], "bet": describe({**r}).rsplit(" ", 1)[0], "price": r["price"], "price_txt": om.fmt(r["price"]),
                "book": book_name(r["book"] or "bovada"), "game": f"{r['away']} @ {r['home']}", "sport": short(r["sport"]) if r["market"] != "parlay" else "Parlay",
                "commence": r["commence"], "placed_at": r["placed_at"], "result": r["result"], "stake": 0.0, "profit": 0.0,
                "agents": [], "recommended": False, "ceo_rank": None, "clv": r["clv"],
                "placed_day": parse(r["placed_at"]).astimezone(tz).date().isoformat(),
                "needs": needs(r["market"], r["selection"], r["point"], r["sport"], r.get("player")),
                "games": [{"game": f"{r['away']} @ {r['home']}", "commence": r["commence"], "sport": short(r["sport"]), "leg": None, "event_id": r["event_id"], "pick_id": r["pick_id"]}],
                "_legs": json.loads(r["legs"]) if r["market"] == "parlay" and r["legs"] else None,
                "_key": (r["event_id"], r["market"], r["selection"], r["point"]), "_close": (r["close_price"], r["close_fair"]),
            }
        g["stake"] += r["stake_cents"] / 100
        g["profit"] += (r["profit_cents"] or 0) / 100
        name = names.get(r["agent"], {}).get("name", r["agent"])
        if name not in g["agents"]:
            g["agents"].append(name)
        if r["real_pick"]:
            g["recommended"] = True
            g["ceo_rank"] = g["ceo_rank"] or r["ceo_rank"]
    bets = list(groups.values())
    for b in bets:
        leg_ids = b.pop("_legs", None)
        if leg_ids:
            legs = db.all("SELECT id, event_id, home, away, commence, sport, market, selection, point, player, price, result FROM picks WHERE id IN (%s) ORDER BY commence"
                          % ",".join("?" * len(leg_ids)), leg_ids)
            if legs:
                b["games"] = [{"game": f"{l['away']} @ {l['home']}", "commence": l["commence"], "sport": short(l["sport"]),
                               "leg": describe(l).rsplit(" ", 1)[0], "leg_result": l["result"], "event_id": l["event_id"], "pick_id": l["id"],
                               "needs": needs(l["market"], l["selection"], l["point"], l["sport"], l.get("player"))} for l in legs]
        b["stake"], b["profit"] = round(b["stake"], 2), round(b["profit"], 2)
        b["to_win"] = round(b["stake"] * (om.dec(b["price"]) - 1), 2)
        b["status"] = "open" if not b["result"] else {"win": "won", "loss": "lost"}.get(b["result"], "push")

    for b in bets:
        key, close = b.pop("_key"), b.pop("_close")
        b["move"] = None if key[1] == "parlay" else price_move(db, key, b["price"], b["placed_at"], close, b["status"])

    ev_ids = list({g["event_id"] for b in bets for g in b["games"]})
    watch = {}
    if ev_ids:
        for r in db.all("SELECT id, home, away, context, status, home_score, away_score FROM events WHERE id IN (%s)" % ",".join("?" * len(ev_ids)), ev_ids):
            cx = json.loads(r["context"] or "{}")
            watch[r["id"]] = {"tv": cx.get("broadcast"), "link": cx.get("espn_link"), "venue": cx.get("venue"),
                              "final": final_txt(r["home"], r["away"], r["home_score"], r["away_score"]) if r["status"] == "final" else None}
    for b in bets:
        for g in b["games"]:
            g.update(watch.get(g["event_id"], {}))

    def tally(items):
        done = [b for b in items if b["status"] != "open"]
        staked = sum(b["stake"] for b in done)
        profit = sum(b["profit"] for b in done)
        return {"n": len(items), "open": sum(b["status"] == "open" for b in items), "settled": len(done),
                "w": sum(b["status"] == "won" for b in done), "l": sum(b["status"] == "lost" for b in done),
                "p": sum(b["status"] == "push" for b in done), "staked": round(staked, 2), "profit": round(profit, 2),
                "roi": profit / staked if staked else None,
                "open_stake": round(sum(b["stake"] for b in items if b["status"] == "open"), 2),
                "open_to_win": round(sum(b["to_win"] for b in items if b["status"] == "open"), 2)}

    return {"bets": bets, "all": tally(bets), "ceo": tally([b for b in bets if b["recommended"]]),
            "mine": tally([b for b in bets if not b["recommended"]]),
            "today_placed": sum(b["placed_day"] == day for b in bets)}


def status_of(agent: dict, s: dict, real_wallet: float, settings) -> str:
    if agent.get("strategy") == "coin":
        return "BENCHMARK"
    if real_wallet < settings.unit_dollars:
        return "BROKE"
    if s["graded"] >= 15 and s["units"] <= -3 and (s["clv"] or 0) < 0:
        return "ON NOTICE"
    if s["graded"] < 5:
        return "ROOKIE"
    if (s["clv"] or 0) >= 0.015:
        return "SHARP"
    if s["units"] >= 2:
        return "HOT"
    return "STEADY"
