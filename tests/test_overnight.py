import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lab import meeting, roster
from lab import oddsmath as om
from lab.agents import Brain
from lab.backup import backup
from lab.config import Settings
from lab.db import DB, iso
from lab.pipeline import Lab
from lab.recheck import recheck
from lab.sources.odds import OddsClient
from lab.strategies import custom, validate_params

NOW = datetime(2026, 10, 4, 3, tzinfo=timezone.utc)  # Saturday night Central -> week ending Oct 4


def settings(tmp) -> Settings:
    return Settings(data_dir=Path(tmp), db_name="t.sqlite3", anthropic_api_key=None, odds_api_key="test", ntfy_topic=None)


def cand(sel="Home", market="h2h", price=120, fair=0.46, point=None, **kw):
    c = {"id": sel + market, "event_id": "ev1", "sport": "americanfootball_nfl", "home": "Home", "away": "Away",
         "commence": iso(NOW + timedelta(hours=5)), "market": market, "selection": sel, "point": point, "player": None,
         "price": price, "fair_prob": fair, "edge": om.edge(fair, price), "books": 5, "dispersion": 0.02}
    c.update(kw)
    return c


class Roster(unittest.TestCase):
    def test_seeded_once_with_seats(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "r.sqlite3")
            roster.ensure(db)
            roster.ensure(db)
            team = roster.active(db)
            self.assertEqual(len(team), 20)
            self.assertEqual([t["seat"] for t in team if t["desk"] == "nfl"], [0, 1, 2, 3, 4])


class Strategies(unittest.TestCase):
    def test_custom_signal_and_side(self):
        agent = {"sports": ["americanfootball_nfl"], "params": validate_params({"markets": ["h2h"], "side": "underdog", "signal": "rest", "min_edge": -0.03}, ["americanfootball_nfl"])}
        ctx = {"ev1": {"rest": {"Home": 9, "Away": 4}}}
        opts = custom([cand(), cand(sel="Away", price=-140, fair=0.57)], ctx, agent)
        self.assertEqual([o["cand"]["selection"] for o in opts], ["Home"])  # rested underdog only
        self.assertIn("rested", opts[0]["signal"])

    def test_validate_clamps_nonsense(self):
        p = validate_params({"sports": ["cricket"], "markets": ["exotic"], "side": "??", "signal": "vibes", "min_price": 50, "max_price": -5000, "min_edge": 0.5}, ["baseball_mlb"])
        self.assertEqual(p["sports"], ["baseball_mlb"])
        self.assertEqual(p["side"], "any")
        self.assertEqual(p["signal"], "none")
        self.assertLessEqual(p["min_edge"], 0.02)
        self.assertLessEqual(om.dec(p["min_price"]), om.dec(p["max_price"]))


