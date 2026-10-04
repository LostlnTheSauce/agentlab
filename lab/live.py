"""Live scores for the games you have money on, straight from ESPN's free scoreboard.

Only games that have started and aren't graded yet, and each sport/date board is cached for a minute,
so a phone sitting on MY BETS costs a handful of requests a minute at most."""

from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

from .db import DB, iso, parse, utcnow
from .sources.espn import Espn, match_game

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


def live_scores(db: DB, espn: Espn | None = None) -> dict[str, dict]:
    espn = espn or Espn()
    now = utcnow()
    ids = set()
    for r in db.all("SELECT p.event_id, p.legs FROM bets b JOIN picks p ON p.id=b.pick_id WHERE b.kind='real' AND b.result IS NULL"):
        ids.add(r["event_id"])
        for leg in json.loads(r["legs"] or "[]"):
            row = db.one("SELECT event_id FROM picks WHERE id=?", (leg,))
            if row:
                ids.add(row["event_id"])
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
    return out
