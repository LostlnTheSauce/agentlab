"""MLB Stats API: probable pitchers, pitcher season lines, bullpen workload."""

from __future__ import annotations

from datetime import date, timedelta

from .http import SourceError, get_json

BASE = "https://statsapi.mlb.com/api/v1"


def ip_to_float(ip) -> float:
    """'5.2' innings means 5 and 2/3."""
    try:
        whole, _, outs = str(ip).partition(".")
        return int(whole) + (int(outs) / 3 if outs else 0)
    except ValueError:
        return 0.0


class Mlb:
    def __init__(self, transport=get_json):
        self.t = transport
        self._cache: dict[str, object] = {}

    def _get(self, url: str):
        if url not in self._cache:
            try:
                self._cache[url], _ = self.t(url)
            except SourceError:
                self._cache[url] = {}
        return self._cache[url]

    def games(self, day: date) -> list[dict]:
        data = self._get(f"{BASE}/schedule?sportId=1&date={day.isoformat()}&hydrate=probablePitcher")
        out = []
        for d in data.get("dates", []) or []:
            for g in d.get("games", []):
                t = g.get("teams", {})
                row = {"pk": g.get("gamePk"), "date": g.get("gameDate"), "venue": (g.get("venue") or {}).get("name")}
                for side in ("home", "away"):
                    s = t.get(side, {})
                    row[side] = (s.get("team") or {}).get("name", "")
                    row[f"{side}_id"] = (s.get("team") or {}).get("id")
                    pp = s.get("probablePitcher") or {}
                    row[f"{side}_pitcher"] = {"id": pp.get("id"), "name": pp.get("fullName")} if pp else None
                out.append(row)
        return out

    def pitcher(self, pid: int, season: int) -> dict | None:
        data = self._get(f"{BASE}/people/{pid}/stats?stats=season&group=pitching&season={season}")
        try:
            st = data["stats"][0]["splits"][0]["stat"]
        except (KeyError, IndexError, TypeError):
            return None
        ip = ip_to_float(st.get("inningsPitched", 0))
        if ip <= 0:
            return None
        fip = (13 * st.get("homeRuns", 0) + 3 * (st.get("baseOnBalls", 0) + st.get("hitByPitch", 0)) - 2 * st.get("strikeOuts", 0)) / ip + 3.15
        return {
            "era": float(st.get("era") or 0), "whip": float(st.get("whip") or 0), "ip": round(ip, 1),
            "k9": float(st.get("strikeoutsPer9Inn") or 0), "bb9": float(st.get("walksPer9Inn") or 0),
            "fip": round(fip, 2), "gs": st.get("gamesStarted", 0),
        }

    def bullpen_ip(self, team_id: int, day: date, days: int = 3) -> tuple[float, int]:
        """Relief innings thrown over the `days` days before `day`."""
        start, end = day - timedelta(days=days), day - timedelta(days=1)
        sched = self._get(f"{BASE}/schedule?sportId=1&teamId={team_id}&startDate={start}&endDate={end}")
        total, games = 0.0, 0
        for d in sched.get("dates", []) or []:
            for g in d.get("games", []):
                if (g.get("status") or {}).get("abstractGameState") != "Final":
                    continue
                box = self._get(f"{BASE}/game/{g['gamePk']}/boxscore")
                for side in ("home", "away"):
                    team = (box.get("teams") or {}).get(side) or {}
                    if (team.get("team") or {}).get("id") != team_id:
                        continue
                    pitchers = team.get("pitchers") or []
                    for pid in pitchers[1:]:  # first listed pitcher is the starter
                        stats = ((team.get("players") or {}).get(f"ID{pid}") or {}).get("stats", {}).get("pitching", {})
                        total += ip_to_float(stats.get("inningsPitched", 0))
                    games += 1
        return round(total, 1), games
