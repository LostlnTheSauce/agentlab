"""The Odds API client with a local daily credit cap, plus Bovada-vs-market candidate building."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from urllib.parse import urlencode

from .. import oddsmath as om
from ..db import DB, iso, local_day, parse, utcnow
from .http import SourceError, get_json

BASE = "https://api.the-odds-api.com/v4/sports"

# length_h: earliest a game could be over; grading starts asking ESPN then (ESPN decides when it's final)
SPORT_INFO = {
    "americanfootball_nfl": {"short": "NFL", "markets": ["h2h", "spreads", "totals"], "window_h": 150, "length_h": 2.75},
    "americanfootball_ncaaf": {"short": "CFB", "markets": ["h2h", "spreads", "totals"], "window_h": 150, "length_h": 2.75},
    "baseball_mlb": {"short": "MLB", "markets": ["h2h", "spreads", "totals"], "window_h": 30, "length_h": 2},
    "basketball_nba": {"short": "NBA", "markets": ["h2h", "spreads", "totals"], "window_h": 30, "length_h": 2},
    "icehockey_nhl": {"short": "NHL", "markets": ["h2h", "spreads", "totals"], "window_h": 30, "length_h": 2},
    "soccer_epl": {"short": "EPL", "markets": ["h2h", "totals"], "window_h": 54, "length_h": 1.75},
    "soccer_usa_mls": {"short": "MLS", "markets": ["h2h", "totals"], "window_h": 54, "length_h": 1.75},
    "soccer_uefa_champs_league": {"short": "UCL", "markets": ["h2h", "totals"], "window_h": 54, "length_h": 1.75},
}
PROP_MARKETS = ["player_receptions", "player_reception_yds", "player_pass_yds", "player_rush_yds"]


class BudgetError(Exception):
    pass


def short(sport: str) -> str:
    return SPORT_INFO.get(sport, {}).get("short", sport.split("_")[-1].upper())


def is_soccer(sport: str) -> bool:
    return sport.startswith("soccer_")


class OddsClient:
    def __init__(self, settings, db: DB, transport=get_json):
        self.s, self.db, self.transport = settings, db, transport

    def credits_today(self) -> int:
        row = self.db.one("SELECT COALESCE(SUM(cost),0) AS c FROM credits WHERE local_day=?", (local_day(self.s.timezone),))
        return int(row["c"])

    def remaining(self) -> dict:
        row = self.db.one("SELECT remaining, used, at FROM credits WHERE remaining IS NOT NULL ORDER BY id DESC LIMIT 1")
        return row or {}

    def _get(self, path: str, params: dict, cost: int, what: str, extra_cap: int = 0):
        if not self.s.odds_api_key:
            raise SourceError("ODDS_API_KEY is not set")
        spent = self.credits_today()
        if spent + cost > self.s.daily_credit_cap + extra_cap:
            raise BudgetError(f"Daily odds budget reached ({spent}/{self.s.daily_credit_cap} credits)")
        last = self.remaining()
        if last.get("remaining") is not None and last["remaining"] < cost:
            raise BudgetError("The Odds API reports no credits left this month")
        url = f"{BASE}/{path}?" + urlencode({**params, "apiKey": self.s.odds_api_key})
        data, headers = self.transport(url)
        actual = headers.get("x-requests-last")
        rem, used = headers.get("x-requests-remaining"), headers.get("x-requests-used")
        self.db.run(
            "INSERT INTO credits(at,local_day,what,cost,remaining,used) VALUES(?,?,?,?,?,?)",
            (iso(), local_day(self.s.timezone), what, int(actual) if actual and actual.isdigit() else cost,
             int(float(rem)) if rem else None, int(float(used)) if used else None),
        )
        return data

    def books_param(self) -> str:
        """Your books first, then comparison books, capped at 10 (10 books cost the same as one region)."""
        keys = list(dict.fromkeys(list(getattr(self.s, "my_books", ["bovada"])) + list(self.s.bookmakers)))
        return ",".join(keys[:10])

    def odds(self, sport: str, markets: list[str] | None = None) -> list[dict]:
        markets = markets or SPORT_INFO[sport]["markets"]
        info = SPORT_INFO.get(sport, {"window_h": 48})
        until = utcnow() + timedelta(hours=info["window_h"])
        params = {
            "bookmakers": self.books_param(), "markets": ",".join(markets),
            "oddsFormat": "american", "dateFormat": "iso", "includeLinks": "true",
            "commenceTimeTo": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return self._get(f"{sport}/odds", params, len(markets), f"odds {short(sport)}")

    def one_game(self, sport: str, event_id: str, market: str) -> list[dict]:
        """One market for one game: 1 credit. Allowed a few credits past the daily cap, since you asked for it."""
        params = {"bookmakers": self.books_param(), "markets": market, "oddsFormat": "american",
                  "dateFormat": "iso", "eventIds": event_id, "includeLinks": "true"}
        return self._get(f"{sport}/odds", params, 1, f"check {short(sport)}", extra_cap=8)

    def event_props(self, sport: str, event_id: str, markets: list[str]) -> dict:
        params = {"bookmakers": self.books_param(), "markets": ",".join(markets), "oddsFormat": "american", "dateFormat": "iso"}
        return self._get(f"{sport}/events/{event_id}/odds", params, len(markets), f"props {short(sport)}")


def cand_id(event_id: str, market: str, selection: str, point, player: str | None = None) -> str:
    raw = f"{event_id}|{market}|{selection}|{point}|{player or ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:14]


def _market(book: dict, key: str) -> dict | None:
    found = [m for m in book.get("markets", []) if m.get("key") == key]
    return found[0] if len(found) == 1 else None


BOOK_NAMES = {"bovada": "Bovada", "lowvig": "LowVig", "betonlineag": "BetOnline", "mybookieag": "MyBookie", "pinnacle": "Pinnacle",
              "draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "williamhill_us": "Caesars", "betrivers": "BetRivers"}


LINK_HOSTS = {"bovada": ("bovada.lv",), "lowvig": ("lowvig.ag",), "betonlineag": ("betonline.ag",), "mybookieag": ("mybookie.ag",)}


def safe_link(book: str, url) -> str | None:
    """Only https links on that sportsbook's own domain."""
    from urllib.parse import urlparse
    if not isinstance(url, str):
        return None
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme == "https" and any(host == h or host.endswith("." + h) for h in LINK_HOSTS.get(book, ())):
        return url
    return None


