"""Grade finished games from ESPN final scores (free) and compute closing line value."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import ledger
from . import oddsmath as om
from .db import DB, iso, parse, utcnow
from .sources.espn import Espn, similarity
from .sources.odds import SPORT_INFO


def line_move(p: dict, close_fair: float | None) -> float | None:
    """Did the market move toward this pick after it was made? The closing fair chance of the pick winning, relative to
    the fair chance when it was picked: +0.02 means the market came 2% the tipster's way. Unlike price-vs-close (clv),
    this doesn't charge the tipster for the bookmaker's cut, so zero really means "no better than the market"."""
    if not close_fair or not p.get("fair_prob") or p.get("estimated"):
        return None
    return close_fair / p["fair_prob"] - 1


def closing(db: DB, p: dict) -> tuple[int | None, float | None]:
    """Last consensus seen before kickoff, if it was captured after the pick was made."""
    row = db.one(
        "SELECT bov_price, fair_prob, seen_at FROM lines WHERE event_id=? AND market=? AND selection=? AND point IS ? "
        "AND seen_at<=? AND fair_prob IS NOT NULL ORDER BY seen_at DESC LIMIT 1",
        (p["event_id"], p["market"], p["selection"], p["point"], p["commence"]),
    )
    if not row or parse(row["seen_at"]) <= parse(p["created_at"]) + timedelta(minutes=5):
        return None, None
    return row["bov_price"], row["fair_prob"]


def grade_pending(db: DB, espn: Espn | None = None, now: datetime | None = None) -> int:
    espn = espn or Espn()
    now = now or utcnow()
    done = 0
    pending = db.all("SELECT * FROM picks WHERE result IS NULL AND market!='parlay' ORDER BY commence")
    for p in pending:
        length = SPORT_INFO.get(p["sport"], {}).get("length_h", 3.5)
        if now < parse(p["commence"]) + timedelta(hours=length):
            continue
        ev = db.one("SELECT * FROM events WHERE id=?", (p["event_id"],)) or {}
        game = None
        if ev.get("status") == "final":
            game = {"home_score": ev["home_score"], "away_score": ev["away_score"], "completed": True, "espn_id": ev.get("espn_id")}
        else:
            game = espn.find(p["sport"], p["home"], p["away"], p["commence"])
            if game and game["completed"] and game["home_score"] is not None:
                # ESPN's home/away can be flipped at neutral sites; align to the odds feed's teams.
                if similarity(p["home"], game["away"]) > similarity(p["home"], game["home"]):
                    game = {**game, "home_score": game["away_score"], "away_score": game["home_score"]}
                db.run("UPDATE events SET status='final', home_score=?, away_score=?, final_at=?, espn_id=? WHERE id=?",
                       (game["home_score"], game["away_score"], iso(now), game["espn_id"], p["event_id"]))
        if not game or not game.get("completed") or game.get("home_score") is None:
            continue
        actual = None
        if p["player"]:
            actual = espn.player_stat(p["sport"], game["espn_id"], p["player"], p["market"]) if game.get("espn_id") else None
            if actual is None:
                continue  # left for manual grading
            result = om.grade_prop(p["selection"], p["point"], actual)
        else:
            try:
                result = om.grade(p["market"], p["selection"], p["point"], p["home"], p["away"], game["home_score"], game["away_score"], p["sport"])
            except ValueError:
                continue
        close_price, close_fair = (None, None) if p["player"] else closing(db, p)
        clv = om.edge(close_fair, p["price"]) if close_fair else None
        db.run("UPDATE picks SET result=?, graded_at=?, actual=?, close_price=?, close_fair=?, clv=?, line_move=? WHERE id=?",
               (result, iso(now), actual, close_price, close_fair, clv, line_move(p, close_fair), p["id"]))
        ledger.settle(db, p["id"], result)
        done += 1
    done += grade_parlays(db, now)
    return done


def grade_parlays(db: DB, now: datetime | None = None) -> int:
    done = 0
    for p in db.all("SELECT * FROM picks WHERE result IS NULL AND market='parlay'"):
        ids = json.loads(p["legs"] or "[]")
        legs = db.all("SELECT id, result, price FROM picks WHERE id IN (%s)" % ",".join("?" * len(ids)), ids) if ids else []
        results = [l["result"] for l in legs]
        if not ids or len(legs) != len(ids):
            continue
        if "loss" in results:
            result = "loss"
        elif None in results:
            continue
        else:
            result, _ = om.parlay_result(results, [l["price"] for l in legs])
        db.run("UPDATE picks SET result=?, graded_at=? WHERE id=?", (result, iso(now), p["id"]))
        ledger.settle(db, p["id"], result)
        done += 1
    return done


def manual_grade(db: DB, pick_id: str, result: str) -> None:
    if result not in ("win", "loss", "push", "void"):
        raise ValueError("Result must be win, loss, push or void")
    p = db.one("SELECT * FROM picks WHERE id=?", (pick_id,))
    if not p:
        raise ValueError("Unknown pick")
    if p["result"]:
        raise ValueError("Already graded")
    db.run("UPDATE picks SET result=?, graded_at=? WHERE id=?", (result, iso(), pick_id))
    ledger.settle(db, pick_id, result)


def regrade(db: DB, pick_id: str, result: str, espn: Espn | None = None, now: datetime | None = None) -> dict:
    """Fix a result that was graded wrong. `result` is "espn" (throw away the saved score and ask ESPN again) or
    "win" / "loss" / "push" (set it by hand). Every pick on the same bet moves together, the money is re-settled, and
    any parlay with this bet as a leg is worked out again."""
    if result not in ("espn", "win", "loss", "push"):
        raise ValueError("Result must be espn, win, loss or push")
    p = db.one("SELECT * FROM picks WHERE id=?", (pick_id,))
    if not p:
        raise ValueError("Unknown pick")
    if p["market"] == "parlay":
        raise ValueError("Fix the leg that is wrong; the parlay follows its legs")
    if result == "espn":  # a wrong score affects every bet on the game
        targets = db.all("SELECT id FROM picks WHERE event_id=? AND market!='parlay' AND result IS NOT NULL", (p["event_id"],))
    else:
        targets = db.all("SELECT id FROM picks WHERE event_id=? AND market=? AND selection=? AND point IS ? AND player IS ?",
                         (p["event_id"], p["market"], p["selection"], p["point"], p["player"]))
    ids = [t["id"] for t in targets] or [pick_id]
    parlays = [q["id"] for q in db.all("SELECT id, legs FROM picks WHERE market='parlay' AND legs IS NOT NULL")
               if set(json.loads(q["legs"] or "[]")) & set(ids)]
    marks = ",".join("?" * len(ids + parlays))
    with db.tx() as c:
        c.execute(f"UPDATE bets SET result=NULL, profit_cents=NULL, settled_at=NULL WHERE pick_id IN ({marks})", ids + parlays)
        c.execute(f"UPDATE picks SET result=NULL, graded_at=NULL WHERE id IN ({marks})", ids + parlays)
        if result == "espn":
            c.execute("UPDATE events SET status='scheduled', home_score=NULL, away_score=NULL, final_at=NULL, espn_id=NULL WHERE id=?", (p["event_id"],))
    if result == "espn":
        grade_pending(db, espn, now)
    else:
        for i in ids:
            db.run("UPDATE picks SET result=?, graded_at=? WHERE id=?", (result, iso(now), i))
            ledger.settle(db, i, result)
        grade_parlays(db, now)
    after = db.one("SELECT result FROM picks WHERE id=?", (pick_id,))["result"]
    db.feed(f"Result corrected: {p['selection']} ({p['away']} at {p['home']}) is now {(after or 'waiting on a score').upper()}.")
    return {"picks": len(ids), "parlays": len(parlays), "result": after}
