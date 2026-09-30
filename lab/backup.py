"""Nightly copies of the database. Uses SQLite's online backup, so it's safe while the app is running."""

from __future__ import annotations

import sqlite3
from pathlib import Path

KEEP = 14


def backup(db_path: Path, day: str, keep: int = KEEP) -> Path:
    folder = db_path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{db_path.stem}-{day}.sqlite3"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    old = sorted(folder.glob(f"{db_path.stem}-*.sqlite3"))
    for f in old[:-keep]:
        f.unlink(missing_ok=True)
    return target
