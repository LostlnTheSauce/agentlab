"""ESPN public site API: schedules, records, venues, injuries, win predictor, final scores, box scores."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from ..db import parse
from .http import SourceError, get_json

BASE = "https://site.api.espn.com/apis/site/v2/sports/"
PATHS = {
    "americanfootball_nfl": "football/nfl",
    "americanfootball_ncaaf": "football/college-football",
    "baseball_mlb": "baseball/mlb",
    "basketball_nba": "basketball/nba",
    "icehockey_nhl": "hockey/nhl",
    "soccer_epl": "soccer/eng.1",
    "soccer_usa_mls": "soccer/usa.1",
    "soccer_uefa_champs_league": "soccer/uefa.champions",
}
STOP = {"fc", "afc", "cf", "sc", "the", "de", "club"}


def norm(name: str) -> set[str]:
    s = name.lower().replace("&", " and ").replace("st.", "state")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return {t for t in s.split() if t not in STOP}


def similarity(a: str, b: str) -> float:
    A, B = norm(a), norm(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def match_game(home: str, away: str, commence: str, games: list[dict]) -> dict | None:
    when = parse(commence)
    found = []
    for g in games:
        try:
            gap = abs((parse(g["date"]) - when).total_seconds())
        except (KeyError, ValueError):
            continue
        if gap > 30 * 3600:
            continue
        straight = similarity(home, g["home"]) + similarity(away, g["away"])
        swapped = similarity(home, g["away"]) + similarity(away, g["home"])
        found.append((max(straight, swapped), gap, g))
    if not found:
        return None
    top = max(s for s, _, _ in found)
    if top < 1.3:
        return None
    # the same two teams on back-to-back days (a playoff series): take the game closest to the bet's start time
    return min(((gap, g) for s, gap, g in found if s >= top - 0.15), key=lambda x: x[0])[1]


def _game(e: dict) -> dict | None:
    try:
        c = e["competitions"][0]
        sides = {x["homeAway"]: x for x in c["competitors"]}
        h, a = sides["home"], sides["away"]
    except (KeyError, IndexError):
        return None
    st = c.get("status", {}).get("type", {})
    venue = c.get("venue") or {}
    addr = venue.get("address") or {}

    def rec(x):
        recs = x.get("records") or []
        return recs[0].get("summary") if recs else None

    def score(x):
        try:
            return float(x.get("score")) if x.get("score") not in (None, "") else None
        except ValueError:
            return None

    names: list[str] = []
    for b in c.get("broadcasts") or []:
        for n in b.get("names") or []:
            if n and n not in names:
                names.append(n)
    link = next((l.get("href") for l in e.get("links") or [] if "summary" in (l.get("rel") or [])), None)
    if not (isinstance(link, str) and link.startswith("https://www.espn.com/")):
        link = None
    return {
        "broadcast": ", ".join(names) or (c.get("broadcast") or None), "link": link,
        "espn_id": e["id"], "date": e.get("date") or c.get("date"), "name": e.get("name"),
        "home": h["team"].get("displayName", ""), "away": a["team"].get("displayName", ""),
        "home_abbr": h["team"].get("abbreviation"), "away_abbr": a["team"].get("abbreviation"),
        "home_record": rec(h), "away_record": rec(a),
        "home_score": score(h), "away_score": score(a),
        "state": st.get("state"), "completed": bool(st.get("completed")), "detail": st.get("shortDetail") or st.get("description"),
        "venue": venue.get("fullName"), "city": addr.get("city"), "region": addr.get("state"), "country": addr.get("country"),
        "indoor": venue.get("indoor"), "neutral": c.get("neutralSite", False),
    }


class Espn:
    def __init__(self, transport=get_json):
        self.t = transport
        self._boards: dict[tuple, list[dict]] = {}
        self._summaries: dict[tuple, dict] = {}

    def scoreboard(self, sport: str, day: date) -> list[dict]:
        key = (sport, day)
        if key in self._boards:
            return self._boards[key]
        path = PATHS.get(sport)
        if not path:
            return []
        extra = "&groups=80&limit=400" if sport == "americanfootball_ncaaf" else "&limit=200"
        try:
            data, _ = self.t(f"{BASE}{path}/scoreboard?dates={day.strftime('%Y%m%d')}{extra}")
            games = [g for g in (_game(e) for e in data.get("events", [])) if g]
        except SourceError:
            games = []
        self._boards[key] = games
        return games

    def games_around(self, sport: str, when: datetime, days_before: int = 1, days_after: int = 1) -> list[dict]:
        d0 = when.date()
        out = []
        for i in range(-days_before, days_after + 1):
            out.extend(self.scoreboard(sport, d0 + timedelta(days=i)))
        return out

    def find(self, sport: str, home: str, away: str, commence: str) -> dict | None:
        return match_game(home, away, commence, self.games_around(sport, parse(commence)))

    def summary(self, sport: str, espn_id: str) -> dict:
        key = (sport, espn_id)
        if key in self._summaries:
            return self._summaries[key]
        path = PATHS.get(sport)
        try:
            data, _ = self.t(f"{BASE}{path}/summary?event={espn_id}")
        except SourceError:
            data = {}
        self._summaries[key] = data
        return data

    def injuries(self, sport: str, espn_id: str) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for team in self.summary(sport, espn_id).get("injuries", []) or []:
            name = team.get("team", {}).get("displayName", "?")
            rows = []
            for inj in team.get("injuries", []) or []:
                ath = inj.get("athlete", {})
                rows.append({
                    "name": ath.get("displayName", "?"),
                    "pos": (ath.get("position") or {}).get("abbreviation", ""),
                    "status": inj.get("status", ""),
                })
            out[name] = rows
        return out

    def predictor(self, sport: str, espn_id: str) -> dict | None:
        p = self.summary(sport, espn_id).get("predictor") or {}
        try:
            return {"home": float(p["homeTeam"]["gameProjection"]) / 100, "away": float(p["awayTeam"]["gameProjection"]) / 100}
        except (KeyError, TypeError, ValueError):
            return None

    def player_stat(self, sport: str, espn_id: str, player: str, market: str) -> float | None:
        """Box-score stat for a finished game, for grading player props."""
        wanted = {
            "player_receptions": ("receiving", "REC"), "player_reception_yds": ("receiving", "YDS"),
            "player_pass_yds": ("passing", "YDS"), "player_rush_yds": ("rushing", "YDS"),
        }.get(market)
        if not wanted:
            return None
        box = self.summary(sport, espn_id).get("boxscore", {})
        target = norm(player)
        for team in box.get("players", []) or []:
            for cat in team.get("statistics", []) or []:
                if cat.get("name") != wanted[0]:
                    continue
                labels = cat.get("labels", [])
                if wanted[1] not in labels:
                    continue
                idx = labels.index(wanted[1])
                for ath in cat.get("athletes", []) or []:
                    if norm(ath.get("athlete", {}).get("displayName", "")) == target:
                        try:
                            return float(ath["stats"][idx])
                        except (ValueError, IndexError, KeyError):
                            return None
        return None
