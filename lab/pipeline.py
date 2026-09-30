"""One run of the firm: prices -> research -> tipsters -> risk board -> CEO -> paper bets -> alerts."""

from __future__ import annotations

import hashlib
import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import board, grading, ledger, notify
from . import oddsmath as om
from .agents import Brain, describe, matchup, pick_real, rank_score
from .db import DB, iso, local_day, parse, utcnow
from .research import Research
from . import roster
from .roster import CFB, NBA, NFL
from .sources.espn import Espn
from .sources.http import SourceError
from .sources.odds import PROP_MARKETS, SPORT_INFO, BudgetError, OddsClient, build_candidates, build_prop_candidates, short
from .strategies import options_for

log = logging.getLogger("lab.pipeline")

PICK_COLUMNS = [
    "id", "run_id", "local_day", "created_at", "agent", "event_id", "sport", "home", "away", "commence", "market", "selection",
    "point", "player", "price", "fair_prob", "edge", "min_price", "books", "estimated", "stake_units", "confidence", "reasoning",
    "signal", "board_status", "board_notes", "group_id", "ceo_rank", "real_pick", "paper_only", "late", "legs", "book", "prices",
]


def floor_price(c: dict) -> int | None:
    """Worst price still worth taking: about 1 point of edge below now, never below normal juice, never better than now."""
    if not c.get("fair_prob"):
        return None
    floor = om.min_price(c["fair_prob"], max(c["edge"] - 0.01, -0.03))
    return c["price"] if om.dec(floor) > om.dec(c["price"]) else floor


def dedupe_open(db: DB) -> int:
    """Clean up repeat picks of the same open bet.

    Same tipster, same bet: the later copy is removed, with its paper bet, unless you placed or passed on it.
    Different tipsters, same bet: the later one is merged into the earlier one's group.
    """
    rows = db.all("SELECT * FROM picks WHERE result IS NULL AND market<>'parlay' ORDER BY created_at, id")
    real = {r["pick_id"] for r in db.all("SELECT DISTINCT pick_id FROM bets WHERE kind='real'")}
    first_by_agent: dict[tuple, dict] = {}
    first_any: dict[tuple, dict] = {}
    removed = 0
    with db.tx() as c:
        for r in rows:
            key = (r["event_id"], r["market"], r["selection"], r["point"], r["player"])
            mine = first_by_agent.get((r["agent"],) + key)
            if mine is not None:
                if r["decision"] is None and r["id"] not in real:
                    c.execute("DELETE FROM bets WHERE pick_id=? AND kind='paper'", (r["id"],))
                    c.execute("DELETE FROM picks WHERE id=?", (r["id"],))
                    removed += 1
                continue
            first_by_agent[(r["agent"],) + key] = r
            lead = first_any.get(key)
            if lead is None:
                first_any[key] = r
            elif not r["group_id"] and lead["id"] != r["id"] and r["id"] not in real:
                c.execute("UPDATE picks SET group_id=? WHERE id=?", (lead["group_id"] or lead["id"], r["id"]))
    return removed


