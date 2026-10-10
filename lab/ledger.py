"""Paper and real money. Every agent has a paper wallet and a real wallet; amounts are integer cents."""

from __future__ import annotations

import json

from . import oddsmath as om
from .db import DB, iso
from . import roster


def wallet_balances(db: DB, kind: str, settings) -> dict[str, float]:
    rows = db.all(
        "SELECT agent, COALESCE(SUM(CASE WHEN result IS NULL THEN stake_cents ELSE 0 END),0) AS open,"
        " COALESCE(SUM(CASE WHEN result IS NOT NULL THEN profit_cents ELSE 0 END),0) AS pnl FROM bets WHERE kind=? AND personal=0 GROUP BY agent",
        (kind,),
    )
    start = round(settings.wallet_dollars * 100)
    out = {a["id"]: settings.wallet_dollars for a in roster.load(db)}
    for r in rows:
        out[r["agent"]] = (start + r["pnl"] - r["open"]) / 100
    return out


def agent_stats(db: DB, settings) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for a in roster.load(db):
        stats[a["id"]] = {"w": 0, "l": 0, "p": 0, "graded": 0, "profit_cents": 0, "staked_cents": 0, "units": 0.0,
                          "open": 0, "clv": None, "clv_n": 0, "last10": [], "real_profit_cents": 0, "real_w": 0, "real_l": 0,
                          "real_open": 0, "picks_total": 0, "real_p": 0, "real_staked_cents": 0, "real_last10": [], "real_clv": None, "real_clv_n": 0}
    for r in db.all("SELECT agent, kind, stake_cents, result, profit_cents FROM bets WHERE personal=0 ORDER BY placed_at"):
        s = stats.get(r["agent"])
        if not s:
            continue
        if r["kind"] == "paper":
            if r["result"] is None:
                s["open"] += 1
                continue
            s["graded"] += 1
            s["staked_cents"] += r["stake_cents"]
            s["profit_cents"] += r["profit_cents"]
            s[{"win": "w", "loss": "l"}.get(r["result"], "p")] += 1
        else:
            if r["result"] is None:
                s["real_open"] += 1
                continue
            s["real_profit_cents"] += r["profit_cents"]
            s["real_staked_cents"] += r["stake_cents"]
            if r["result"] == "win":
                s["real_w"] += 1
            elif r["result"] == "loss":
                s["real_l"] += 1
            else:
                s["real_p"] += 1
    for r in db.all("SELECT agent, result, line_move AS clv FROM picks WHERE result IS NOT NULL ORDER BY graded_at"):
        s = stats.get(r["agent"])
        if not s:
            continue
        s["last10"] = (s["last10"] + [r["result"]])[-10:]
        if r["clv"] is not None:
            s["clv"] = ((s["clv"] or 0) * s["clv_n"] + r["clv"]) / (s["clv_n"] + 1)
            s["clv_n"] += 1
    # the same form and closing-line read, but only for bets that had cash on them
    for r in db.all("SELECT b.agent, b.result, p.line_move AS clv FROM bets b JOIN picks p ON p.id=b.pick_id "
                    "WHERE b.kind='real' AND b.personal=0 AND b.result IS NOT NULL ORDER BY b.settled_at, b.id"):
        s = stats.get(r["agent"])
        if not s:
            continue
        s["real_last10"] = (s["real_last10"] + [r["result"]])[-10:]
        if r["clv"] is not None:
            s["real_clv"] = ((s["real_clv"] or 0) * s["real_clv_n"] + r["clv"]) / (s["real_clv_n"] + 1)
            s["real_clv_n"] += 1
    for r in db.all("SELECT agent, COUNT(*) AS n FROM picks GROUP BY agent"):
        if r["agent"] in stats:
            stats[r["agent"]]["picks_total"] = r["n"]
    unit_cents = settings.unit_dollars * 100
    for s in stats.values():
        s["units"] = s["profit_cents"] / unit_cents if unit_cents else 0
        s["roi"] = s["profit_cents"] / s["staked_cents"] if s["staked_cents"] else None
    return stats


def record_line(s: dict) -> str:
    if not s["graded"]:
        return "no graded bets yet"
    clv = f", line moved {s['clv'] * 100:+.1f}% their way on average" if s["clv"] is not None else ""
    return f"{s['w']}-{s['l']}{'-' + str(s['p']) if s['p'] else ''}, {s['units']:+.1f} units on paper{clv}"


