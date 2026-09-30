"""Per-game research context shared by every agent: records, venue, weather, injuries, pitchers, rest."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from zoneinfo import ZoneInfo

from .db import parse, utcnow
from .roster import CFB, MLB, NBA, NFL, SOCCER
from .sources.espn import Espn, similarity
from .sources.mlb import Mlb
from .sources.weather import Weather

STATE_TZ = {}
for tz, states in {
    "ET": "CT DE DC FL GA IN KY ME MD MA MI NH NJ NY NC OH PA RI SC VT VA WV",
    "CT": "AL AR IL IA KS LA MN MS MO NE ND OK SD TN TX WI",
    "MT": "AZ CO ID MT NM UT WY",
    "PT": "CA NV OR WA",
}.items():
    for s in states.split():
        STATE_TZ[s] = tz

TEAM_TZ = {}
for tz, teams in {
    "ET": "Bills Dolphins Patriots Jets Ravens Bengals Browns Steelers Colts Jaguars Giants Eagles Commanders Falcons Panthers Buccaneers Lions "
          "Celtics Nets Knicks 76ers Raptors Cavaliers Pistons Pacers Hawks Hornets Heat Magic Wizards",
    "CT": "Texans Titans Chiefs Bears Packers Vikings Saints Cowboys Bulls Bucks Rockets Grizzlies Pelicans Spurs Mavericks Thunder Timberwolves",
    "MT": "Broncos Cardinals Nuggets Jazz Suns",
    "PT": "Raiders Chargers Rams 49ers Seahawks Clippers Lakers Kings Warriors Blazers",
}.items():
    for t in teams.split():
        TEAM_TZ[t.lower()] = tz


def team_tz(name: str) -> str | None:
    words = name.lower().split()
    for w in reversed(words):
        if w in TEAM_TZ:
            return TEAM_TZ[w]
    return None


class Research:
    def __init__(self, db, espn: Espn | None = None, mlb: Mlb | None = None, weather: Weather | None = None, workers: int = 8):
        self.espn = espn or Espn()
        self.mlb = mlb or Mlb()
        self.weather = weather or Weather(db)
        self.workers = workers

    def build(self, events: dict[str, dict]) -> dict[str, dict]:
        """events: event_id -> {sport, home, away, commence}. Returns event_id -> context."""
        ctx: dict[str, dict] = {}
        for ev_id, ev in events.items():
            cx = dict(ev)
            g = self.espn.find(ev["sport"], ev["home"], ev["away"], ev["commence"])
            if g:
                cx.update(espn_id=g["espn_id"], home_record=g["home_record"], away_record=g["away_record"], venue=g["venue"],
                          city=g["city"], region=g["region"], country=g["country"], indoor=g["indoor"], neutral=g["neutral"])
                cx["venue_tz"] = STATE_TZ.get(g["region"] or "", "INTL" if g["country"] and g["country"] != "USA" else None)
            cx["home_tz"], cx["away_tz"] = team_tz(ev["home"]), team_tz(ev["away"])
            ctx[ev_id] = cx
        self._weather(ctx)
        self._rest(ctx)
        self._mlb(ctx)
        return ctx

    def deepen(self, ctx: dict[str, dict], event_ids: list[str]) -> None:
        """Injuries and ESPN's matchup predictor. One ESPN call per game, so only for games that matter."""
        todo = [e for e in event_ids if ctx.get(e, {}).get("espn_id") and "injuries" not in ctx[e]]

        def one(ev_id):
            cx = ctx[ev_id]
            return ev_id, self.espn.injuries(cx["sport"], cx["espn_id"]), self.espn.predictor(cx["sport"], cx["espn_id"])

        with ThreadPoolExecutor(self.workers) as pool:
            for ev_id, inj, pred in pool.map(one, todo):
                # ESPN team names may differ slightly from the odds feed; map onto odds names.
                mapped = {}
                for team, rows in inj.items():
                    target = max((ctx[ev_id]["home"], ctx[ev_id]["away"]), key=lambda n: similarity(n, team))
                    mapped[target] = rows
                ctx[ev_id]["injuries"] = mapped
                if pred:
                    ctx[ev_id]["predictor"] = pred

    def _weather(self, ctx):
        now = utcnow()
        todo = [cx for cx in ctx.values()
                if cx["sport"] in (NFL, CFB, MLB, *SOCCER) and cx.get("city") and cx.get("indoor") is False
                and parse(cx["commence"]) - now < timedelta(days=7)]

        def one(cx):
            ll = self.weather.coords(cx["city"], cx.get("region"), cx.get("country"))
            return cx, (self.weather.at(ll[0], ll[1], parse(cx["commence"])) if ll else None)

        with ThreadPoolExecutor(self.workers) as pool:
            for cx, w in pool.map(one, todo):
                if w:
                    cx["weather"] = w

    def _rest(self, ctx):
        for sport, back in ((NFL, 12), (NBA, 3)):
            games = [cx for cx in ctx.values() if cx["sport"] == sport]
            if not games:
                continue
            first = min(parse(cx["commence"]) for cx in games)
            history = []
            for i in range(1, back + 1):
                history.extend(g for g in self.espn.scoreboard(sport, (first - timedelta(days=i)).date()) if g["completed"])
            for cx in games:
                kick = parse(cx["commence"])
                rest = {}
                for team in (cx["home"], cx["away"]):
                    played = [parse(g["date"]) for g in history if max(similarity(team, g["home"]), similarity(team, g["away"])) >= 0.99 and parse(g["date"]) < kick]
                    if played:
                        rest[team] = (kick.date() - max(played).date()).days
                cx["rest"] = rest

    def _mlb(self, ctx):
        games = [cx for cx in ctx.values() if cx["sport"] == MLB]
        if not games:
            return
        days = sorted({parse(cx["commence"]).astimezone(ZoneInfo("America/New_York")).date() for cx in games})
        sched = [g for d in days for g in self.mlb.games(d)]
        season = parse(games[0]["commence"]).year
        jobs = []
        for cx in games:
            best = max(sched, key=lambda g: similarity(cx["home"], g["home"]) + similarity(cx["away"], g["away"]), default=None)
            if not best or similarity(cx["home"], best["home"]) + similarity(cx["away"], best["away"]) < 1.5:
                continue
            cx["mlb"] = best
            jobs.append(cx)

        def one(cx):
            g = cx["mlb"]
            pitchers, pen = {}, {}
            for side, team in (("home", cx["home"]), ("away", cx["away"])):
                p = g.get(f"{side}_pitcher")
                if p and p.get("id"):
                    line = self.mlb.pitcher(p["id"], season)
                    if line:
                        pitchers[team] = {"name": p["name"], **line}
                if g.get(f"{side}_id"):
                    pen[team] = self.mlb.bullpen_ip(g[f"{side}_id"], parse(cx["commence"]).date())[0]
            return cx, pitchers, pen

        with ThreadPoolExecutor(self.workers) as pool:
            for cx, pitchers, pen in pool.map(one, jobs):
                cx["pitchers"], cx["bullpen"] = pitchers, pen
                cx.pop("mlb", None)


