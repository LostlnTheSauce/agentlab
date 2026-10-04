import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lab import board, grading, ledger
from lab import oddsmath as om
from lab.config import Settings
from lab.db import DB, iso
from lab.sources.espn import match_game, similarity, web_link
from lab.sources.mlb import ip_to_float
from lab.sources.odds import build_candidates

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def settings(tmp) -> Settings:
    return Settings(data_dir=Path(tmp), db_name="t.sqlite3", anthropic_api_key=None, odds_api_key=None)


def event(bov_home=-110, bov_away=-110, refs=((-120, 100), (-118, -102), (-122, 102)), spread=-3.5, bov_spread=None):
    def book(key, ml, sp):
        return {"key": key, "last_update": iso(NOW - timedelta(minutes=5)), "markets": [
            {"key": "h2h", "outcomes": [{"name": "Home", "price": ml[0]}, {"name": "Away", "price": ml[1]}]},
            {"key": "spreads", "outcomes": [{"name": "Home", "price": -110, "point": sp}, {"name": "Away", "price": -110, "point": -sp}]},
        ]}
    books = [book("bovada", (bov_home, bov_away), bov_spread if bov_spread is not None else spread)]
    books += [book(k, r, spread) for k, r in zip(("pinnacle", "draftkings", "fanduel"), refs)]
    return {"id": "ev1", "sport_key": "americanfootball_nfl", "commence_time": iso(NOW + timedelta(hours=5)),
            "home_team": "Home", "away_team": "Away", "bookmakers": books}


class OddsMath(unittest.TestCase):
    def test_conversions(self):
        self.assertAlmostEqual(om.dec(-110), 1.9090909, places=5)
        self.assertAlmostEqual(om.dec(150), 2.5)
        self.assertEqual(om.american_from_dec(2.5), 150)
        self.assertEqual(om.american_from_dec(1.5), -200)
        with self.assertRaises(ValueError):
            om.dec(50)

    def test_no_vig_and_edge(self):
        p = om.no_vig([-110, -110])
        self.assertAlmostEqual(p[0], 0.5)
        self.assertAlmostEqual(om.edge(0.5, -110), -0.04545, places=4)
        self.assertAlmostEqual(om.edge(0.55, 100), 0.10)

    def test_min_price_is_conservative(self):
        floor = om.min_price(0.55, margin=0.01)
        self.assertGreaterEqual(om.edge(0.55, floor), 0.01 - 1e-9)
        self.assertTrue(om.price_at_least(-110, floor))

    def test_grading(self):
        g = om.grade
        self.assertEqual(g("h2h", "Home", None, "Home", "Away", 21, 17), "win")
        self.assertEqual(g("h2h", "Away", None, "Home", "Away", 21, 17), "loss")
        self.assertEqual(g("h2h", "Draw", None, "Home", "Away", 1, 1), "win")
        self.assertEqual(g("h2h", "Home", None, "Home", "Away", 1, 1, "soccer_epl"), "loss")
        self.assertEqual(g("h2h", "Home", None, "Home", "Away", 20, 20, "americanfootball_nfl"), "push")
        self.assertEqual(g("spreads", "Home", -3.5, "Home", "Away", 21, 17), "win")
        self.assertEqual(g("spreads", "Home", -3.0, "Home", "Away", 20, 17), "push")
        self.assertEqual(g("spreads", "Away", 3.5, "Home", "Away", 20, 17), "win")
        self.assertEqual(g("spreads", "Away", 2.5, "Home", "Away", 20, 17), "loss")
        self.assertEqual(g("totals", "Over", 44.5, "Home", "Away", 24, 21), "win")
        self.assertEqual(g("totals", "Under", 45.0, "Home", "Away", 24, 21), "push")
        self.assertEqual(om.grade_prop("Over", 6.5, 7), "win")

    def test_parlay_and_payouts(self):
        self.assertEqual(om.parlay_result(["win", "loss"], [-110, -110])[0], "loss")
        r, d = om.parlay_result(["win", "push"], [150, -110])
        self.assertEqual(r, "win")
        self.assertAlmostEqual(d, 2.5)
        self.assertEqual(om.profit_cents(100, -110, "win"), 91)
        self.assertEqual(om.profit_cents(100, 150, "loss"), -100)
        self.assertEqual(om.split_cents(100, 3), [34, 33, 33])


