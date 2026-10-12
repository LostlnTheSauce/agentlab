import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lab.config import Settings
from lab.db import DB
from lab.web import create_app, set_password


class InstallAndBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Settings(data_dir=Path(self.tmp.name), db_name="t.sqlite3", anthropic_api_key=None, odds_api_key=None)
        self.db = DB(self.s.db_path)
        set_password(self.db, "correct horse battery")
        self.client = create_app(self.s, self.db).test_client()

    def tearDown(self):
        self.client = None
        self.tmp.cleanup()

    def test_phone_can_fetch_the_app_manifest_and_icons_signed_out(self):
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        m = json.loads(r.data)
        self.assertEqual((m["display"], m["start_url"]), ("standalone", "./"))
        for icon in m["icons"]:
            got = self.client.get("/" + icon["src"])
            self.assertEqual((got.status_code, got.data[:8]), (200, b"\x89PNG\r\n\x1a\n"))
            got.close()
        r.close()
        self.assertEqual(self.client.get("/app.js").status_code, 302)  # everything else still needs the login

    def test_backup_download_needs_login_and_leaves_the_password_out(self):
        self.assertEqual(self.client.get("/api/backup").status_code, 401)
        self.client.post("/login", data={"password": "correct horse battery"})
        self.db.feed("hello from the live database")
        r = self.client.get("/api/backup")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        copy = Path(self.tmp.name) / "copy.sqlite3"
        copy.write_bytes(r.data)
        r.close()
        con = sqlite3.connect(copy)
        try:
            self.assertEqual(con.execute("SELECT text FROM feed").fetchone()[0], "hello from the live database")
            self.assertIsNone(con.execute("SELECT 1 FROM kv WHERE key='password_hash'").fetchone())
        finally:
            con.close()
        self.assertTrue(self.db.get("password_hash"))                 # the live one is untouched
        self.assertTrue(self.db.get("last_backup_download"))


if __name__ == "__main__":
    unittest.main()
