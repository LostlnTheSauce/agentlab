"""Live scores for the games you have money on, straight from ESPN's free scoreboard.

Only games that have started and aren't graded yet, and each sport/date board is cached for a minute,
so a phone sitting on MY BETS costs a handful of requests a minute at most."""

from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import oddsmath as om
from .db import DB, iso, parse, utcnow
from .sources.espn import Espn, match_game, similarity

TTL = 60
_lock = threading.Lock()
_cache: dict[tuple, tuple[float, list[dict]]] = {}


def _board(espn: Espn, sport: str, day) -> list[dict]:
    key = (sport, day)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    games = espn.scoreboard(sport, day)
    with _lock:
        _cache[key] = (time.time(), games)
    return games


def live_scores(db: DB, espn: Espn | None = None) -> dict:
    """{"games": {event_id: score}, "picks": {pick_id: "win"|"loss"|"push"}}: where each open cash bet (and parlay leg)
    would land if its game ended right now."""
    espn = espn or Espn()
    now = utcnow()
    picks = {}
    for r in db.all("SELECT p.* FROM bets b JOIN picks p ON p.id=b.pick_id WHERE b.kind='real' AND b.result IS NULL"):
        if r["market"] == "parlay":
            for leg in json.loads(r["legs"] or "[]"):
                row = db.one("SELECT * FROM picks WHERE id=? AND result IS NULL", (leg,))
                if row:
                    picks[row["id"]] = row
        else:
            picks[r["id"]] = r
    ids = {p["event_id"] for p in picks.values()}
    out = {}
    for ev_id in ids:
        ev = db.one("SELECT id, sport, home, away, commence, status, espn_id FROM events WHERE id=?", (ev_id,))
        if not ev or ev["status"] == "final" or parse(ev["commence"]) > now + timedelta(minutes=10):
            continue
        # ESPN files games under the US Eastern date
        day = parse(ev["commence"]).astimezone(ZoneInfo("America/New_York")).date()
        games = _board(espn, ev["sport"], day)
        g = next((x for x in games if ev["espn_id"] and str(x.get("espn_id")) == str(ev["espn_id"])), None)
        g = g or match_game(ev["home"], ev["away"], ev["commence"], games)
        if not g or g.get("state") not in ("in", "post"):
            continue
        out[ev_id] = {"state": g["state"], "detail": g.get("detail"), "home": g["home"], "away": g["away"],
                      "home_score": g.get("home_score"), "away_score": g.get("away_score"), "at": iso(now)}
    status = {}
    for p in picks.values():
        g = out.get(p["event_id"])
        if not g or p["player"] or g["home_score"] is None or g["away_score"] is None:
            continue
        hs, aws = g["home_score"], g["away_score"]
        if similarity(p["home"], g["away"]) > similarity(p["home"], g["home"]):  # ESPN can flip home/away at neutral sites
            hs, aws = aws, hs
        try:
            status[p["id"]] = om.grade(p["market"], p["selection"], p["point"], p["home"], p["away"], hs, aws, p["sport"])
        except ValueError:
            continue
    return {"games": out, "picks": status}