class Candidates(unittest.TestCase):
    def test_consensus_and_edge(self):
        cands = build_candidates([event(bov_home=-105)], "americanfootball_nfl", 3, NOW)
        home = next(c for c in cands if c["market"] == "h2h" and c["selection"] == "Home")
        self.assertEqual(home["books"], 3)
        self.assertGreater(home["fair_prob"], 0.5)
        self.assertGreater(home["edge"], 0)

    def test_different_line_is_not_compared(self):
        cands = build_candidates([event(bov_spread=-3.0)], "americanfootball_nfl", 3, NOW)
        sp = next(c for c in cands if c["market"] == "spreads" and c["selection"] == "Home")
        self.assertIsNone(sp["fair_prob"])  # -3 must never be priced with -3.5 quotes
        self.assertEqual(sp["consensus_point"], -3.5)

    def test_started_games_skipped(self):
        self.assertEqual(build_candidates([event()], "americanfootball_nfl", 3, NOW + timedelta(hours=6)), [])


class Matching(unittest.TestCase):
    def test_names(self):
        self.assertGreater(similarity("Brighton and Hove Albion", "Brighton & Hove Albion"), 0.99)
        games = [{"date": iso(NOW), "home": "Miami (OH) RedHawks", "away": "Ohio Bobcats"},
                 {"date": iso(NOW), "home": "Miami Hurricanes", "away": "Florida State Seminoles"}]
        g = match_game("Miami Hurricanes", "Florida St Seminoles", iso(NOW), games)
        self.assertEqual(g["home"], "Miami Hurricanes")
        # a playoff series: same teams on back-to-back days, yesterday's game listed first
        series = [{"date": iso(NOW - timedelta(hours=19.5)), "home": "Milwaukee Brewers", "away": "San Diego Padres", "n": 1},
                  {"date": iso(NOW), "home": "Milwaukee Brewers", "away": "San Diego Padres", "n": 2}]
        self.assertEqual(match_game("Milwaukee Brewers", "San Diego Padres", iso(NOW), series)["n"], 2)
        self.assertEqual(match_game("Milwaukee Brewers", "San Diego Padres", iso(NOW - timedelta(hours=19.5)), series)["n"], 1)
        self.assertEqual(web_link("baseball_mlb", "401908003"), "https://www.espn.com/mlb/game/_/gameId/401908003")
        self.assertIsNone(web_link("baseball_mlb", None))

    def test_innings(self):
        self.assertAlmostEqual(ip_to_float("5.2"), 5 + 2 / 3)


def pick(pid, agent="quinn", **kw):
    base = {"id": pid, "agent": agent, "agent_name": agent.title(), "event_id": "ev1", "sport": "americanfootball_nfl", "home": "Home",
            "away": "Away", "commence": iso(NOW + timedelta(hours=5)), "market": "h2h", "selection": "Home", "point": None, "player": None,
            "price": -105, "fair_prob": 0.53, "edge": om.edge(0.53, -105), "min_price": om.min_price(0.53), "books": 4, "estimated": False,
            "quote_at": iso(NOW - timedelta(minutes=3)), "stake_units": 1, "confidence": 3}
    base.update(kw)
    return base