class Meetings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = settings(self.tmp.name)
        self.lab = Lab(self.s, DB(self.s.db_path), brain=Brain(self.s))

    def tearDown(self):
        self.tmp.cleanup()

    def _losing(self, agent, n=14):
        db = self.lab.db
        for i in range(n):
            pid = f"{agent}{i}"
            db.run("INSERT INTO picks(id,local_day,created_at,agent,event_id,sport,home,away,commence,market,selection,price,fair_prob,edge,stake_units,result,graded_at,clv) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, "2026-10-01", iso(NOW - timedelta(days=2)), agent, f"e{i}", "americanfootball_nfl", "H", "A", iso(NOW - timedelta(days=2)),
                    "h2h", "H", -110, 0.5, -0.045, 1, "loss", iso(NOW - timedelta(days=1)), -0.02))
            db.run("INSERT INTO bets(pick_id,kind,agent,stake_cents,price,placed_at,result,profit_cents,settled_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (pid, "paper", agent, 100, -110, iso(NOW), "loss", -100, iso(NOW)))

    def test_fire_hire_and_jail(self):
        self._losing("connie")
        m = meeting.hold(self.lab, now=NOW)
        self.assertEqual(m["fired"], "connie")
        everyone = roster.lookup(self.lab.db)
        self.assertEqual(everyone["connie"]["status"], "jailed")
        new = everyone[m["hired"]]
        self.assertEqual((new["status"], new["desk"], new["seat"], new["replaces"]), ("active", "nfl", everyone["connie"]["seat"], "connie"))
        self.assertEqual(len(roster.active(self.lab.db)), 20)
        self.assertIsNone(meeting.hold(self.lab, now=NOW))  # once a week
        from lab.state import build_state
        st = build_state(self.s, self.lab.db)
        self.assertEqual([t["id"] for t in st["roster"]["jail"]], ["connie"])
        self.assertEqual(st["meetings"][0]["detail"]["fired_name"], "Connie")

    def test_small_samples_and_protected_are_safe(self):
        self._losing("connie", n=5)
        self._losing("coin", n=20)
        m = meeting.hold(self.lab, now=NOW)
        self.assertIsNone(m["fired"])

    def test_due_only_sunday_night(self):
        sunday_night = datetime(2026, 10, 5, 3, tzinfo=timezone.utc)  # Sun 10 PM Central
        self.assertTrue(meeting.due(self.lab.db, self.s, sunday_night))
        self.assertFalse(meeting.due(self.lab.db, self.s, NOW))


class BackupAndRecheck(unittest.TestCase):
    def test_backup_keeps_14(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "lab.sqlite3")
            db.put("x", 1)
            for d in range(20):
                backup(db.path, f"2026-10-{d + 1:02d}")
            files = sorted((Path(tmp) / "backups").glob("*.sqlite3"))
            self.assertEqual(len(files), 14)
            self.assertTrue(files[-1].name.endswith("2026-10-20.sqlite3"))

    def test_recheck_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = settings(tmp)
            db = DB(s.db_path)
            commence = iso(datetime.now(timezone.utc) + timedelta(hours=6))
            db.run("INSERT INTO picks(id,local_day,created_at,agent,event_id,sport,home,away,commence,market,selection,point,price,fair_prob,edge,min_price,stake_units) "
                   "VALUES('p','2026-10-01',?,'rhea','ev1','americanfootball_nfl','Home','Away',?,'spreads','Home',-3.5,-110,0.52,?,-115,1)",
                   (iso(), commence, om.edge(0.52, -110)))

            def feed(bov_point, bov_price):
                def book(key, pt, pr):
                    return {"key": key, "last_update": iso(), "markets": [{"key": "spreads", "outcomes": [
                        {"name": "Home", "price": pr, "point": pt}, {"name": "Away", "price": -110, "point": -pt}]}]}
                ev = {"id": "ev1", "sport_key": "americanfootball_nfl", "commence_time": commence, "home_team": "Home", "away_team": "Away",
                      "bookmakers": [book("bovada", bov_point, bov_price)] + [book(k, -3.5, -112) for k in ("pinnacle", "draftkings", "fanduel")]}
                return lambda url: ([ev], {"x-requests-last": "1", "x-requests-remaining": "400", "x-requests-used": "100"})

            self.assertEqual(recheck(s, db, "p", OddsClient(s, db, feed(-3.5, -108)))["status"], "good")
            self.assertEqual(recheck(s, db, "p", OddsClient(s, db, feed(-3.5, -125)))["status"], "worse")
            self.assertEqual(recheck(s, db, "p", OddsClient(s, db, feed(-4.5, -110)))["status"], "moved")
            self.assertEqual(db.get("recheck:p")["status"], "moved")


if __name__ == "__main__":
    unittest.main()


class EnvFile(unittest.TestCase):
    def test_inline_comments_and_quotes(self):
        import os
        from lab.config import load_env
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".env"
            p.write_text('LABTEST_A=1          # one unit\nLABTEST_B="has # hash"\nLABTEST_C=sk-ant-abc#def\n', encoding="utf-8")
            for k in ("LABTEST_A", "LABTEST_B", "LABTEST_C"):
                os.environ.pop(k, None)
            load_env(p)
            self.assertEqual((os.environ["LABTEST_A"], os.environ["LABTEST_B"], os.environ["LABTEST_C"]), ("1", "has # hash", "sk-ant-abc#def"))


class Duplicates(unittest.TestCase):
    def _pick(self, db, pid, agent, day, created, **kw):
        row = {"id": pid, "local_day": day, "created_at": created, "agent": agent, "event_id": "ev1", "sport": "americanfootball_nfl",
               "home": "Houston Texans", "away": "Dallas Cowboys", "commence": iso(datetime.now(timezone.utc) + timedelta(days=3)),
               "market": "h2h", "selection": "Dallas Cowboys", "price": 130, "stake_units": 1, "decision": None}
        row.update(kw)
        cols = ",".join(row)
        db.run(f"INSERT INTO picks({cols}) VALUES({','.join('?' * len(row))})", list(row.values()))
        db.run("INSERT INTO bets(pick_id,kind,agent,stake_cents,price,placed_at) VALUES(?,?,?,?,?,?)", (pid, "paper", agent, 100, 130, created))

    def test_same_tipster_repeat_removed_other_tipster_merged(self):
        from lab.pipeline import dedupe_open
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(Path(tmp) / "d.sqlite3")
            self._pick(db, "day1", "ursula", "2026-09-29", "2026-09-29T13:00:00Z")
            self._pick(db, "day2", "ursula", "2026-09-30", "2026-09-30T13:00:00Z")      # repeat: remove
            self._pick(db, "kept", "ursula", "2026-09-30", "2026-09-30T13:01:00Z", decision="placed")  # you acted on it: keep
            self._pick(db, "other", "quinn", "2026-09-30", "2026-09-30T13:02:00Z")     # different tipster: merge
            self.assertEqual(dedupe_open(db), 1)
            ids = {r["id"]: r for r in db.all("SELECT id, group_id FROM picks")}
            self.assertEqual(set(ids), {"day1", "kept", "other"})
            self.assertEqual(ids["other"]["group_id"], "day1")
            self.assertEqual(db.one("SELECT COUNT(*) n FROM bets WHERE pick_id='day2'")["n"], 0)
            self.assertEqual(dedupe_open(db), 0)  # idempotent


class LineShopping(unittest.TestCase):
    def _event(self):
        commence = iso(datetime.now(timezone.utc) + timedelta(hours=6))

        def book(key, home, away):
            return {"key": key, "last_update": iso(), "markets": [{"key": "h2h", "outcomes": [
                {"name": "Home", "price": home}, {"name": "Away", "price": away}]}]}
        return {"id": "ev1", "sport_key": "americanfootball_nfl", "commence_time": commence, "home_team": "Home", "away_team": "Away",
                "bookmakers": [book("bovada", 130, -150), book("lowvig", 138, -148), book("betonlineag", 135, -155),
                               book("pinnacle", 136, -146), book("draftkings", 130, -150), book("fanduel", 132, -152)]}

    def test_best_book_wins_and_is_left_out_of_fair(self):
        from lab.sources.odds import build_candidates
        solo = {c["selection"]: c for c in build_candidates([self._event()], "americanfootball_nfl", 3, my_books=["bovada"])}
        shop = {c["selection"]: c for c in build_candidates([self._event()], "americanfootball_nfl", 3, my_books=["bovada", "lowvig", "betonlineag"])}
        self.assertEqual((solo["Home"]["book"], solo["Home"]["price"]), ("bovada", 130))
        self.assertEqual((shop["Home"]["book"], shop["Home"]["price"]), ("lowvig", 138))
        self.assertEqual(shop["Home"]["prices"], {"bovada": 130, "lowvig": 138, "betonlineag": 135})
        self.assertEqual(shop["Away"]["book"], "lowvig")  # -148 beats -150/-155
        self.assertGreater(shop["Home"]["edge"], solo["Home"]["edge"])
        self.assertEqual(shop["Home"]["books"], 5)  # every book except LowVig itself

    def test_real_bet_records_book_and_alerts(self):
        from lab import alerts, grading, ledger, notify
        with tempfile.TemporaryDirectory() as tmp:
            s = settings(tmp)
            s.ntfy_topic = "t"
            db = DB(s.db_path)
            db.run("INSERT INTO picks(id,local_day,created_at,agent,event_id,sport,home,away,commence,market,selection,price,stake_units,book) "
                   "VALUES('p','2026-10-01',?,'ursula','ev1','americanfootball_nfl','Houston Texans','Dallas Cowboys',?,'h2h','Dallas Cowboys',130,1,'lowvig')",
                   (iso(), iso()))
            ledger.place_real(db, "p", 135, 2.0, "lowvig")
            self.assertEqual(db.one("SELECT book FROM bets WHERE kind='real'")["book"], "lowvig")
            sent = []
            orig = notify.push
            notify.push = lambda st, title, body, priority="default": sent.append((title, body)) or True
            try:
                was = alerts.open_real_picks(db)
                grading.manual_grade(db, "p", "win")
                self.assertEqual(alerts.graded(s, db, was), 1)
            finally:
                notify.push = orig
            self.assertEqual(sent[0][0], "Bet won!")
            self.assertIn("WON Dallas Cowboys moneyline (LowVig): +$2.70", sent[0][1])


class WaterCooler(unittest.TestCase):
    def test_fallback_chat_once_per_moment(self):
        from lab import cooler
        with tempfile.TemporaryDirectory() as tmp:
            s = settings(tmp)
            lab = Lab(s, DB(s.db_path), brain=Brain(s))
            day = lab.today()
            lab.db.run("INSERT INTO picks(id,local_day,created_at,agent,event_id,sport,home,away,commence,market,selection,price,edge,stake_units) "
                       "VALUES('p',?,?,'ursula','e','americanfootball_nfl','H','A',?,'h2h','A',150,0.01,1)", (day, iso(), iso()))
            c = cooler.chat(lab, "morning")
            self.assertTrue(c and c["lines"][0]["speaker"] == "ursula")
            self.assertIsNone(cooler.chat(lab, "morning"))
            from lab.state import build_state
            self.assertEqual(build_state(s, lab.db)["cooler"][0]["kind"], "morning")


class FreshSlate(unittest.TestCase):
    def test_clear_today_keeps_what_you_acted_on(self):
        from lab import ledger
        with tempfile.TemporaryDirectory() as tmp:
            s = settings(tmp)
            lab = Lab(s, DB(s.db_path), brain=Brain(s))
            day = lab.today()
            for pid, decision in (("a", None), ("b", "passed"), ("c", None)):
                lab.db.run("INSERT INTO picks(id,local_day,created_at,agent,event_id,sport,home,away,commence,market,selection,price,stake_units,decision) "
                           "VALUES(?,?,?,'quinn',?,'americanfootball_nfl','H','A',?,'h2h','H',-110,1,?)", (pid, day, iso(), pid, iso(), decision))
                ledger.paper_bet(lab.db, {"id": pid, "agent": "quinn", "stake_units": 1, "price": -110}, s, {})
            ledger.place_real(lab.db, "c", -110, 1.0)
            lab.db.run("INSERT INTO memos(local_day,text,created_at) VALUES(?,?,?)", (day, "m", iso()))
            self.assertEqual(lab.clear_today()["picks"], 1)
            self.assertEqual({r["id"] for r in lab.db.all("SELECT id FROM picks")}, {"b", "c"})
            self.assertIsNone(lab.db.one("SELECT 1 FROM memos"))
            self.assertIsNone(lab.db.one("SELECT 1 FROM bets WHERE pick_id='a'"))


class ParlayBooks(unittest.TestCase):
    def test_parlay_stays_at_one_book(self):
        from lab import board
        lydon = {"id": "lydon", "name": "Lydon"}
        now = datetime.now(timezone.utc)

        def leg(pid, ev, prices, fair=0.5):
            best = max(prices, key=lambda k: om.dec(prices[k]))
            return {"id": pid, "agent": "quinn", "agent_name": "Quinn", "event_id": ev, "sport": "americanfootball_nfl", "home": "H", "away": "A",
                    "commence": iso(now + timedelta(hours=5)), "market": "h2h", "selection": "H", "point": None, "player": None,
                    "price": prices[best], "book": best, "prices": prices, "fair_prob": fair, "edge": om.edge(fair, prices[best]),
                    "books": 6, "estimated": False, "board_status": "cleared", "reasoning": "r", "group_id": None}
        a = leg("a", "e1", {"lowvig": 104, "bovada": 100})
        b = leg("b", "e2", {"bovada": 102, "lowvig": 101})
        c = leg("c", "e3", {"betonlineag": 105})
        opts = board.parlay_options([a, b, c], lydon, "d", now)
        ab = next(o for o in opts if set(o["legs"]) == {"a", "b"})
        self.assertEqual(ab["book"], "lowvig")  # 2.04*2.01 beats 2.00*2.02
        self.assertEqual(ab["price"], om.american_from_dec(2.04 * 2.01))
        self.assertFalse(any("c" in o["legs"] for o in opts))  # no single book has c with a or b


class QuickBetLinks(unittest.TestCase):
    def test_only_real_sportsbook_links_survive(self):
        from lab.sources.odds import safe_link
        self.assertEqual(safe_link("lowvig", "https://sports.lowvig.ag/sportsbook/football/nfl/game/1"), "https://sports.lowvig.ag/sportsbook/football/nfl/game/1")
        self.assertEqual(safe_link("bovada", "https://www.bovada.lv/sports/football/nfl/x"), "https://www.bovada.lv/sports/football/nfl/x")
        self.assertIsNone(safe_link("lowvig", "http://sports.lowvig.ag/x"))           # not https
        self.assertIsNone(safe_link("lowvig", "https://lowvig.ag.evil.com/x"))        # look-alike host
        self.assertIsNone(safe_link("lowvig", "https://sports.betonline.ag/x"))       # another book's domain
        self.assertIsNone(safe_link("lowvig", "javascript:alert(1)"))

    def test_candidates_carry_links(self):
        from lab.sources.odds import build_candidates
        commence = iso(datetime.now(timezone.utc) + timedelta(hours=6))

        def book(key, link, home):
            return {"key": key, "link": link, "last_update": iso(), "markets": [{"key": "h2h", "outcomes": [
                {"name": "Home", "price": home}, {"name": "Away", "price": -150}]}]}
        ev = {"id": "e", "sport_key": "americanfootball_nfl", "commence_time": commence, "home_team": "Home", "away_team": "Away",
              "bookmakers": [book("bovada", "https://www.bovada.lv/g", 130), book("lowvig", "https://sports.lowvig.ag/g", 138),
                             book("pinnacle", None, 136), book("draftkings", None, 130), book("fanduel", None, 132)]}
        home = next(c for c in build_candidates([ev], "americanfootball_nfl", 3, my_books=["bovada", "lowvig"]) if c["selection"] == "Home")
        self.assertEqual(home["links"], {"bovada": "https://www.bovada.lv/g", "lowvig": "https://sports.lowvig.ag/g"})
