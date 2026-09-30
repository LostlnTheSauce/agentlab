"""A synthetic sports world for demos and tests. Runs the real pipeline, with fake feeds, over past days.

Nothing here touches the network or spends credits. Outcomes are drawn from the fair probabilities,
so the demo firm loses roughly the vig over time, like a real one would without an edge.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import ledger
from . import oddsmath as om
from .agents import Brain
from .db import DB, iso, parse
from .pipeline import Lab
from .research import Research
from .roster import CFB, MLB, NFL
from .sources.espn import Espn
from .sources.mlb import Mlb
from .sources.odds import OddsClient
from .sources.weather import Weather

EPL = "soccer_epl"
TEAMS = {
    NFL: ["Buffalo Bills", "Miami Dolphins", "Kansas City Chiefs", "Denver Broncos", "Seattle Seahawks", "Los Angeles Rams", "Green Bay Packers",
          "Chicago Bears", "Dallas Cowboys", "Philadelphia Eagles", "Cincinnati Bengals", "Pittsburgh Steelers", "San Francisco 49ers",
          "Detroit Lions", "Houston Texans", "Tennessee Titans"],
    CFB: ["Ole Miss Rebels", "LSU Tigers", "Georgia Bulldogs", "Alabama Crimson Tide", "Ohio State Buckeyes", "Michigan Wolverines",
          "UTSA Roadrunners", "North Texas Mean Green", "Boise State Broncos", "Oregon Ducks", "Texas Longhorns", "Oklahoma Sooners",
          "Tulane Green Wave", "Memphis Tigers", "Indiana Hoosiers", "Iowa Hawkeyes"],
    MLB: ["New York Yankees", "Boston Red Sox", "San Diego Padres", "Chicago Cubs", "Los Angeles Dodgers", "Philadelphia Phillies",
          "Cleveland Guardians", "Detroit Tigers", "Seattle Mariners", "Houston Astros", "Atlanta Braves", "Milwaukee Brewers"],
    EPL: ["Arsenal", "Chelsea", "Liverpool", "Manchester City", "Tottenham Hotspur", "Newcastle United", "Aston Villa", "Brighton and Hove Albion"],
}
CITIES = [("Orchard Park", "NY"), ("Chicago", "IL"), ("Kansas City", "MO"), ("Denver", "CO"), ("Seattle", "WA"), ("Green Bay", "WI"),
          ("Philadelphia", "PA"), ("Pittsburgh", "PA"), ("Oxford", "MS"), ("Athens", "GA"), ("Boise", "ID"), ("San Diego", "CA")]
BOOKS = ["pinnacle", "draftkings", "fanduel", "betmgm", "betrivers"]
SCHEDULE = {NFL: {6: 6, 3: 1, 0: 1}, CFB: {5: 8}, MLB: {d: 5 for d in range(7)}, EPL: {5: 3, 6: 1}}  # weekday -> games
KICK = {NFL: 17, CFB: 18, MLB: 23, EPL: 14}  # UTC hour


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def vig_prices(probs: list[float], margin: float = 0.045) -> list[int]:
    return [om.american_from_prob(min(max(p * (1 + margin), 0.02), 0.97)) for p in probs]


class World:
    def __init__(self, start, end, seed: int = 7):
        self.rng = random.Random(seed)
        self.events: dict[str, dict] = {}
        d = start
        while d <= end:
            for sport, per_day in SCHEDULE.items():
                n = per_day.get(d.weekday(), 0)
                teams = self.rng.sample(TEAMS[sport], min(len(TEAMS[sport]), n * 2))
                for i in range(n):
                    self._event(sport, teams[2 * i], teams[2 * i + 1], datetime.combine(d, time(KICK[sport]), timezone.utc) + timedelta(hours=(i % 3) * 3))
            d += timedelta(days=1)

    def _event(self, sport, home, away, when):
        r = random.Random(f"{sport}{home}{when}")
        ev = {"id": f"demo{abs(hash((sport, home, when))) % 10**10}", "sport": sport, "home": home, "away": away, "commence": iso(when),
              "indoor": r.random() < 0.2, "city": r.choice(CITIES), "rec": (f"{r.randint(0, 5)}-{r.randint(0, 5)}", f"{r.randint(0, 5)}-{r.randint(0, 5)}")}
        if sport in (NFL, CFB):
            sd = 13.5 if sport == NFL else 16
            ev["spread"] = round(r.gauss(-2, 6 if sport == NFL else 11) * 2) / 2
            ev["total"] = round(r.gauss(44 if sport == NFL else 55, 5) * 2) / 2
            ev["p_home"] = phi(-ev["spread"] / sd)
            margin = round(r.gauss(-ev["spread"], sd))
            tot = max(abs(margin) + 3, round(r.gauss(ev["total"], 13)))
            hs = (tot + margin) // 2
            ev["score"] = (hs, tot - hs)
        elif sport == MLB:
            ev["p_home"] = r.uniform(0.38, 0.64)
            ev["total"] = r.choice([7.5, 8, 8.5, 9])
            lam = ev["total"] / 2
            while True:
                x, y = self._poisson(r, lam), self._poisson(r, lam)
                if x != y:
                    break
            hi, lo = max(x, y), min(x, y)
            ev["score"] = (hi, lo) if r.random() < ev["p_home"] else (lo, hi)
            ev["pitchers"] = {home: self._pitcher(r), away: self._pitcher(r)}
            ev["pen"] = {home: round(r.uniform(3, 14), 1), away: round(r.uniform(3, 14), 1)}
        else:
            ph, pd = r.uniform(0.28, 0.6), r.uniform(0.22, 0.3)
            ev["probs3"] = [ph, 1 - ph - pd, pd]
            ev["total"] = 2.5
            u = r.random()
            outcome = "home" if u < ph else "draw" if u < ph + pd else "away"
            while True:
                x, y = self._poisson(r, 1.3), self._poisson(r, 1.3)
                if (outcome == "draw") == (x == y):
                    break
            hi, lo = max(x, y), min(x, y)
            ev["score"] = (hi, lo) if outcome == "home" else (lo, hi) if outcome == "away" else (x, x)
        self.events[ev["id"]] = ev

    @staticmethod
    def _poisson(r, lam):
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= r.random()
            if p <= L:
                return k
            k += 1

    @staticmethod
    def _pitcher(r):
        fip = round(r.uniform(2.8, 5.2), 2)
        return {"name": r.choice(["J. Ryan", "L. Gil", "C. Sale", "T. Skubal", "D. Cease", "S. Imanaga", "B. Woo", "H. Brown"]),
                "era": round(fip + r.uniform(-0.5, 0.5), 2), "fip": fip, "whip": round(0.9 + (fip - 2.8) / 5, 2), "ip": round(r.uniform(60, 190), 1),
                "k9": round(r.uniform(7, 11.5), 1), "bb9": round(r.uniform(2, 3.8), 1), "gs": 25}

    # ------------------------------------------------------------------ odds feed

    def odds_payload(self, sport: str, now: datetime) -> list[dict]:
        out = []
        for ev in self.events.values():
            c = parse(ev["commence"])
            if ev["sport"] != sport or not now < c < now + timedelta(hours=150):
                continue
            hours_out = (c - now).total_seconds() / 3600
            books = []
            for i, key in enumerate(["bovada"] + BOOKS):
                r = random.Random(f"{ev['id']}{key}{int(hours_out // 4)}")
                noise = r.gauss(0, 0.018 if key == "bovada" else 0.006) * min(1, hours_out / 24 + 0.3)
                books.append({"key": key, "last_update": iso(now - timedelta(minutes=r.randint(1, 20))), "markets": self._markets(ev, noise, key, r)})
            out.append({"id": ev["id"], "sport_key": sport, "commence_time": ev["commence"], "home_team": ev["home"], "away_team": ev["away"], "bookmakers": books})
        return out

    def _markets(self, ev, noise, key, r):
        ms = []
        h, a = ev["home"], ev["away"]
        if "probs3" in ev:
            p = ev["probs3"]
            q = [max(0.05, p[0] + noise), max(0.05, p[1] - noise), p[2]]
            s = sum(q)
            prices = vig_prices([x / s for x in q])
            ms.append({"key": "h2h", "outcomes": [{"name": h, "price": prices[0]}, {"name": a, "price": prices[1]}, {"name": "Draw", "price": prices[2]}]})
            po = min(0.8, max(0.2, 0.52 + noise))
            pr = vig_prices([po, 1 - po])
            ms.append({"key": "totals", "outcomes": [{"name": "Over", "price": pr[0], "point": 2.5}, {"name": "Under", "price": pr[1], "point": 2.5}]})
            return ms
        ph = min(0.93, max(0.07, ev["p_home"] + noise))
        pr = vig_prices([ph, 1 - ph])
        ms.append({"key": "h2h", "outcomes": [{"name": h, "price": pr[0]}, {"name": a, "price": pr[1]}]})
        if "spread" in ev:
            sp = ev["spread"] + (r.choice([-0.5, 0, 0, 0, 0.5]) if key == "bovada" else 0)
            ps = min(0.8, max(0.2, 0.5 + noise))
            pr = vig_prices([ps, 1 - ps])
            ms.append({"key": "spreads", "outcomes": [{"name": h, "price": pr[0], "point": sp}, {"name": a, "price": pr[1], "point": -sp}]})
        else:
            fav_home = ev["p_home"] >= 0.5
            ps = min(0.8, max(0.2, 0.42 + noise))
            pr = vig_prices([ps, 1 - ps])
            ms.append({"key": "spreads", "outcomes": [{"name": h, "price": pr[0] if fav_home else pr[1], "point": -1.5 if fav_home else 1.5},
                                                      {"name": a, "price": pr[1] if fav_home else pr[0], "point": 1.5 if fav_home else -1.5}]})
        tot = ev["total"] + (r.choice([-1, -0.5, 0, 0, 0, 0.5, 1]) if key == "bovada" else 0)
        po = min(0.8, max(0.2, 0.5 - noise))
        pr = vig_prices([po, 1 - po])
        ms.append({"key": "totals", "outcomes": [{"name": "Over", "price": pr[0], "point": tot}, {"name": "Under", "price": pr[1], "point": tot}]})
        return ms


class DemoOdds(OddsClient):
    def __init__(self, settings, db, world):
        super().__init__(settings, db, transport=None)
        self.world, self.sim_now = world, None

    def odds(self, sport, markets=None):
        self.db.run("INSERT INTO credits(at,local_day,what,cost,remaining,used) VALUES(?,?,?,?,?,?)",
                    (iso(self.sim_now), self.db.get("sim_day", ""), f"demo {sport}", 0, 480, 20))
        return self.world.odds_payload(sport, self.sim_now)


class DemoEspn(Espn):
    def __init__(self, world, clock):
        super().__init__(transport=None)
        self.world, self.clock = world, clock

    def scoreboard(self, sport, day):
        out = []
        for ev in self.world.events.values():
            c = parse(ev["commence"])
            if ev["sport"] != sport or c.date() != day:
                continue
            done = self.clock() > c + timedelta(hours=4)
            city, state = ev["city"]
            out.append({"espn_id": ev["id"], "date": ev["commence"], "home": ev["home"], "away": ev["away"], "home_record": ev["rec"][0],
                        "away_record": ev["rec"][1], "home_score": ev["score"][0] if done else None, "away_score": ev["score"][1] if done else None,
                        "state": "post" if done else "pre", "completed": done, "venue": f"{city} Stadium", "city": city, "region": state,
                        "country": "USA", "indoor": ev["indoor"], "neutral": False})
        return out

    def summary(self, sport, espn_id):
        ev = self.world.events.get(espn_id, {})
        r = random.Random(espn_id)
        inj = []
        for team in (ev.get("home"), ev.get("away")):
            rows = [{"athlete": {"displayName": f"Player {r.randint(1, 99)}", "position": {"abbreviation": r.choice(["QB", "WR", "CB", "OT", "S", "RB"])}},
                     "status": r.choice(["Out", "Questionable", "Doubtful", "Out"])} for _ in range(r.randint(0, 6))]
            inj.append({"team": {"displayName": team}, "injuries": rows})
        p = ev.get("p_home", 0.5) + r.gauss(0, 0.07)
        return {"injuries": inj, "predictor": {"homeTeam": {"gameProjection": str(round(p * 100, 1))}, "awayTeam": {"gameProjection": str(round((1 - p) * 100, 1))}}}


class DemoMlb(Mlb):
    def __init__(self, world):
        super().__init__(transport=None)
        self.world = world

    def games(self, day):
        return [{"pk": ev["id"], "date": ev["commence"], "home": ev["home"], "away": ev["away"], "home_id": ev["home"], "away_id": ev["away"],
                 "home_pitcher": {"id": ev["home"], "name": ev["pitchers"][ev["home"]]["name"]}, "away_pitcher": {"id": ev["away"], "name": ev["pitchers"][ev["away"]]["name"]},
                 "venue": "Park"} for ev in self.world.events.values() if ev["sport"] == MLB]

    def pitcher(self, pid, season):
        for ev in self.world.events.values():
            if ev["sport"] == MLB and pid in ev["pitchers"]:
                p = dict(ev["pitchers"][pid])
                p.pop("name")
                return p
        return None

    def bullpen_ip(self, team_id, day, days=3):
        for ev in self.world.events.values():
            if ev["sport"] == MLB and team_id in ev.get("pen", {}) and parse(ev["commence"]).date() == day:
                return ev["pen"][team_id], 3
        return 6.0, 3


class DemoWeather(Weather):
    def coords(self, city, region=None, country=None):
        return (40.0 + len(city) / 10, -90.0)

    def at(self, lat, lon, when):
        r = random.Random(f"{lat}{when}")
        return {"temp_f": r.uniform(40, 92), "wind_mph": r.choice([4, 7, 9, 12, 17, 21]), "gust_mph": r.uniform(8, 32), "precip_pct": r.choice([0, 10, 20, 40, 80])}


def seed(settings, days: int = 12) -> dict:
    settings.anthropic_api_key = None
    settings.daily_credit_cap = 10_000
    path = settings.db_path
    for suffix in ("", "-wal", "-shm"):
        p = path.with_name(path.name + suffix)
        if p.exists():
            p.unlink()
    db = DB(path)
    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz).date()
    world = World(today - timedelta(days=days), today + timedelta(days=7))
    clock = {"now": None}
    espn = DemoEspn(world, lambda: clock["now"])
    odds = DemoOdds(settings, db, world)
    lab = Lab(settings, db, odds=odds, research=Research(db, espn=espn, mlb=DemoMlb(world), weather=DemoWeather(db)), brain=Brain(settings), espn=espn)
    lab._active_sports = lambda: None
    rng = random.Random(3)
    summary = {"days": 0, "picks": 0, "real_bets": 0}
    for i in range(days, -1, -1):
        day = today - timedelta(days=i)
        db.put("sim_day", day.isoformat())
        for hour, kind in ((settings.slate_hour, "slate"), (16, "rescan")):
            sim = datetime.combine(day, time(hour, 5), tz).astimezone(timezone.utc)
            if i == 0 and sim > datetime.now(timezone.utc):
                continue
            clock["now"] = odds.sim_now = sim
            lab.run(kind, tag="16" if kind == "rescan" else None, now=sim)
        # the owner places most of the CEO's real-money picks, at the listed price
        for p in db.all("SELECT * FROM picks WHERE local_day=? AND real_pick=1 AND decision IS NULL", (day.isoformat(),)):
            if rng.random() < 0.8:
                ledger.place_real(db, p["id"], p["price"], p["stake_units"] * settings.unit_dollars)
                summary["real_bets"] += 1
        # Sunday night board meeting (relaxed thresholds so a short demo still sees a firing)
        if day.weekday() == 6 and i:
            from . import meeting
            clock["now"] = datetime.combine(day, time(23, 30), tz).astimezone(timezone.utc)
            lab.grade(now=clock["now"])
            meeting.hold(lab, now=clock["now"], relaxed=True)
        # overnight: grade whatever has finished (the 4 PM rescan doubled as the closing-line read)
        if i:
            clock["now"] = datetime.combine(day + timedelta(days=1), time(7), tz).astimezone(timezone.utc)
            lab.grade(now=clock["now"])
        summary["days"] += 1
    clock["now"] = datetime.now(timezone.utc)
    lab.grade()
    summary["picks"] = db.one("SELECT COUNT(*) n FROM picks")["n"]
    summary["graded"] = db.one("SELECT COUNT(*) n FROM picks WHERE result IS NOT NULL")["n"]
    summary["meetings"] = [dict(m) for m in db.all("SELECT week, fired, hired FROM meetings")]
    return summary
