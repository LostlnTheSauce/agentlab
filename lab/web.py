"""Flask app: login-protected office UI plus a small JSON API. Works under cPanel's Passenger (WSGI)."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from datetime import timedelta

from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import grading, ledger
from .config import ROOT, get_settings
from .db import DB, iso, utcnow
from .state import build_state

WEB = ROOT / "web"
MAX_FAILS = 8


def _secret(settings) -> str:
    if settings.secret_key:
        return settings.secret_key
    path = settings.data_dir / "secret_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def set_password(db: DB, password: str) -> None:
    if len(password) < 10:
        raise ValueError("Use at least 10 characters")
    db.put("password_hash", generate_password_hash(password))


def create_app(settings=None, db: DB | None = None) -> Flask:
    s = settings or get_settings()
    db = db or DB(s.db_path)
    app = Flask(__name__, static_folder=None)
    app.secret_key = _secret(s)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(s.public_url and s.public_url.startswith("https")),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30), MAX_CONTENT_LENGTH=64 * 1024,
    )

    @app.after_request
    def headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def authed() -> bool:
        return session.get("ok") is True

    @app.before_request
    def gate():
        p = request.path
        if p in ("/login", "/login.css", "/login.js", "/favicon.svg", "/healthz"):
            return None
        if not authed():
            if p.startswith("/api/"):
                return jsonify(error="Sign in first"), 401
            return redirect(url_for("login_page"))
        # CSRF: state-changing calls must be same-origin JSON with our header (a cross-site form can't set it).
        if request.method == "POST" and p.startswith("/api/") and request.headers.get("X-Lab") != "1":
            return jsonify(error="Missing request header"), 400
        return None

    @app.get("/healthz")
    def health():
        return "ok"

    @app.get("/login")
    def login_page():
        return send_from_directory(WEB, "login.html")

    @app.post("/login")
    def login():
        since = iso(utcnow() - timedelta(minutes=15))
        fails = db.one("SELECT COUNT(*) n FROM logins WHERE ok=0 AND at>?", (since,))["n"]
        if fails >= MAX_FAILS:
            return redirect(url_for("login_page", e="locked"))
        stored = db.get("password_hash")
        ok = bool(stored) and check_password_hash(stored, request.form.get("password", ""))
        db.run("INSERT INTO logins(at,ok,ip) VALUES(?,?,?)", (iso(), int(ok), request.headers.get("X-Forwarded-For", request.remote_addr)))
        if not stored:
            return redirect(url_for("login_page", e="nopass"))
        if not ok:
            return redirect(url_for("login_page", e="bad"))
        session.clear()
        session["ok"] = True
        session.permanent = True
        return redirect(url_for("index"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login_page"))

    @app.get("/")
    def index():
        return send_from_directory(WEB, "index.html")

    @app.get("/<path:name>")
    def static_file(name):
        if name in ("app.js", "style.css", "office.js", "login.css", "login.js", "favicon.svg"):
            return send_from_directory(WEB, name)
        return jsonify(error="Not found"), 404

    @app.get("/api/state")
    def state():
        return jsonify(build_state(s, db))

    @app.post("/api/pick/<pick_id>")
    def pick_action(pick_id):
        body = request.get_json(silent=True) or {}
        action = body.get("action")
        p = db.one("SELECT * FROM picks WHERE id=?", (pick_id,))
        if not p:
            return jsonify(error="Unknown pick"), 404
        try:
            if action in ("take", "pass"):
                db.run("UPDATE picks SET decision=?, decided_at=? WHERE id=? AND (decision IS NULL OR decision IN ('take','pass'))",
                       ("taken" if action == "take" else "passed", iso(), pick_id))
            elif action == "undo":
                if p["decision"] == "placed":
                    ledger.unplace_real(db, pick_id)
                else:
                    db.run("UPDATE picks SET decision=NULL, decided_at=NULL WHERE id=?", (pick_id,))
            elif action == "placed":
                price = int(body.get("price", p["price"]))
                stake = float(body.get("stake", p["stake_units"] * s.unit_dollars))
                if not 0 < stake <= 500:
                    raise ValueError("Stake must be between $0 and $500")
                book = body.get("book") or p.get("book") or "bovada"
                if book not in s.my_books and book != p.get("book"):
                    raise ValueError("Unknown sportsbook")
                ledger.place_real(db, pick_id, price, stake, book)
            elif action == "grade":
                grading.manual_grade(db, pick_id, body.get("result", ""))
            else:
                return jsonify(error="Unknown action"), 400
        except (ValueError, TypeError) as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True)

    @app.post("/api/pick/<pick_id>/check")
    def pick_check(pick_id):
        from .recheck import recheck
        from .sources.http import SourceError
        from .sources.odds import BudgetError
        try:
            return jsonify(recheck(s, db, pick_id))
        except (ValueError, BudgetError, SourceError) as e:
            return jsonify(error=str(e)), 400

    @app.post("/api/alert-test")
    def alert_test():
        from . import notify
        if not s.ntfy_topic:
            return jsonify(error="Phone alerts are off: NTFY_TOPIC isn't set in .env"), 400
        ok = notify.push(s, "Agent Lab test alert", "If you can read this, phone alerts work. The morning slate, late picks, "
                                                    "results and the nightly recap will arrive here.", priority="high")
        return (jsonify(ok=True), 200) if ok else (jsonify(error="The server couldn't reach ntfy.sh"), 502)

    @app.post("/api/run")
    def run_now():
        kind = (request.get_json(silent=True) or {}).get("kind", "slate")
        if kind not in ("slate", "rescan", "grade"):
            return jsonify(error="Unknown run"), 400
        running = db.one("SELECT id FROM runs WHERE status='running' AND started_at>?", (iso(utcnow() - timedelta(minutes=30)),))
        if running and kind != "grade":
            return jsonify(error="A run is already in progress"), 409
        fresh = bool((request.get_json(silent=True) or {}).get("fresh")) and kind == "slate"
        args = [sys.executable, "-m", "lab", "grade" if kind == "grade" else "run", *([] if kind == "grade" else [kind, "--manual"])]
        if fresh:
            args += ["--fresh", "--extra-credits", "15"]
        flags = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
        subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy(), **flags)
        return jsonify(ok=True, started=kind)

    return app