def book_name(key: str | None) -> str:
    return BOOK_NAMES.get(key or "", (key or "?").title())


def _no_vig_map(book: dict, key: str) -> tuple[dict, dict] | None:
    """(name, point) -> no-vig probability for one book's complete market, plus name -> points offered."""
    m = _market(book, key)
    outs = (m or {}).get("outcomes") or []
    if len(outs) < 2:
        return None
    try:
        probs = om.no_vig([o["price"] for o in outs])
        if not 0.97 <= sum(om.implied(o["price"]) for o in outs) <= 1.3:
            return None
    except (KeyError, ValueError, ZeroDivisionError):
        return None
    return ({(o["name"], o.get("point")): p for o, p in zip(outs, probs)},
            {o["name"]: float(o["point"]) for o in outs if o.get("point") is not None})


def build_candidates(events: list[dict], sport: str, min_books: int = 3, now: datetime | None = None,
                     my_books: tuple | list = ("bovada",)) -> list[dict]:
    """Every bet offered at one of your books, at its best price among them, priced against the
    no-vig consensus of the other books at the same line (the book you'd bet at is left out of its own fair price)."""
    now = now or utcnow()
    my_books = [b for b in my_books] or ["bovada"]
    out = []
    for ev in events:
        try:
            commence = parse(ev["commence_time"])
        except (KeyError, ValueError):
            continue
        if commence <= now:
            continue
        books = {b["key"]: b for b in ev.get("bookmakers", []) if b.get("key")}
        mine = [b for b in my_books if b in books]
        if not mine:
            continue
        for key in ("h2h", "spreads", "totals"):
            maps = {bk: _no_vig_map(book, key) for bk, book in books.items()}
            offers: dict[tuple, dict] = defaultdict(dict)
            quote_at: dict[str, str] = {}
            for bk in mine:
                m = _market(books[bk], key)
                if not m or len(m.get("outcomes") or []) < 2:
                    continue
                quote_at[bk] = m.get("last_update") or books[bk].get("last_update")
                for o in m["outcomes"]:
                    if o.get("name") is not None and o.get("price") is not None:
                        offers[(o["name"], o.get("point"))][bk] = int(o["price"])
            for (name, point), prices in offers.items():
                best = max(prices, key=lambda b: (om.dec(prices[b]), -mine.index(b)))
                price = prices[best]
                r = [(bk, mp[0][(name, point)]) for bk, mp in maps.items() if bk != best and mp and (name, point) in mp[0]]
                pts = [mp[1][name] for bk, mp in maps.items() if bk != best and mp and name in mp[1]]
                fair = disp = None
                if len(r) >= min_books:
                    probs = [p for _, p in r]
                    med = median(probs)
                    pin = next((p for k, p in r if k == "pinnacle"), None)
                    fair = (pin + med) / 2 if pin is not None else med
                    disp = max(probs) - min(probs)
                out.append({
                    "id": cand_id(ev["id"], key, name, point),
                    "event_id": ev["id"], "sport": sport, "home": ev["home_team"], "away": ev["away_team"],
                    "commence": ev["commence_time"], "market": key, "selection": name,
                    "point": float(point) if point is not None else None, "player": None, "price": price,
                    "fair_prob": fair, "edge": om.edge(fair, price) if fair else None,
                    "min_price": om.min_price(fair) if fair else None, "books": len(r), "dispersion": disp,
                    "consensus_point": median(pts) if pts else None,
                    "quote_at": quote_at.get(best), "estimated": False, "book": best, "prices": prices,
                    "links": {bk: safe_link(bk, books[bk].get("link")) for bk in prices if safe_link(bk, books[bk].get("link"))},
                })
    return out