class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = settings(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_value_veto_and_duplicates(self):
        good, dup, bad = pick("a"), pick("b", agent="rhea"), pick("c", agent="blue", price=-130, edge=om.edge(0.53, -130))
        wallets = {a: 10.0 for a in ("quinn", "rhea", "blue")}
        board.review([good, dup, bad], [], {}, wallets, self.s, NOW)
        self.assertEqual(good["board_status"], "cleared")
        self.assertEqual(dup["group_id"], "a")
        self.assertEqual(bad["board_status"], "vetoed")

    def test_opposite_sides_flagged_and_stale_quotes(self):
        a = pick("a")
        b = pick("b", agent="rhea", selection="Away", quote_at=iso(NOW - timedelta(hours=2)))
        board.review([a, b], [], {}, {"quinn": 10, "rhea": 10}, self.s, NOW)
        self.assertEqual(b["board_status"], "flagged")
        self.assertTrue(any("old" in n for n in b["board_notes"]))

    def test_cold_or_broke_agents_go_paper_only(self):
        a = pick("a")
        board.review([a], [], {"quinn": {"graded": 20, "units": -4, "clv": -0.01}}, {"quinn": 10}, self.s, NOW)
        self.assertTrue(a["paper_only"])
        b = pick("b", agent="rhea")
        board.review([b], [], {}, {"rhea": 0.5}, self.s, NOW)
        self.assertTrue(b["paper_only"])


class LedgerAndGrading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = settings(self.tmp.name)
        self.db = DB(self.s.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def insert(self, p, day="2026-10-01"):
        self.db.run("INSERT INTO picks(id,local_day,created_at,agent,event_id,sport,home,away,commence,market,selection,point,price,fair_prob,edge,stake_units,group_id,legs) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (p["id"], day, iso(NOW), p["agent"], p["event_id"], p["sport"], p["home"], p["away"], p["commence"], p["market"],
                     p["selection"], p["point"], p["price"], p["fair_prob"], p["edge"], p["stake_units"], p.get("group_id"),
                     json.dumps(p["legs"]) if p.get("legs") else None))

    def test_real_bet_split_and_settle(self):
        self.insert(pick("a"))
        self.insert(pick("b", agent="rhea", group_id="a"))
        ledger.place_real(self.db, "a", -105, 2.00)
        rows = self.db.all("SELECT agent, stake_cents FROM bets WHERE kind='real' ORDER BY agent")
        self.assertEqual([r["stake_cents"] for r in rows], [100, 100])
        with self.assertRaises(ValueError):
            ledger.place_real(self.db, "b", -105, 2.00)
        for pid in ("a", "b"):
            grading.manual_grade(self.db, pid, "win")
        w = ledger.wallet_balances(self.db, "real", self.s)
        self.assertAlmostEqual(w["quinn"], 10 + 0.95)
        ledger.unplace_real  # still importable

    def test_auto_grade_from_scores_and_clv(self):
        p = pick("a", commence=iso(NOW - timedelta(hours=5)))
        self.insert(p)
        self.db.run("INSERT INTO events(id,sport,home,away,commence,status,home_score,away_score) VALUES('ev1','americanfootball_nfl','Home','Away',?, 'final', 24, 20)", (p["commence"],))
        self.db.run("INSERT INTO lines(event_id,market,selection,point,bov_price,fair_prob,books,seen_at) VALUES('ev1','h2h','Home',NULL,-120,0.56,4,?)",
                    (iso(NOW - timedelta(hours=5, minutes=10)),))
        self.db.run("UPDATE picks SET created_at=? WHERE id='a'", (iso(NOW - timedelta(hours=9)),))
        ledger.paper_bet(self.db, {**p, "stake_units": 1}, self.s, {})
        n = grading.grade_pending(self.db, espn=None, now=NOW)
        self.assertEqual(n, 1)
        got = self.db.one("SELECT result, clv FROM picks WHERE id='a'")
        self.assertEqual(got["result"], "win")
        self.assertAlmostEqual(got["clv"], om.edge(0.56, -105))
        self.assertEqual(self.db.one("SELECT profit_cents FROM bets")["profit_cents"], 95)

    def test_parlay_grades_after_legs(self):
        self.insert(pick("x", market="h2h", price=150))
        self.insert(pick("y", event_id="ev2", price=-110))
        self.insert(pick("pp", agent="lydon", market="parlay", price=om.american_from_dec(2.5 * om.dec(-110)), legs=["x", "y"]))
        ledger.paper_bet(self.db, {"id": "pp", "agent": "lydon", "stake_units": 1, "price": 377}, self.s, {})
        grading.manual_grade(self.db, "x", "win")
        self.assertEqual(grading.grade_parlays(self.db), 0)
        grading.manual_grade(self.db, "y", "push")
        self.assertEqual(grading.grade_parlays(self.db), 1)
        self.assertEqual(self.db.one("SELECT profit_cents FROM bets WHERE pick_id='pp'")["profit_cents"], 150)


class Web(unittest.TestCase):
    def setUp(self):
        from lab.web import create_app, set_password
        self.tmp = tempfile.TemporaryDirectory()
        s = settings(self.tmp.name)
        self.db = DB(s.db_path)
        set_password(self.db, "correct horse battery")
        self.app = create_app(s, self.db).test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_auth_and_csrf(self):
        self.assertEqual(self.app.get("/api/state").status_code, 401)
        self.assertEqual(self.app.get("/").status_code, 302)
        self.assertIn("e=bad", self.app.post("/login", data={"password": "nope"}).location)
        self.assertEqual(self.app.get("/").location, "/login")
        self.app.post("/login", data={"password": "correct horse battery"})
        self.assertEqual(self.app.get("/api/state").status_code, 200)
        self.assertEqual(self.app.post("/api/pick/x", json={"action": "pass"}).status_code, 400)  # no X-Lab header
        self.assertEqual(self.app.post("/api/pick/x", json={"action": "pass"}, headers={"X-Lab": "1"}).status_code, 404)
        self.assertEqual(self.app.get("/.env").status_code, 404)
        self.assertEqual(self.app.get("/data/lab.sqlite3").status_code, 404)

    def test_lockout(self):
        for _ in range(8):
            self.app.post("/login", data={"password": "nope"})
        self.assertIn("locked", self.app.post("/login", data={"password": "correct horse battery"}).location)


class EndToEnd(unittest.TestCase):
    def test_demo_world_runs_clean(self):
        from lab.demo import seed
        with tempfile.TemporaryDirectory() as tmp:
            s = settings(tmp)
            out = seed(s, days=4)
            db = DB(s.db_path)
            self.assertFalse(db.all("SELECT * FROM runs WHERE status='error'"))
            self.assertGreater(out["picks"], 10)
            self.assertGreater(out["graded"], 5)
            # every graded paper bet is settled consistently with its pick
            bad = db.all("SELECT b.id FROM bets b JOIN picks p ON p.id=b.pick_id WHERE p.result IS NOT NULL AND b.result IS NULL")
            self.assertFalse(bad)
            # nobody holds two open copies of the same bet across days
            dup = db.all("SELECT agent, event_id, market, selection, point, COUNT(*) n FROM picks WHERE market<>'parlay' "
                         "GROUP BY agent, event_id, market, selection, point, player HAVING n>1")
            self.assertFalse(dup, dup)
            from lab.state import build_state
            st = build_state(s, db)
            self.assertEqual(len(st["agents"]), 20)


if __name__ == "__main__":
    unittest.main()


class Parlays(unittest.TestCase):
    def test_leg_and_combined_limits(self):
        lydon = {"id": "lydon", "name": "Lydon"}
        mk = lambda pid, ev, edge, agent="quinn", **kw: {**pick(pid, agent=agent, event_id=ev, fair_prob=0.5, price=om.american_from_dec((1 + edge) / 0.5)),
                                                        "edge": edge, "board_status": "cleared", "reasoning": "r", "group_id": None, **kw}
        pool = [mk("a", "e1", -0.012), mk("b", "e2", -0.02, agent="rhea"), mk("c", "e3", -0.03, agent="blue"),
                mk("d", "e1", -0.005, agent="stormy"), mk("e", "e4", 0.0, agent="coin")]
        opts = board.parlay_options(pool, lydon, "2026-10-01", NOW)
        legsets = [set(o["legs"]) for o in opts]
        self.assertTrue(all("c" not in s and "e" not in s for s in legsets))  # -3% leg and Coin Flip excluded
        self.assertNotIn({"a", "d"}, legsets)                                 # same game never pairs
        self.assertIn({"a", "b"}, legsets)
        best = board.finalize_parlay(dict(opts[0]))
        self.assertEqual(best["board_status"], "cleared")
        self.assertEqual(best["paper_only"], best["edge"] < board.PARLAY_REAL)

    def test_combined_veto(self):
        lydon = {"id": "lydon", "name": "Lydon"}
        mk = lambda pid, ev, edge: {**pick(pid, event_id=ev, fair_prob=0.5, price=om.american_from_dec((1 + edge) / 0.5)),
                                    "edge": edge, "board_status": "cleared", "reasoning": "r", "group_id": None}
        self.assertEqual(board.parlay_options([mk("a", "e1", -0.024), mk("b", "e2", -0.024)], lydon, "d", NOW), [])  # ~ -4.7% combined
