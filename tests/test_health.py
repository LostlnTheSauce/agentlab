import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lab import health
from lab.agents import Brain
from lab.config import Settings
from lab.db import DB, iso
from lab.pipeline import Lab
from lab.sources.odds import OddsClient

NOW = datetime(2026, 10, 11, 15, tzinfo=timezone.utc)  # Sunday 10 AM Central


def settings(tmp):
    return Settings(data_dir=Path(tmp), db_name="t.sqlite3", anthropic_api_key=None, odds_api_key="k", ntfy_topic=None)


class Health(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = settings(self.tmp.name)
        self.db = DB(self.s.db_path)
        self.db.put("last_tick", iso(NOW))

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self, now=NOW):
        return [i["code"] for i in health.issues(self.db, self.s, now)]

    def _slate(self, status, **detail):
        self.db.run("INSERT INTO runs(kind,local_day,started_at,status,detail) VALUES('slate','2026-10-11',?,?,?)", (iso(NOW), status, json.dumps(detail)))

    def test_quiet_when_all_is_well(self):
        self._slate("ok", fetched=["americanfootball_nfl"], picks=6, notes=[])
        self.assertEqual(self.codes(), [])
        self.assertEqual(health.slate_today(self.db, self.s, NOW)["why"], "")

    def test_slate_that_got_no_prices_says_why(self):
        self._slate("skipped", fetched=[], picks=0, notes=["Daily odds budget reached (16/16 credits)"])
        self.assertEqual(self.codes(), ["slate"])
        self.assertIn("budget reached", health.slate_today(self.db, self.s, NOW)["why"])

    def test_no_picks_but_prices_is_not_an_alarm(self):
        self._slate("ok", fetched=["americanfootball_nfl"], picks=0, candidates=180, notes=[])
        self.assertEqual(self.codes(), [])
        self.assertIn("no tipster found one", health.slate_today(self.db, self.s, NOW)["why"])

    def test_missing_slate_stopped_job_low_credits_and_stuck_bet(self):
        self.assertEqual(self.codes(), ["noslate"])                       # it's 10 AM and nothing ran
        self.assertEqual(self.codes(NOW + timedelta(hours=2))[0], "cron")  # and the job has gone quiet
        self.db.run("INSERT INTO credits(at,local_day,what,cost,remaining,used) VALUES(?,?,?,?,?,?)", (iso(NOW), "2026-10-11", "x", 3, 12, 488))
        self.db.run("INSERT INTO picks(id,local_day,created_at,agent,event_id,sport,home,away,commence,market,selection,price,stake_units) "
                    "VALUES('p','2026-10-10',?,'quinn','e','americanfootball_ncaaf','Home U','Away U',?,'h2h','Home U',-120,1)",
                    (iso(NOW - timedelta(days=1)), iso(NOW - timedelta(hours=20))))
        self.db.run("INSERT INTO bets(pick_id,kind,agent,stake_cents,price,placed_at) VALUES('p','real','quinn',100,-120,?)", (iso(NOW - timedelta(days=1)),))
        self.assertEqual(self.codes(), ["noslate", "credits", "stuck:p"])

    def test_pushes_each_problem_once_a_day(self):
        self._slate("error", error="boom")
        lab = Lab(self.s, self.db, brain=Brain(self.s))
        self.assertEqual(health.check(lab, NOW), ["slate"])
        self.assertEqual(health.check(lab, NOW + timedelta(minutes=5)), [])
        self.assertEqual(health.check(lab, NOW + timedelta(days=1)), ["noslate"])

    def test_slate_is_never_starved_by_the_rescan_reserve(self):
        """A thin daily budget used to leave the 8 AM slate nothing after holding credits for the rescans."""
        self.db.run("INSERT INTO credits(at,local_day,what,cost,remaining,used) VALUES(?,?,?,?,?,?)", (iso(NOW), "2026-10-01", "x", 0, 150, 350))
        calls = []

        class Espn:
            def has_games(self, sport, start, end):
                return sport == "americanfootball_nfl"

        odds = OddsClient(self.s, self.db, transport=lambda url: (calls.append(url) or [], {"x-requests-last": "3", "x-requests-remaining": "147"}))
        lab = Lab(self.s, self.db, odds=odds, brain=Brain(self.s), espn=Espn())
        self.assertLess(odds.cap(), 9)                                    # 150 credits over 21 days: about 7 a day
        lab.db.put("active_sports", {"at": iso(), "keys": list(self.s.sports)})
        _, fetched, notes = lab.fetch_prices("slate", datetime(2026, 10, 11, 13, tzinfo=timezone.utc))
        self.assertEqual(fetched, ["americanfootball_nfl"], notes)


if __name__ == "__main__":
    unittest.main()
