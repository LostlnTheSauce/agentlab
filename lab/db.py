"""SQLite storage. One file, safe for a cron job and a web process to share."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, local_day TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, status TEXT NOT NULL DEFAULT 'running', detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS credits (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, local_day TEXT NOT NULL, what TEXT NOT NULL,
  cost INTEGER NOT NULL, remaining INTEGER, used INTEGER
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, sport TEXT NOT NULL, home TEXT NOT NULL, away TEXT NOT NULL, commence TEXT NOT NULL,
  espn_id TEXT, status TEXT NOT NULL DEFAULT 'scheduled', home_score REAL, away_score REAL, final_at TEXT,
  context TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS lines (
  id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, market TEXT NOT NULL, selection TEXT NOT NULL, point REAL,
  bov_price INTEGER, fair_prob REAL, books INTEGER NOT NULL DEFAULT 0, seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lines_key ON lines(event_id, market, selection, seen_at);
CREATE TABLE IF NOT EXISTS picks (
  id TEXT PRIMARY KEY, run_id INTEGER, local_day TEXT NOT NULL, created_at TEXT NOT NULL, agent TEXT NOT NULL,
  event_id TEXT NOT NULL, sport TEXT NOT NULL, home TEXT NOT NULL, away TEXT NOT NULL, commence TEXT NOT NULL,
  market TEXT NOT NULL, selection TEXT NOT NULL, point REAL, player TEXT,
  price INTEGER NOT NULL, fair_prob REAL, edge REAL, min_price INTEGER, books INTEGER, estimated INTEGER NOT NULL DEFAULT 0,
  stake_units REAL NOT NULL, confidence INTEGER NOT NULL DEFAULT 3, reasoning TEXT NOT NULL DEFAULT '', signal TEXT NOT NULL DEFAULT '',
  board_status TEXT NOT NULL DEFAULT 'pending', board_notes TEXT NOT NULL DEFAULT '[]', group_id TEXT,
  ceo_rank INTEGER, real_pick INTEGER NOT NULL DEFAULT 0, paper_only INTEGER NOT NULL DEFAULT 0, late INTEGER NOT NULL DEFAULT 0,
  decision TEXT, decided_at TEXT, legs TEXT,
  result TEXT, graded_at TEXT, actual REAL, close_price INTEGER, close_fair REAL, clv REAL
);
CREATE INDEX IF NOT EXISTS picks_day ON picks(local_day);
CREATE INDEX IF NOT EXISTS picks_agent ON picks(agent);
CREATE TABLE IF NOT EXISTS bets (
  id INTEGER PRIMARY KEY, pick_id TEXT NOT NULL REFERENCES picks(id), kind TEXT NOT NULL CHECK(kind IN ('paper','real')),
  agent TEXT NOT NULL, stake_cents INTEGER NOT NULL CHECK(stake_cents > 0), price INTEGER NOT NULL,
  placed_at TEXT NOT NULL, result TEXT, profit_cents INTEGER, settled_at TEXT
);
CREATE INDEX IF NOT EXISTS bets_agent ON bets(agent, kind);
CREATE TABLE IF NOT EXISTS memos (local_day TEXT PRIMARY KEY, run_id INTEGER, text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feed (id INTEGER PRIMARY KEY, at TEXT NOT NULL, agent TEXT, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS logins (id INTEGER PRIMARY KEY, at TEXT NOT NULL, ok INTEGER NOT NULL, ip TEXT);
CREATE TABLE IF NOT EXISTS tipsters (
  id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','jailed')),
  seat INTEGER NOT NULL, hired_at TEXT, fired_at TEXT, fired_note TEXT, replaces TEXT, replaced_by TEXT
);
CREATE TABLE IF NOT EXISTS watercooler (
  id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, lines TEXT NOT NULL, UNIQUE(day, kind)
);
CREATE TABLE IF NOT EXISTS meetings (
  id INTEGER PRIMARY KEY, week TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, report TEXT NOT NULL,
  mvp TEXT, fired TEXT, hired TEXT, detail TEXT NOT NULL DEFAULT '{}'
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def local_day(tz: str, dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(ZoneInfo(tz)).date().isoformat()


class DB:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.executescript(SCHEMA)
            for table, col in (("picks", "book TEXT"), ("picks", "prices TEXT"), ("picks", "links TEXT"), ("bets", "book TEXT"),
                               ("picks", "line_move REAL")):
                have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                if col.split()[0] not in have:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
            # line_move: how far the market's fair price moved toward the pick between the pick and the close.
            # Fill it in for picks graded before the column existed.
            db.execute("UPDATE picks SET line_move = close_fair / fair_prob - 1 WHERE line_move IS NULL AND close_fair IS NOT NULL "
                       "AND fair_prob > 0 AND estimated = 0 AND market != 'parlay'")

    @contextmanager
    def tx(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def all(self, sql: str, args=()) -> list[dict]:
        with self.tx() as db:
            return [dict(r) for r in db.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        with self.tx() as db:
            row = db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def run(self, sql: str, args=()) -> int:
        with self.tx() as db:
            cur = db.execute(sql, args)
            return cur.lastrowid

    def get(self, key: str, default=None):
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value) -> None:
        self.run("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def feed(self, text: str, agent: str | None = None) -> None:
        self.run("INSERT INTO feed(at,agent,text) VALUES(?,?,?)", (iso(), agent, text))
