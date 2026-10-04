"""Command line: python -m lab <command>"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys

from .config import get_settings
from .db import DB


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lab", description="Agent Lab: 20 AI tipsters, one risk board, you place the bets.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="run the web app locally")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--db", help="database file name inside the data folder, e.g. demo.sqlite3")
    rn = sub.add_parser("run", help="run a slate or rescan now (spends odds credits)")
    rn.add_argument("kind", choices=["slate", "rescan"])
    rn.add_argument("--manual", action="store_true", help="tag as a manual run so the daily schedule still happens")
    rn.add_argument("--fresh", action="store_true", help="clear today's untouched picks, memo and chats, then redo the slate")
    rn.add_argument("--extra-credits", type=int, default=0, help="allow this many odds credits past today's cap")
    sub.add_parser("tick", help="what cron runs every 5 minutes")
    sub.add_parser("grade", help="grade finished games (free)")
    sub.add_parser("closing", help="capture closing lines for games about to start")
    sub.add_parser("set-password", help="set the web login password")
    sub.add_parser("status", help="show credits, last runs and open picks")
    sub.add_parser("backup", help="copy the database into data/backups now")
    sub.add_parser("dedupe", help="clean up repeat picks of the same open bet")
    mt = sub.add_parser("meeting", help="hold this week's board meeting now")
    mt.add_argument("--force", action="store_true", help="redo this week's meeting (can fire again)")
    dm = sub.add_parser("demo", help="fill a separate demo database with a synthetic day (no network, no credits)")
    dm.add_argument("--days", type=int, default=12)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    s = get_settings()
    if args.cmd == "demo":
        from .demo import seed
        s.db_name = "demo.sqlite3"
        print(json.dumps(seed(s, days=args.days), indent=2))
        print(f"Demo data written to {s.db_path}. Serve it with: set LAB_DB=demo.sqlite3 then python -m lab serve")
        return 0
    if getattr(args, "db", None):
        s.db_name = args.db
    db = DB(s.db_path)
    if args.cmd == "serve":
        from .web import create_app
        create_app(s, db).run(host=args.host, port=args.port, debug=False)
    elif args.cmd == "set-password":
        from .web import set_password
        pw = getpass.getpass("New password: ")
        if pw != getpass.getpass("Again: "):
            print("Passwords didn't match.")
            return 1
        set_password(db, pw)
        print("Password saved.")
    elif args.cmd == "status":
        rows = db.all("SELECT kind, started_at, status FROM runs ORDER BY id DESC LIMIT 5")
        credit = db.one("SELECT remaining, used FROM credits WHERE remaining IS NOT NULL ORDER BY id DESC LIMIT 1")
        print(json.dumps({"runs": rows, "odds_api": credit, "open_picks": db.one("SELECT COUNT(*) n FROM picks WHERE result IS NULL")["n"]}, indent=2))
    else:
        from .pipeline import Lab
        lab = Lab(s, db)
        if args.cmd == "run":
            s.daily_credit_cap += max(0, min(args.extra_credits, 30))
            if args.fresh and args.kind == "slate":
                print(f"cleared {lab.clear_today()}")
            # a manual slate counts as the day's slate unless one already ran
            first_slate = args.kind == "slate" and (args.fresh or not lab.ran("slate", lab.today()))
            out = lab.run(args.kind, tag=None if first_slate or not args.manual else "manual")
            out.pop("trace", None)
            print(json.dumps(out, indent=2, default=str))
            return 0 if out["status"] != "error" else 1
        if args.cmd == "tick":
            print(lab.tick())
        elif args.cmd == "grade":
            print(f"graded {lab.grade()}")
        elif args.cmd == "closing":
            print(lab.capture_closing())
        elif args.cmd == "dedupe":
            from .pipeline import dedupe_open
            print(f"removed {dedupe_open(db)} duplicate picks")
        elif args.cmd == "backup":
            from .backup import backup
            print(backup(s.db_path, lab.today()))
        elif args.cmd == "meeting":
            from . import meeting
            m = meeting.hold(lab, force=args.force)
            print(m["report"] if m else "This week's meeting already happened. Use --force to redo it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