def build_prop_candidates(event: dict, sport: str, min_books: int = 3) -> list[dict]:
    """Player over/under props. Outcomes are keyed by player description + line."""
    books = {b["key"]: b for b in event.get("bookmakers", [])}
    bov = books.get("bovada")
    out = []
    if not bov:
        return out
    for bm in bov.get("markets", []):
        key = bm["key"]
        pairs: dict[tuple, dict] = defaultdict(dict)
        for o in bm.get("outcomes", []):
            pairs[(o.get("description"), o.get("point"))][o["name"]] = o["price"]
        refs: dict[tuple, list[float]] = defaultdict(list)
        for bkey, book in books.items():
            if bkey == "bovada":
                continue
            m = _market(book, key)
            if not m:
                continue
            by: dict[tuple, dict] = defaultdict(dict)
            for o in m.get("outcomes", []):
                by[(o.get("description"), o.get("point"))][o["name"]] = o["price"]
            for k, sides in by.items():
                if set(sides) == {"Over", "Under"}:
                    p_over, p_under = om.no_vig([sides["Over"], sides["Under"]])
                    refs[k + ("Over",)].append(p_over)
                    refs[k + ("Under",)].append(p_under)
        for (player, point), sides in pairs.items():
            for side, price in sides.items():
                r = refs.get((player, point, side), [])
                if len(r) < min_books or not player:
                    continue
                fair = median(r)
                out.append({
                    "id": cand_id(event["id"], key, side, point, player), "event_id": event["id"], "sport": sport,
                    "home": event["home_team"], "away": event["away_team"], "commence": event["commence_time"],
                    "market": key, "selection": side, "point": float(point), "player": player, "price": int(price),
                    "fair_prob": fair, "edge": om.edge(fair, price), "min_price": om.min_price(fair), "books": len(r),
                    "dispersion": max(r) - min(r), "consensus_point": None, "quote_at": bm.get("last_update"), "estimated": False,
                })
    return out