def briefing(cx: dict) -> str:
    """Compact plain-text research note for one game, for agents and the UI."""
    parts = []
    if cx.get("home_record") or cx.get("away_record"):
        parts.append(f"Records: {cx['away']} {cx.get('away_record') or '?'} at {cx['home']} {cx.get('home_record') or '?'}.")
    if cx.get("venue"):
        where = ", ".join(x for x in (cx.get("city"), cx.get("region") or cx.get("country")) if x)
        tags = [t for t, on in (("neutral site", cx.get("neutral")), ("indoors", cx.get("indoor"))) if on]
        parts.append(f"Venue: {cx['venue']}{', ' + where if where else ''}{' (' + ', '.join(tags) + ')' if tags else ''}.")
    w = cx.get("weather")
    if w:
        parts.append(f"Forecast at start: {w.get('temp_f', 0):.0f}°F, wind {w.get('wind_mph', 0):.0f} mph (gusts {w.get('gust_mph', 0):.0f}), {w.get('precip_pct', 0):.0f}% rain.")
    if cx.get("rest"):
        parts.append("Days of rest: " + ", ".join(f"{t} {d}" for t, d in cx["rest"].items()) + ".")
    for team, p in (cx.get("pitchers") or {}).items():
        parts.append(f"{team} starter {p['name']}: ERA {p['era']}, FIP {p['fip']}, WHIP {p['whip']}, {p['k9']} K/9 over {p['ip']} IP.")
    if cx.get("bullpen"):
        parts.append("Bullpen innings last 3 days: " + ", ".join(f"{t} {ip:g}" for t, ip in cx["bullpen"].items()) + ".")
    for team, rows in (cx.get("injuries") or {}).items():
        notable = [f"{r['name']} ({r['pos']}, {r['status']})" for r in rows if r["status"].lower() in ("out", "doubtful", "questionable")]
        if notable:
            parts.append(f"{team} injuries: {', '.join(notable[:8])}{' +' + str(len(notable) - 8) + ' more' if len(notable) > 8 else ''}.")
    if cx.get("predictor"):
        parts.append(f"ESPN matchup predictor: {cx['home']} {cx['predictor']['home'] * 100:.0f}%, {cx['away']} {cx['predictor']['away'] * 100:.0f}%.")
    return " ".join(parts) or "No extra research available for this game."
