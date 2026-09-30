"""Settings come from environment variables, optionally loaded from a .env file in the project root."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SPORTS = "americanfootball_nfl,americanfootball_ncaaf,baseball_mlb,basketball_nba,soccer_epl"
# Up to 10 bookmakers cost the same as one region. Pinnacle is the sharpest reference.
DEFAULT_BOOKS = "bovada,pinnacle,draftkings,fanduel,betmgm,williamhill_us,betonlineag,betrivers,lowvig,mybookieag"


def load_env(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in ('"', "'"):
            value = value[1:].split(value[0], 1)[0]  # quoted: keep everything inside the quotes
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()  # drop trailing "   # comment"
        os.environ.setdefault(key.strip(), value)


def _str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


def _list(name: str, default: str) -> list[str]:
    return [s.strip() for s in (_str(name, default) or "").split(",") if s.strip()]


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return (_str(name, "1" if default else "0") or "").lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path = ROOT / "data"
    db_name: str = "lab.sqlite3"
    odds_api_key: str | None = None
    anthropic_api_key: str | None = None
    sports: list[str] = field(default_factory=lambda: DEFAULT_SPORTS.split(","))
    my_books: list[str] = field(default_factory=lambda: ["bovada"])
    bookmakers: list[str] = field(default_factory=lambda: DEFAULT_BOOKS.split(","))
    timezone: str = "America/Chicago"
    unit_dollars: float = 1.0
    wallet_dollars: float = 10.0
    max_units: int = 2
    max_real_per_day: int = 6
    min_books: int = 3
    slate_hour: int = 8
    rescan_hours: list[int] = field(default_factory=lambda: [12, 16])
    daily_credit_cap: int = 16
    closing_lines: bool = True
    props_enabled: bool = False
    agent_memory: bool = True
    meeting_hour: int = 21
    backup_hour: int = 3
    recap_hour: int = 22
    tipster_model: str = "claude-opus-5-5"
    tipster_effort: str = "low"
    ceo_model: str = "claude-opus-5-5"
    ceo_effort: str = "medium"
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"
    public_url: str | None = None
    secret_key: str | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_name

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


def get_settings() -> Settings:
    load_env()
    data_dir = Path(_str("LAB_DATA_DIR", str(ROOT / "data")))
    return Settings(
        data_dir=data_dir,
        db_name=_str("LAB_DB", "lab.sqlite3"),
        odds_api_key=_str("ODDS_API_KEY"),
        anthropic_api_key=_str("ANTHROPIC_API_KEY"),
        sports=_list("LAB_SPORTS", DEFAULT_SPORTS),
        my_books=_list("LAB_MY_BOOKS", "bovada"),
        bookmakers=_list("LAB_BOOKMAKERS", DEFAULT_BOOKS),
        timezone=_str("LAB_TZ", "America/Chicago"),
        unit_dollars=_float("LAB_UNIT_DOLLARS", 1.0),
        wallet_dollars=_float("LAB_WALLET_DOLLARS", 10.0),
        max_units=_int("LAB_MAX_UNITS", 2),
        max_real_per_day=_int("LAB_MAX_REAL_PER_DAY", 6),
        min_books=_int("LAB_MIN_BOOKS", 3),
        slate_hour=_int("LAB_SLATE_HOUR", 8),
        rescan_hours=[int(h) for h in _list("LAB_RESCAN_HOURS", "12,16") if h.isdigit()],
        daily_credit_cap=_int("LAB_DAILY_CREDIT_CAP", 16),
        closing_lines=_bool("LAB_CLOSING_LINES", True),
        props_enabled=_bool("LAB_PROPS", False),
        agent_memory=_bool("LAB_AGENT_MEMORY", True),
        meeting_hour=_int("LAB_MEETING_HOUR", 21),
        backup_hour=_int("LAB_BACKUP_HOUR", 3),
        recap_hour=_int("LAB_RECAP_HOUR", 22),
        tipster_model=_str("LAB_TIPSTER_MODEL", "claude-opus-5-5"),
        tipster_effort=_str("LAB_TIPSTER_EFFORT", "low"),
        ceo_model=_str("LAB_CEO_MODEL", "claude-opus-5-5"),
        ceo_effort=_str("LAB_CEO_EFFORT", "medium"),
        ntfy_topic=_str("NTFY_TOPIC"),
        ntfy_server=_str("NTFY_SERVER", "https://ntfy.sh"),
        public_url=_str("LAB_PUBLIC_URL"),
        secret_key=_str("LAB_SECRET_KEY"),
    )
