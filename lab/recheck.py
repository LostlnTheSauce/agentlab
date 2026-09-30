"""The CHECK PRICE button: is Bovada's price still worth taking right now?"""

from __future__ import annotations

from . import oddsmath as om
from .db import DB, iso, utcnow
from .sources.odds import OddsClient, book_name, build_candidates


def recheck(settings, db: DB, pick_id: str, odds: OddsClient | None = None) -> dict:
    p = db.one("SELECT * FROM picks WHERE id=?", (pick_id,))
    if not p:
        raise ValueError("Unknown pick")
    if p["market"] == "parlay" or p["player"]:
        raise ValueError("Price checks work for straight bets only. Check parlays and props on Bovada directly.")
    odds = odds or OddsClient(settings, db)
    now = utcnow()
    events = odds.one_game(p["sport"], p["event_id"], p["market"])
    cands = [c for c in build_candidates(events, p["sport"], settings.min_books, now, settings.my_books) if c["event_id"] == p["event_id"] and c["market"] == p["market"]]
    with db.tx() as c:
        c.executemany("INSERT INTO lines(event_id,market,selection,point,bov_price,fair_prob,books,seen_at) VALUES(?,?,?,?,?,?,?,?)",
                      [(x["event_id"], x["market"], x["selection"], x["point"], x["price"], x["fair_prob"], x["books"], iso(now)) for x in cands])
    same = next((c for c in cands if c["selection"] == p["selection"] and c["point"] == p["point"]), None)
    moved = next((c for c in cands if c["selection"] == p["selection"]), None)
    now_c = same or moved
    if not now_c:
        out = {"status": "gone", "text": "None of your books is offering this bet right now. Pass.", "checked_at": iso(now)}
    else:
        ok = p["min_price"] is None or (same is not None and om.price_at_least(same["price"], p["min_price"]))
        at = f" at {book_name(now_c.get('book'))}" if len(settings.my_books) > 1 else ""
        line = f"{now_c['selection']}" + (f" {now_c['point']:+g}" if p["market"] == "spreads" else f" {now_c['point']:g}" if p["market"] == "totals" else "")
        edge = f", {now_c['edge'] * 100:+.2f}% vs fair" if now_c.get("edge") is not None else ""
        if same is None:
            status, text = "moved", f"The line moved: now {line} {om.fmt(now_c['price'])}{at}{edge}. Different number than the pick; judge it fresh."
        elif ok:
            status, text = "good", f"Still good: {line} {om.fmt(same['price'])}{at}{edge}, at or above the {om.fmt(p['min_price'])} floor."
        else:
            status, text = "worse", f"Price got worse: {line} {om.fmt(same['price'])}{at}{edge}, past the {om.fmt(p['min_price'])} floor. Pass."
        out = {"status": status, "text": text, "price": now_c["price"], "book": now_c.get("book"),
               "book_name": book_name(now_c.get("book")), "prices": now_c.get("prices") or {}, "links": now_c.get("links") or {},
               "same_line": same is not None, "min_price": p["min_price"], "point": now_c["point"], "edge": now_c.get("edge"), "checked_at": iso(now)}
    db.put(f"recheck:{pick_id}", out)
    return out