def paper_bet(db: DB, pick: dict, settings, balances: dict) -> bool:
    """Paper never runs dry: evaluation keeps going even if the wallet would be empty (it shows as negative)."""
    stake = round(pick["stake_units"] * settings.unit_dollars * 100)
    if stake <= 0:
        return False
    db.run("INSERT INTO bets(pick_id,kind,agent,stake_cents,price,placed_at) VALUES(?,?,?,?,?,?)",
           (pick["id"], "paper", pick["agent"], stake, pick["price"], iso()))
    balances[pick["agent"]] = balances.get(pick["agent"], 0) - stake / 100
    return True


def group_members(db: DB, pick_id: str) -> list[dict]:
    p = db.one("SELECT * FROM picks WHERE id=?", (pick_id,))
    if not p:
        return []
    lead = p["group_id"] or p["id"]
    return db.all("SELECT * FROM picks WHERE (id=? OR group_id=?) AND board_status!='vetoed' ORDER BY created_at", (lead, lead))


def set_personal(db: DB, pick_id: str, personal: bool) -> int:
    """Mark a real bet as personal (kept out of every stat) or put it back. Every tipster's share of the bet moves together."""
    ids = [m["id"] for m in group_members(db, pick_id)] or [pick_id]
    marks = ",".join("?" * len(ids))
    if not db.one(f"SELECT 1 FROM bets WHERE kind='real' AND pick_id IN ({marks})", ids):
        raise ValueError("No real bet recorded on this pick")
    db.run(f"UPDATE bets SET personal=? WHERE kind='real' AND pick_id IN ({marks})", [int(bool(personal)), *ids])
    return len(ids)


def place_real(db: DB, pick_id: str, price: int, stake_dollars: float, book: str | None = None, personal: bool = False) -> list[dict]:
    """Record a real Bovada bet. A merged pick splits stake and result across every tipster who made it."""
    om.dec(price)  # validates
    members = group_members(db, pick_id)
    if not members:
        raise ValueError("Unknown pick")
    if db.one("SELECT 1 FROM bets WHERE kind='real' AND pick_id IN (%s)" % ",".join("?" * len(members)), [m["id"] for m in members]):
        raise ValueError("Already placed")
    total = round(stake_dollars * 100)
    if total < 1:
        raise ValueError("Stake must be positive")
    shares = om.split_cents(total, len(members))
    now = iso()
    with db.tx() as c:
        for m, share in zip(members, shares):
            if share > 0:
                c.execute("INSERT INTO bets(pick_id,kind,agent,stake_cents,price,placed_at,book,personal) VALUES(?,?,?,?,?,?,?,?)",
                          (m["id"], "real", m["agent"], share, price, now, book or m.get("book") or "bovada", int(bool(personal))))
            c.execute("UPDATE picks SET decision='placed', decided_at=? WHERE id=?", (now, m["id"]))
        # a real bet placed after the game was graded settles immediately
        for m in members:
            if m["result"]:
                _settle_rows(c, m["id"], m["result"], _decimal_override(c, m))
    return members


def unplace_real(db: DB, pick_id: str) -> None:
    members = group_members(db, pick_id)
    ids = [m["id"] for m in members]
    with db.tx() as c:
        c.execute("DELETE FROM bets WHERE kind='real' AND pick_id IN (%s)" % ",".join("?" * len(ids)), ids)
        c.executemany("UPDATE picks SET decision=NULL, decided_at=NULL WHERE id=?", [(i,) for i in ids])


def _decimal_override(c, pick: dict) -> float | None:
    """Parlays with a pushed leg pay at the remaining legs' straight prices; otherwise the bet's own price."""
    if pick["market"] != "parlay" or not pick.get("legs"):
        return None
    ids = json.loads(pick["legs"])
    legs = [dict(r) for r in c.execute("SELECT result, price FROM picks WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)]
    if not any(l["result"] == "push" for l in legs):
        return None
    return om.parlay_result([l["result"] for l in legs], [l["price"] for l in legs])[1]


def _settle_rows(c, pick_id: str, result: str, decimal_override: float | None = None) -> None:
    for b in c.execute("SELECT id, stake_cents, price FROM bets WHERE pick_id=? AND result IS NULL", (pick_id,)).fetchall():
        profit = om.profit_cents(b["stake_cents"], b["price"], result, decimal_override)
        c.execute("UPDATE bets SET result=?, profit_cents=?, settled_at=? WHERE id=?", (result, profit, iso(), b["id"]))


def settle(db: DB, pick_id: str, result: str) -> None:
    with db.tx() as c:
        pick = dict(c.execute("SELECT * FROM picks WHERE id=?", (pick_id,)).fetchone())
        _settle_rows(c, pick_id, result, _decimal_override(c, pick))
