"""Phone alerts about your real bets: a buzz when one is graded, and a short recap each night."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import notify
from .agents import describe
from .db import DB, iso, parse, utcnow
from .sources.odds import book_name


def open_real_picks(db: DB) -> set[str]:
    return {r["pick_id"] for r in db.all("SELECT DISTINCT pick_id FROM bets WHERE kind='real' AND result IS NULL")}


def _bets(db: DB, pick_ids) -> list[dict]:
    """Real bets for these picks, one row per bet you actually placed (merged tipsters' shares combined)."""
    ids = list(pick_ids)
    if not ids:
        return []
    rows = db.all("SELECT b.*, p.market, p.selection, p.point, p.player, p.home, p.away, p.agent FROM bets b JOIN picks p ON p.id=b.pick_id "
                  "WHERE b.kind='real' AND b.pick_id IN (%s)" % ",".join("?" * len(ids)), ids)
    groups: dict[tuple, dict] = {}
    for r in rows:
        k = (r["placed_at"], r["price"], r["home"], r["market"], r["selection"], r["point"])
        g = groups.setdefault(k, {**r, "stake": 0, "profit": 0})
        g["stake"] += r["stake_cents"] / 100
        g["profit"] += (r["profit_cents"] or 0) / 100
    return list(groups.values())


def _line(b: dict) -> str:
    what = describe(b).rsplit(" ", 1)[0]
    at = f" ({book_name(b.get('book'))})" if b.get("book") and b["book"] != "bovada" else ""
    if b["result"] == "win":
        return f"WON {what}{at}: +${b['profit']:.2f}"
    if b["result"] == "loss":
        return f"LOST {what}{at}: -${b['stake']:.2f}"
    return f"PUSH {what}{at}: stake back"


def graded(settings, db: DB, was_open: set[str]) -> int:
    """Called after grading with the picks that had open real bets before it ran."""
    done = [b for b in _bets(db, was_open) if b["result"]]
    if not done:
        return 0
    total = sum(b["profit"] for b in done)
    wins = sum(b["result"] == "win" for b in done)
    if len(done) == 1:
        title = {"win": "Bet won!", "loss": "Bet lost", "push": "Bet pushed", "void": "Bet voided"}.get(done[0]["result"], "Bet graded")
    else:
        title = f"{wins} of {len(done)} bets won ({'+' if total >= 0 else '-'}${abs(total):.2f})"
    season = db.one("SELECT COALESCE(SUM(profit_cents),0) p FROM bets WHERE kind='real' AND result IS NOT NULL")["p"] / 100
    body = "\n".join(_line(b) for b in done) + f"\n\nReal money all-time: {'+' if season >= 0 else '-'}${abs(season):.2f}"
    notify.push(settings, title, body, priority="high" if any(b["result"] == "win" for b in done) else "default")
    return len(done)


def recap(settings, db: DB, day: str) -> bool:
    """Nightly summary. Skipped on days when nothing happened."""
    tz = ZoneInfo(settings.timezone)

    def local(ts):
        return parse(ts).astimezone(tz).date().isoformat() if ts else None

    real_today = [b for b in _bets(db, {r["pick_id"] for r in db.all("SELECT DISTINCT pick_id FROM bets WHERE kind='real' AND settled_at IS NOT NULL")})
                  if local(b["settled_at"]) == day]
    since = iso(utcnow() - timedelta(days=2))
    paper = db.all("SELECT agent, profit_cents, settled_at FROM bets WHERE kind='paper' AND settled_at IS NOT NULL AND settled_at>?", (since,))
    paper = [r for r in paper if local(r["settled_at"]) == day]
    open_real = db.one("SELECT COUNT(DISTINCT placed_at||price) n, COALESCE(SUM(stake_cents),0) s FROM bets WHERE kind='real' AND result IS NULL")
    if not real_today and not paper:
        return False
    names = {r["id"]: r for r in db.all("SELECT id, data FROM tipsters")}
    by_agent: dict[str, float] = defaultdict(float)
    for r in paper:
        by_agent[r["agent"]] += r["profit_cents"] / (settings.unit_dollars * 100)
    lines = []
    if real_today:
        pr = sum(b["profit"] for b in real_today)
        lines.append(f"Your bets: {sum(b['result'] == 'win' for b in real_today)}-{sum(b['result'] == 'loss' for b in real_today)} today, "
                     f"{'+' if pr >= 0 else '-'}${abs(pr):.2f}")
    if by_agent:
        firm = sum(by_agent.values())
        best = max(by_agent, key=by_agent.get)
        best_name = json.loads(names[best]["data"])["name"] if best in names else best
        lines.append(f"Paper firm: {firm:+.1f}u today. Best: {best_name} ({by_agent[best]:+.1f}u)")
    if open_real["n"]:
        lines.append(f"Still riding: {open_real['n']} bet{'s' if open_real['n'] != 1 else ''}, ${open_real['s'] / 100:.2f}")
    notify.push(settings, "Tonight at the firm", "\n".join(lines))
    return True
