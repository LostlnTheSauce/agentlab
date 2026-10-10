import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lab.config import Settings
from lab.db import DB, iso
from lab.sources.espn import Espn
from lab.sources.http import SourceError
from lab.sources.odds import BudgetError, OddsClient, daily_cap

NOW = datetime(2026, 10, 10, 13, tzinfo=timezone.utc)  # a Saturday, 22 days left in the month


def settings(tmp):
    return Settings(data_dir=Path(tmp), db_name="t.sqlite3", anthropic_api_key=None, odds_api_key="k")


class Budget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = settings(self.tmp.name)
        self.db = DB(self.s.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _left(self, remaining):
        self.db.run("INSERT INTO credits(at,local_day,what,cost,remaining,used) VALUES(?,?,?,?,?,?)", (iso(NOW), "2026-10-01", "x", 0, remaining, 0))

    def test_daily_cap_follows_the_monthly_allowance(self):
        self.assertEqual(daily_cap(self.s, self.db, NOW), 16)   # nothing known yet: the configured cap
        self._left(440)
        self.assertEqual(daily_cap(self.s, self.db, NOW), 20)   # quiet weekdays left room: 440 / 22 days
        self._left(2000)
        self.assertEqual(daily_cap(self.s, self.db, NOW), 32)   # never more than double
        self._left(40)
        self.assertEqual(daily_cap(self.s, self.db, NOW), 4)    # nearly out: stretch it, but keep one sport alive

    def test_reserve_holds_credits_back_for_the_rescans(self):
        calls = []
        client = OddsClient(self.s, self.db, transport=lambda url: (calls.append(url) or [], {"x-requests-last": "3"}))
        for _ in range(3):
            client.odds("americanfootball_nfl", reserve=6)      # 9 of 16 spent, 6 held back
        with self.assertRaises(BudgetError):
            client.odds("americanfootball_nfl", reserve=6)      # a 4th would eat into the reserve
        client.odds("americanfootball_nfl")                     # the rescan itself may use it
        self.assertEqual(len(calls), 4)

    def test_has_games(self):
        board = {"events": [{"date": iso(NOW + timedelta(hours=5))}]}
        self.assertTrue(Espn(transport=lambda url: (board, {})).has_games("baseball_mlb", NOW, NOW + timedelta(hours=20)))
        self.assertFalse(Espn(transport=lambda url: ({"events": []}, {})).has_games("baseball_mlb", NOW, NOW + timedelta(hours=20)))
        self.assertFalse(Espn(transport=lambda url: (board, {})).has_games("baseball_mlb", NOW, NOW + timedelta(hours=2)))

        def down(url):
            raise SourceError("down")
        self.assertIsNone(Espn(transport=down).has_games("baseball_mlb", NOW, NOW + timedelta(hours=20)))  # can't say: fetch anyway
        self.assertIsNone(Espn().has_games("curling", NOW, NOW + timedelta(hours=20)))


if __name__ == "__main__":
    unittest.main()
