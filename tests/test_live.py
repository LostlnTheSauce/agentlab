import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from lab.db import DB, iso, utcnow
from lab import live


class FakeEspn:
    def __init__(self, games):
        self.games, self.calls = games, 0

    def scoreboard(self, sport, day):
        self.calls += 1
        return self.games


class LiveScores(unittest.TestCase):
    def test_only_started_open_cash_bets(self):
        live._cache.clear()
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "t.sqlite3")
            now = utcnow()
            for ev, start in (("today", now - timedelta(hours=1)), ("later", now + timedelta(hours=5))):
                db.run("INSERT INTO events (id, sport, home, away, commence, espn_id) VALUES (?,?,?,?,?,?)",
                       (ev, "baseball_mlb", "Milwaukee Brewers", "San Diego Padres", iso(start), "2" if ev == "today" else None))
                db.run("INSERT INTO picks (id, local_day, created_at, agent, event_id, sport, home, away, commence, market, selection, price, stake_units) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("p" + ev, "d", iso(now), "quinn", ev, "baseball_mlb", "Milwaukee Brewers",
                                                             "San Diego Padres", iso(start), "h2h", "Milwaukee Brewers", -132, 1))
                db.run("INSERT INTO bets (pick_id, kind, agent, stake_cents, price, placed_at) VALUES (?,?,?,?,?,?)",
                       ("p" + ev, "real", "quinn", 200, -132, iso(now)))
            games = [{"espn_id": "1", "date": iso(now - timedelta(hours=20)), "home": "Milwaukee Brewers", "away": "San Diego Padres",
                      "state": "post", "detail": "Final", "home_score": 3.0, "away_score": 2.0},
                     {"espn_id": "2", "date": iso(now - timedelta(hours=1)), "home": "Milwaukee Brewers", "away": "San Diego Padres",
                      "state": "in", "detail": "Top 7th", "home_score": 0.0, "away_score": 1.0}]
            espn = FakeEspn(games)
            out = live.live_scores(db, espn)
            self.assertEqual(list(out), ["today"])  # the later game hasn't started
            self.assertEqual(out["today"]["detail"], "Top 7th")  # today's game, not yesterday's final
            live.live_scores(db, espn)
            self.assertEqual(espn.calls, 1)  # second call within a minute is cached


if __name__ == "__main__":
    unittest.main()