class Lab:
    def __init__(self, settings, db: DB | None = None, odds: OddsClient | None = None, research: Research | None = None,
                 brain: Brain | None = None, espn: Espn | None = None):
        self.s = settings
        self.db = db or DB(settings.db_path)
        self.espn = espn or Espn()
        self.odds = odds or OddsClient(settings, self.db)
        self.research = research or Research(self.db, espn=self.espn)
        self.brain = brain or Brain(settings)

    # ------------------------------------------------------------------ helpers

    def today(self, now: datetime | None = None) -> str:
        return local_day(self.s.timezone, now)

    def picks_today(self, day: str) -> list[dict]:
        rows = self.db.all("SELECT * FROM picks WHERE local_day=? ORDER BY created_at", (day,))
        names = roster.lookup(self.db)
        for r in rows:
            r["board_notes"] = json.loads(r["board_notes"] or "[]")
            r["agent_name"] = names.get(r["agent"], {}).get("name", r["agent"])
        return rows

    def open_earlier(self, day: str, now: datetime) -> list[dict]:
        rows = self.db.all("SELECT * FROM picks WHERE local_day<>? AND result IS NULL AND commence>? AND board_status<>'vetoed'", (day, iso(now)))
        names = roster.lookup(self.db)
        for r in rows:
            r["board_notes"] = json.loads(r["board_notes"] or "[]")
            r["agent_name"] = names.get(r["agent"], {}).get("name", r["agent"])
        return rows

    def _store_prices(self, cands: list[dict], seen_at: str) -> None:
        with self.db.tx() as c:
            events = {x["event_id"]: x for x in cands}
            for e in events.values():
                c.execute("INSERT INTO events(id,sport,home,away,commence) VALUES(?,?,?,?,?) "
                          "ON CONFLICT(id) DO UPDATE SET commence=excluded.commence", (e["event_id"], e["sport"], e["home"], e["away"], e["commence"]))
            c.executemany("INSERT INTO lines(event_id,market,selection,point,bov_price,fair_prob,books,seen_at) VALUES(?,?,?,?,?,?,?,?)",
                          [(x["event_id"], x["market"], x["selection"], x["point"], x["price"], x["fair_prob"], x["books"], seen_at) for x in cands if not x.get("player")])

    def fetch_prices(self, kind: str, now: datetime) -> tuple[list[dict], list[str], list[str]]:
        """Returns (candidates, sports fetched, notes). Stops politely when the credit budget runs out."""
        cands, fetched, notes = [], [], []
        active = self._active_sports()
        for sport in self.s.sports:
            if sport not in SPORT_INFO:
                notes.append(f"{sport}: not supported yet")
                continue
            if active is not None and sport not in active:
                notes.append(f"{short(sport)}: out of season")
                continue
            if kind != "slate" and not self._has_games_soon(sport, now, hours=18):
                continue
            try:
                events = self.odds.odds(sport)
            except BudgetError as e:
                notes.append(str(e))
                break
            except SourceError as e:
                notes.append(f"{short(sport)}: odds unavailable ({e})")
                continue
            got = build_candidates(events, sport, self.s.min_books, now, self.s.my_books)
            self._store_prices(got, iso(now))
            cands.extend(got)
            fetched.append(sport)
        return cands, fetched, notes

    def _active_sports(self) -> set[str] | None:
        """The /sports endpoint is free. Cached for 6 hours."""
        cached = self.db.get("active_sports")
        if cached and parse(cached["at"]) > utcnow() - timedelta(hours=6):
            return set(cached["keys"])
        if not self.s.odds_api_key:
            return None
        try:
            from urllib.parse import urlencode
            from .sources.http import get_json
            data, _ = get_json("https://api.the-odds-api.com/v4/sports?" + urlencode({"apiKey": self.s.odds_api_key}))
            keys = [x["key"] for x in data if x.get("active")]
            self.db.put("active_sports", {"at": iso(), "keys": keys})
            return set(keys)
        except SourceError:
            return None

    def _has_games_soon(self, sport: str, now: datetime, hours: int) -> bool:
        return bool(self.db.one("SELECT 1 FROM events WHERE sport=? AND commence>? AND commence<?", (sport, iso(now), iso(now + timedelta(hours=hours)))))

    def moves(self, cands: list[dict]) -> dict:
        """Change in moneyline fair probability since the first time we saw each game."""
        out = {}
        for c in cands:
            if c["market"] != "h2h" or c["fair_prob"] is None:
                continue
            first = self.db.one("SELECT fair_prob, seen_at FROM lines WHERE event_id=? AND market='h2h' AND selection=? AND fair_prob IS NOT NULL ORDER BY seen_at LIMIT 1",
                                (c["event_id"], c["selection"]))
            if first and parse(first["seen_at"]) < utcnow() - timedelta(hours=6):
                out[(c["event_id"], c["selection"])] = c["fair_prob"] - first["fair_prob"]
        return out

    def _props(self, cands: list[dict], now: datetime) -> list[dict]:
        if not self.s.props_enabled:
            return []
        nfl = sorted({(c["commence"], c["event_id"]) for c in cands if c["sport"] == NFL and parse(c["commence"]) < now + timedelta(hours=36)})
        if not nfl:
            return []
        try:
            event = self.odds.event_props(NFL, nfl[0][1], PROP_MARKETS)
        except (BudgetError, SourceError):
            return []
        return build_prop_candidates(event, NFL, self.s.min_books)

    def _record(self, agent_id: str, stats: dict) -> str:
        """Season line, plus the tipster's recent graded picks so they can learn from their own results."""
        line = ledger.record_line(stats.get(agent_id) or {"graded": 0})
        if not self.s.agent_memory:
            return line
        rows = self.db.all("SELECT * FROM picks WHERE agent=? AND result IS NOT NULL ORDER BY graded_at DESC LIMIT 8", (agent_id,))
        if not rows:
            return line
        recent = []
        for r in reversed(rows):
            clv = f", CLV {r['clv'] * 100:+.1f}%" if r["clv"] is not None else ""
            recent.append(f"- {r['local_day']}: {describe(r)} ({matchup(r)}), {(r['edge'] or 0) * 100:+.1f}% vs fair at pick -> {r['result'].upper()}{clv}")
        return (line + ".\nYour most recent graded picks (learn from them: CLV says whether you beat the closing price, "
                "which matters more than any single win or loss):\n" + "\n".join(recent))

    def _make_pick(self, agent: dict, choice: dict, run_id: int, day: str, now: datetime, late: bool) -> dict:
        c = choice["cand"]
        return {
            "id": hashlib.sha1(f"{day}|{agent['id']}|{c['id']}".encode()).hexdigest()[:14], "run_id": run_id, "local_day": day,
            "created_at": iso(now), "agent": agent["id"], "agent_name": agent["name"], "event_id": c["event_id"], "sport": c["sport"],
            "home": c["home"], "away": c["away"], "commence": c["commence"], "market": c["market"], "selection": c["selection"],
            "point": c["point"], "player": c.get("player"), "price": c["price"], "fair_prob": c["fair_prob"], "edge": c["edge"],
            "min_price": floor_price(c), "books": c["books"], "estimated": bool(c.get("estimated")), "dispersion": c.get("dispersion"),
            "quote_at": c.get("quote_at"), "stake_units": float(choice["stake_units"]), "confidence": choice["confidence"],
            "reasoning": choice["reasoning"], "signal": choice["signal"], "late": int(late), "legs": None,
            "book": c.get("book", "bovada"), "prices": c.get("prices"),
        }

    def _insert(self, picks: list[dict]) -> None:
        with self.db.tx() as c:
            for p in picks:
                row = {k: p.get(k) for k in PICK_COLUMNS}
                row["board_notes"] = json.dumps(p.get("board_notes", []))
                row["legs"] = json.dumps(p["legs"]) if p.get("legs") else None
                row["prices"] = json.dumps(p["prices"]) if p.get("prices") else None
                row["estimated"] = int(bool(p.get("estimated")))
                row["paper_only"] = int(bool(p.get("paper_only")))
                row["real_pick"] = int(bool(p.get("real_pick")))
                c.execute(f"INSERT OR IGNORE INTO picks({','.join(PICK_COLUMNS)}) VALUES({','.join('?' * len(PICK_COLUMNS))})",
                          [row[k] for k in PICK_COLUMNS])

    # ------------------------------------------------------------------ runs

    def run(self, kind: str = "slate", tag: str | None = None, now: datetime | None = None) -> dict:
        now = now or utcnow()
        day = self.today(now)
        run_kind = f"{kind}-{tag}" if tag else kind
        run_id = self.db.run("INSERT INTO runs(kind,local_day,started_at) VALUES(?,?,?)", (run_kind, day, iso(now)))
        detail: dict = {"notes": []}
        try:
            detail.update(self._run(kind, run_id, day, now))
            status = "ok" if detail.get("fetched") else "skipped"
        except Exception as e:  # keep the firm alive; the error is visible in the UI
            log.exception("run failed")
            detail["error"] = f"{type(e).__name__}: {e}"
            detail["trace"] = traceback.format_exc()[-2000:]
            status = "error"
        detail["llm"] = dict(self.brain.usage)
        self.db.run("UPDATE runs SET finished_at=?, status=?, detail=? WHERE id=?", (iso(), status, json.dumps(detail), run_id))
        return {"id": run_id, "status": status, **detail}

    def _run(self, kind: str, run_id: int, day: str, now: datetime) -> dict:
        late = kind != "slate"
        cands, fetched, notes = self.fetch_prices(kind, now)
        if not cands:
            return {"fetched": fetched, "notes": notes, "picks": 0}
        events = {c["event_id"]: {"sport": c["sport"], "home": c["home"], "away": c["away"], "commence": c["commence"]} for c in cands}
        ctx = self.research.build(events)
        deep = [e for e, cx in ctx.items() if cx["sport"] in (NFL, NBA)]
        cfb = [e for _, e in sorted({(c["commence"], c["event_id"]) for c in cands if c["sport"] == CFB})]
        self.research.deepen(ctx, deep + cfb[:45])
        with self.db.tx() as c:
            for ev_id, cx in ctx.items():
                c.execute("UPDATE events SET context=?, espn_id=COALESCE(?, espn_id) WHERE id=?", (json.dumps(cx), cx.get("espn_id"), ev_id))
        props = self._props(cands, now) if kind == "slate" else []
        moves = self.moves(cands)
        dedupe_open(self.db)
        existing = self.picks_today(day)
        earlier = self.open_earlier(day, now)  # picked on a previous day, game not played yet
        stats = ledger.agent_stats(self.db, self.s)
        jobs = []
        team = roster.active(self.db)
        for agent in team:
            if agent.get("strategy") == "lydon":
                continue
            mine = [p for p in existing if p["agent"] == agent["id"]]
            room = agent["max_picks"] - len(mine)
            if room <= 0:
                continue
            taken = {p["event_id"] for p in mine} | {p["event_id"] for p in earlier if p["agent"] == agent["id"]}
            opts = options_for(agent, cands, ctx, taken=taken, moves=moves, props=props, day=day)
            if opts:
                jobs.append((agent, room, opts))

        def think(job):
            agent, room, opts = job
            return job, self.brain.decide(agent, opts, ctx, self._record(agent["id"], stats))

        new: list[dict] = []
        with ThreadPoolExecutor(4) as pool:
            for (agent, room, _), (chosen, note) in pool.map(think, jobs):
                for ch in chosen[:room]:
                    new.append(self._make_pick(agent, ch, run_id, day, now, late))
                if note and not chosen:
                    self.db.feed(note, agent["id"])
        wallets = ledger.wallet_balances(self.db, "real", self.s)
        board.review(new, existing + earlier, stats, wallets, self.s, now)
        parlay = self._lydon(existing, new, ctx, stats, day, now)
        if parlay:
            parlay.update(run_id=run_id, created_at=iso(now), late=int(late))
            new.append(parlay)
        memo_ids = self._ceo(kind, run_id, day, existing, new, stats, now)
        self._insert(new)
        for pid, rank in memo_ids.items():
            self.db.run("UPDATE picks SET real_pick=1, ceo_rank=? WHERE id=?", (rank, pid))
        paper = ledger.wallet_balances(self.db, "paper", self.s)
        placed = sum(ledger.paper_bet(self.db, p, self.s, paper) for p in new if p["board_status"] != "vetoed")
        for p in new:
            if p["board_status"] != "vetoed":
                self.db.feed(f"{describe(p)} · {matchup(p)}", p["agent"])
        self._alert(kind, new, memo_ids)
        return {"fetched": fetched, "notes": notes, "candidates": len(cands), "picks": len(new), "paper_bets": placed,
                "real_recommended": len(memo_ids), "credits_today": self.odds.credits_today()}

    def _lydon(self, existing, new, ctx, stats, day, now) -> dict | None:
        """One two-leg parlay a day, tried at every run until he finds one he likes."""
        lydon = next((t for t in roster.active(self.db) if t.get("strategy") == "lydon"), None)
        if not lydon or any(p["agent"] == lydon["id"] for p in existing):
            return None
        opts = [{"cand": p, "signal": p["signal"], "score": p["edge"]} for p in board.parlay_options(existing + new, lydon, day, now)]
        if not opts:
            return None
        chosen, note = self.brain.decide(lydon, opts, ctx, self._record(lydon["id"], stats))
        if not chosen:
            if note:
                self.db.feed(note, lydon["id"])
            return None
        ch = chosen[0]
        p = board.finalize_parlay(dict(ch["cand"]))
        p.update(local_day=day, confidence=ch["confidence"], reasoning=ch["reasoning"], min_price=floor_price(p))
        return p

    def _ceo(self, kind, run_id, day, existing, new, stats, now) -> dict[str, int]:
        """Returns {pick_id: rank} for picks recommended for real money."""
        allp = existing + new
        leaders = [p for p in allp if not p.get("group_id")]
        cleared = [p for p in leaders if p["board_status"] == "cleared" and not p.get("paper_only")]
        flagged = [p for p in leaders if p["board_status"] == "flagged"]
        vetoed = [p for p in leaders if p["board_status"] == "vetoed"]
        already = sum(1 for p in existing if p.get("real_pick"))
        room = max(0, self.s.max_real_per_day - already)
        if kind == "slate" and not self.db.one("SELECT 1 FROM memos WHERE local_day=?", (day,)):
            standings = "; ".join(f"{a['name']} {ledger.record_line(stats[a['id']])}" for a in roster.active(self.db) if stats[a["id"]]["graded"]) or "no graded bets yet"
            text, ids = self.brain.memo(cleared, flagged, vetoed, standings, room)
            self.db.run("INSERT OR REPLACE INTO memos(local_day,run_id,text,created_at) VALUES(?,?,?,?)", (day, run_id, text, iso(now)))
            return {pid: i + 1 for i, pid in enumerate(ids)}
        # rescans: late picks earn real money only when they're clearly strong
        fresh = [p for p in new if p in cleared and (p.get("edge") or -1) >= 0.0 and not p.get("estimated")]
        ids = pick_real(sorted(fresh, key=rank_score, reverse=True), room)
        return {pid: already + i + 1 for i, pid in enumerate(ids)}

    def _at(self, p: dict) -> str:
        """ " at LowVig" when you shop more than one book."""
        from .sources.odds import book_name
        return f" at {book_name(p.get('book'))}" if len(self.s.my_books) > 1 and p.get("book") else ""

    def _alert(self, kind: str, new: list[dict], real: dict) -> None:
        real_picks = [p for p in new if p["id"] in real]
        if kind == "slate":
            memo = self.db.one("SELECT text FROM memos ORDER BY created_at DESC LIMIT 1")
            body = f"{len(real_picks)} for real money, {len(new)} picks total.\n" + "\n".join(
                f"• {describe(p)}{self._at(p)} ({p['agent_name']}), floor {om.fmt(p['min_price'])}" for p in real_picks[:6])
            notify.push(self.s, "Morning slate is ready", body + ("\n\n" + memo["text"][:300] if memo else ""))
        elif real_picks:
            body = "\n".join(f"• {describe(p)}{self._at(p)} ({p['agent_name']}, {matchup(p)}), don't take worse than {om.fmt(p['min_price'])}" for p in real_picks)
            notify.push(self.s, "Late pick: price is live now", body, priority="high")

    # ------------------------------------------------------------------ closing lines and grading

    def capture_closing(self, now: datetime | None = None) -> list[str]:
        """Right before kickoff, grab one more price read for sports with open picks. Powers CLV."""
        if not self.s.closing_lines:
            return []
        now = now or utcnow()
        soon = self.db.all("SELECT DISTINCT sport, event_id, commence FROM picks WHERE result IS NULL AND market!='parlay' AND player IS NULL "
                           "AND commence>? AND commence<=?", (iso(now), iso(now + timedelta(minutes=35))))
        need = set()
        for r in soon:
            got = self.db.one("SELECT 1 FROM lines WHERE event_id=? AND seen_at>=?", (r["event_id"], iso(parse(r["commence"]) - timedelta(minutes=45))))
            if not got:
                need.add(r["sport"])
        done = []
        for sport in sorted(need):
            try:
                events = self.odds.odds(sport)
            except (BudgetError, SourceError) as e:
                log.info("closing skipped for %s: %s", sport, e)
                continue
            self._store_prices(build_candidates(events, sport, self.s.min_books, now, self.s.my_books), iso(now))
            done.append(sport)
        return done

    def grade(self, now: datetime | None = None) -> int:
        from . import alerts
        was_open = alerts.open_real_picks(self.db)
        n = grading.grade_pending(self.db, self.espn, now)
        if was_open:
            alerts.graded(self.s, self.db, was_open)
        if n:
            self.db.feed(f"Graded {n} finished bet{'s' if n != 1 else ''}.")
        return n

    def ran(self, kind: str, day: str) -> bool:
        """A run counts once it finished, or while it is plausibly still running (a crashed run frees up after an hour)."""
        return bool(self.db.one("SELECT 1 FROM runs WHERE kind=? AND local_day=? AND (status IN ('ok','skipped') OR "
                                "(status='running' AND started_at>?))", (kind, day, iso(utcnow() - timedelta(hours=1)))))

    def tick(self, now: datetime | None = None) -> list[str]:
        """Called every 15 minutes by cron. Idempotent: each job runs at most once per day."""
        now = now or utcnow()
        lock = self.db.get("tick_lock")
        if lock and parse(lock) > now - timedelta(minutes=25):
            return ["locked"]
        self.db.put("tick_lock", iso(now))
        did = []
        try:
            if dedupe_open(self.db):
                did.append("dedupe")
            day = self.today(now)
            hour = now.astimezone(ZoneInfo(self.s.timezone)).hour
            if hour >= self.s.slate_hour and not self.ran("slate", day):
                self.run("slate", now=now)
                did.append("slate")
            elif self.ran("slate", day):
                for h in self.s.rescan_hours:
                    if hour >= h and not self.ran(f"rescan-{h}", day):
                        self.run("rescan", tag=str(h), now=now)
                        did.append(f"rescan-{h}")
                        break
            if self.capture_closing(now):
                did.append("closing")
            last = self.db.get("last_grade")
            if not last or parse(last) < now - timedelta(minutes=20):
                self.db.put("last_grade", iso(now))
                if self.grade(now):
                    did.append("grade")
            from . import meeting
            if meeting.due(self.db, self.s, now):
                self.grade(now)  # settle the weekend before judging it
                if meeting.hold(self, now):
                    did.append("meeting")
            if hour >= self.s.recap_hour and self.db.get("last_recap") != day:
                from . import alerts
                self.grade(now)
                self.db.put("last_recap", day)
                if alerts.recap(self.s, self.db, day):
                    did.append("recap")
            if hour >= self.s.backup_hour and self.db.get("last_backup") != day:
                from .backup import backup
                backup(self.s.db_path, day)
                self.db.put("last_backup", day)
                did.append("backup")
        finally:
            self.db.put("tick_lock", None)
        return did
