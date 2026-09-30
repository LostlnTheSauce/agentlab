"""Water cooler talk: short break-room conversations between the tipsters, written after the slate,
after the night's results, and after a board meeting. Tap the cooler in the office to read them."""

from __future__ import annotations

import json
import random

from . import ledger, roster
from .agents import describe, matchup
from .db import DB, iso, utcnow
from .roster import STAFF
from .sources.odds import book_name

KEEP = 30


def _schema(ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {"lines": {"type": "array", "items": {
            "type": "object",
            "properties": {"speaker": {"type": "string", "enum": ids}, "text": {"type": "string"}},
            "required": ["speaker", "text"], "additionalProperties": False}}},
        "required": ["lines"], "additionalProperties": False,
    }


def _context(lab, day: str, kind: str) -> str:
    db, s = lab.db, lab.s
    names = roster.lookup(db)
    stats = ledger.agent_stats(db, s)
    parts = []
    picks = db.all("SELECT * FROM picks WHERE local_day=? ORDER BY real_pick DESC, edge DESC LIMIT 14", (day,))
    if picks:
        parts.append("Today's picks:\n" + "\n".join(
            f"- {names.get(p['agent'], {}).get('name', p['agent'])}: {describe(p)}"
            f"{' at ' + book_name(p['book']) if p.get('book') and len(s.my_books) > 1 else ''} ({matchup(p)}), "
            f"{(p['edge'] or 0) * 100:+.1f}% vs fair, board {p['board_status']}{', REAL MONEY' if p['real_pick'] else ''}"
            f"{', result ' + p['result'].upper() if p['result'] else ''}. Their case: {(p['reasoning'] or '')[:140]}"
            for p in picks))
    graded = db.all("SELECT * FROM picks WHERE result IS NOT NULL ORDER BY graded_at DESC LIMIT 12")
    if graded:
        parts.append("Most recent results:\n" + "\n".join(
            f"- {names.get(p['agent'], {}).get('name', p['agent'])}: {describe(p)} -> {p['result'].upper()}"
            + (f", beat the closing line by {p['clv'] * 100:+.1f}%" if p['clv'] is not None else "") for p in graded))
    standing = [f"{t['name']} {ledger.record_line(stats[t['id']])}" for t in roster.active(db) if stats[t['id']]['graded']]
    if standing:
        parts.append("Season standings (paper): " + "; ".join(standing))
    jailed = [t for t in names.values() if t["status"] == "jailed"]
    if jailed:
        parts.append("In tipster jail (fired): " + "; ".join(f"{t['name']} ({t.get('fired_note') or ''})" for t in jailed))
    m = db.one("SELECT report, detail FROM meetings ORDER BY week DESC LIMIT 1")
    if m and kind == "meeting":
        parts.append("Tonight's board meeting minutes: " + m["report"][:900])
    return "\n\n".join(parts) or "A quiet day. No picks yet."


def _fallback(lab, day: str, kind: str) -> list[dict]:
    db = lab.db
    names = roster.lookup(db)
    rng = random.Random(f"{day}{kind}")
    lines = []
    for p in db.all("SELECT * FROM picks WHERE local_day=? AND agent IN (SELECT id FROM tipsters) ORDER BY edge DESC LIMIT 3", (day,)):
        t = names.get(p["agent"])
        if t:
            lines.append({"speaker": p["agent"], "text": f"I'm on {describe(p)}. {t.get('quote', '')}"})
    if lines:
        lines.append({"speaker": "mara", "text": rng.choice(["Prices first, stories second.", "Show me the fair price and I'll show you a smile.",
                                                            "Somebody check that quote before it goes stale."])})
    return lines


def chat(lab, kind: str, day: str | None = None) -> dict | None:
    """Write one conversation and store it. kind: morning | evening | meeting."""
    db, s, brain = lab.db, lab.s, lab.brain
    day = day or lab.today()
    if db.one("SELECT 1 FROM watercooler WHERE day=? AND kind=?", (day, kind)):
        return None
    team = roster.active(db)
    ids = [t["id"] for t in team] + ["mara", "barb", "carl", "commish", "guard"]
    lines = None
    if s.llm_enabled:
        cast = "\n".join(f"- {t['id']} = {t['name']}, {t['role']}: {t['voice']}" for t in team)
        cast += "\n" + "\n".join(f"- {k} = {v['name']}, {v['role']}: {v['voice']}" for k, v in STAFF.items())
        moment = {"morning": "It's the morning, right after the slate went out.",
                  "evening": "It's the end of the night, after today's games were graded.",
                  "meeting": "The board meeting just ended."}[kind]
        system = ("You write the break-room chatter at Agent Lab, a sports betting firm staffed by AI tipsters. "
                  f"{moment} Write a short, funny conversation at the water cooler: 6 to 10 lines, 3 to 6 different speakers, each "
                  "line under 25 words, in each character's voice. Reference real picks, results and prices from the notes (never invent "
                  "games, prices or results). Gentle trash talk, rivalries, and inside jokes are welcome; keep it PG and good-natured. "
                  "Rivals can disagree about a pick. Fired tipsters are in jail and can't be at the cooler, but others may mention them. "
                  "No betting advice aimed at the reader, no hashtags, no emojis.\n\nCast (id = name, role: voice):\n" + cast)
        got = brain._ask(s.tipster_model, "low", system, _context(lab, day, kind), _schema(ids))
        if got:
            lines = [{"speaker": l["speaker"], "text": l["text"].strip()[:220]} for l in got.get("lines", [])
                     if l.get("speaker") in ids and l.get("text", "").strip()][:12]
    if not lines:
        lines = _fallback(lab, day, kind)
    if not lines:
        return None
    db.run("INSERT OR IGNORE INTO watercooler(day,kind,created_at,lines) VALUES(?,?,?,?)", (day, kind, iso(utcnow()), json.dumps(lines)))
    db.run("DELETE FROM watercooler WHERE id NOT IN (SELECT id FROM watercooler ORDER BY id DESC LIMIT ?)", (KEEP,))
    return {"day": day, "kind": kind, "lines": lines}
