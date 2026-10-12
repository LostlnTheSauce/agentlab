"""Things that need the owner's attention: the slate didn't produce prices, a bet is stuck, credits are nearly gone,
or the background job has stopped. Shown as a banner in the app; the ones the job itself can notice are also
pushed to the phone, once a day each."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import notify
from .agents import describe
from .db import DB, iso, local_day, parse, utcnow

STUCK_HOURS = 8       # a real bet still open this long after kickoff needs a result entered by hand
LOW_CREDITS = 30


def slate_today(db: DB, settings, now: datetime | None = None) -> dict | None:
    """Today's morning slate in plain words, so an empty Slate tab can say why."""
    now = now or utcnow()
    r = db.one("SELECT status, detail, started_at FROM runs WHERE kind='slate' AND local_day=? ORDER BY id DESC LIMIT 1",
               (local_day(settings.timezone, now),))
    if not r:
        return None
    d = json.loads(r["detail"] or "{}")
    notes = [n for n in d.get("notes") or [] if n]
    fetched = d.get("fetched") or []
    if r["status"] == "running":
        why = "The desks are working on today's slate right now."
    elif r["status"] == "error":
        why = f"Today's slate crashed before it finished ({d.get('error') or 'unknown error'})."
    elif not fetched:
        why = "Today's slate couldn't get any prices. " + ("; ".join(notes) if notes else "No sport had games or prices available.")
    elif not d.get("picks"):
        why = (f"Prices came in for {len(fetched)} sport{'s' if len(fetched) != 1 else ''} ({d.get('candidates', 0)} possible bets), "
               "but no tipster found one that cleared their bar." + (" Notes: " + "; ".join(notes) if notes else ""))
    else:
        why = ""
    return {"status": r["status"], "at": r["started_at"], "picks": d.get("picks", 0), "fetched": fetched, "notes": notes, "why": why}


def issues(db: DB, settings, now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    hour = now.astimezone(ZoneInfo(settings.timezone)).hour
    out = []
    last = db.get("last_tick")
    if last and parse(last) < now - timedelta(minutes=45):
        mins = int((now - parse(last)).total_seconds() // 60)
        out.append({"code": "cron", "push": False, "text": f"The background job hasn't run for {mins // 60}h {mins % 60}m. Picks, grading and "
                    "alerts are paused until it runs again. Check Cron Jobs in cPanel."})
    s = slate_today(db, settings, now)
    if s is None:
        if hour >= settings.slate_hour + 1 and last:
            out.append({"code": "noslate", "push": True, "text": "Today's morning slate hasn't run yet."})
    elif s["status"] == "error" or (s["status"] != "running" and not s["fetched"]):
        out.append({"code": "slate", "push": True, "text": s["why"]})
    left = db.one("SELECT remaining FROM credits WHERE remaining IS NOT NULL ORDER BY id DESC LIMIT 1")
    if left and left["remaining"] < LOW_CREDITS:
        out.append({"code": "credits", "push": True, "text": f"Only {left['remaining']} odds credits are left this month, so price checks "
                    "are being rationed until the allowance resets."})
    seen = set()
    for p in db.all("SELECT p.* FROM bets b JOIN picks p ON p.id=b.pick_id WHERE b.kind='real' AND b.result IS NULL AND p.market!='parlay' "
                    "AND p.commence<? ORDER BY p.commence", (iso(now - timedelta(hours=STUCK_HOURS)),)):
        key = (p["event_id"], p["market"], p["selection"], p["point"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"code": f"stuck:{p['id']}", "push": True, "text": f"{describe(p)} ({p['away']} at {p['home']}) still hasn't settled. ESPN may "
                    "not have matched the game. Enter the result under MY BETS, Behind the scenes, Needs a result."})
    return out


def check(lab, now: datetime | None = None) -> list[str]:
    """Called on every tick: push anything new to the phone, at most once a day per problem."""
    now = now or utcnow()
    day = local_day(lab.s.timezone, now)
    sent = []
    for i in issues(lab.db, lab.s, now):
        key = f"health_sent:{i['code']}"
        if not i["push"] or lab.db.get(key) == day:
            continue
        lab.db.put(key, day)
        notify.push(lab.s, "Agent Lab needs attention", i["text"], priority="high")
        sent.append(i["code"])
    return sent
