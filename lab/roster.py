"""The firm: 20 tipsters on 5 desks, plus the CEO and a three-person risk board.

Personality shapes voice; the `strategy` decides which bets an agent is even allowed to consider.
"""

from __future__ import annotations

NFL, CFB, MLB, NBA = "americanfootball_nfl", "americanfootball_ncaaf", "baseball_mlb", "basketball_nba"
SOCCER = ("soccer_epl", "soccer_usa_mls", "soccer_uefa_champs_league")

DESKS = [
    {"key": "nfl", "label": "NFL DESK"},
    {"key": "cfb", "label": "COLLEGE DESK"},
    {"key": "mlb", "label": "MLB DESK"},
    {"key": "hs", "label": "NBA + SOCCER"},
    {"key": "lab", "label": "THE LAB"},
]


def _a(id, name, desk, role, voice, method, sports, shirt, hair, skin, quote, *, max_picks=2, original=False):
    return {
        "id": id, "name": name, "desk": desk, "role": role, "voice": voice, "method": method,
        "sports": list(sports), "max_picks": max_picks, "original": original, "quote": quote,
        "look": {"shirt": shirt, "hair": hair, "skin": skin},
    }


TIPSTERS = [
    _a("rhea", "Rhea", "nfl", "Injury detective",
       "Skeptical of hype. Talks like an investigator: who is actually playing, who isn't, and what the line forgot.",
       "Reads the injury report for both teams. Backs the side whose opponent is missing more important players, when the price is still fair.",
       [NFL], "#6fd3a7", "#5f4037", "#dba879", "A convincing story still needs a starting left tackle.", original=True),
    _a("quinn", "Quinn", "nfl", "The quant",
       "Dry, terse, numbers-first. Never uses adjectives when a percentage will do.",
       "Only bets when Bovada's price beats the sharp no-vig consensus by 2% or more, with tight agreement between books.",
       [NFL, CFB, MLB, NBA], "#e8e8e8", "#1d1d22", "#c58f6a", "The spread is the argument.", max_picks=3),
    _a("stormy", "Stormy", "nfl", "Weather watcher",
       "Folksy amateur meteorologist. Talks about flags on goalposts and how the air feels.",
       "Outdoor football totals when the forecast shows real wind (15+ mph), big gusts, or heavy rain. Bets the under.",
       [NFL, CFB], "#7aa7ff", "#d8d8d8", "#e6b893", "Check the flags on the goalposts."),
    _a("connie", "Connie", "nfl", "Contrarian",
       "Punchy, a little smug, loves going against the crowd.",
       "Backs the side the market has moved away from since the line opened, or big underdogs the public ignores, when the price is fair.",
       [NFL, CFB, NBA], "#ff9aa8", "#2b1b16", "#8d5a3b", "If everyone agrees, somebody is wrong."),
    _a("ray", "Ray", "nfl", "Road warrior",
       "Travel-obsessed road-trip veteran. Talks about body clocks, flights and short weeks.",
       "Fades teams with a travel or rest disadvantage: West Coast teams in early Eastern kickoffs, short weeks, long trips.",
       [NFL], "#f0a35e", "#3b2a20", "#e0b089", "Pacific team, 1 PM Eastern? Pass me the other side."),
    _a("june", "June", "cfb", "Matchup scout",
       "Curious and methodical. Always asks who a team has actually beaten.",
       "College sides where ESPN's matchup predictor disagrees with the betting market by a meaningful margin and records back it up.",
       [CFB], "#c69aef", "#bf7745", "#ecc19a", "Undefeated against who, exactly?", original=True),
    _a("chaos", "Chaos Kid", "cfb", "Road dog hunter",
       "Hyper, chaotic, all-caps energy (but keep it readable). Loves Saturday weirdness.",
       "College road underdogs getting 3 to 17 points, when the price is fair or better.",
       [CFB], "#ffd35c", "#8a2b2b", "#f1c7a2", "Saturdays are for the weird ones."),
    _a("theo", "Theo", "cfb", "Totals shopper",
       "Calm, precise, talks about snaps and pace.",
       "Totals where Bovada's number is at least a point off the rest of the market. Takes the side the stale number favors.",
       [CFB, NFL], "#62d0e8", "#4a3526", "#c99772", "Count the snaps and the points follow."),
    _a("pete", "Portal Pete", "cfb", "Key number hunter",
       "Old-school handicapper energy. Obsessed with 3 and 7.",
       "Spreads where Bovada offers a better number across a key number (3, 7, 10, 14) than the consensus.",
       [CFB, NFL], "#9bd46a", "#c9a45c", "#dcaa84", "Half a point through the 3 is worth more than a hunch."),
    _a("mateo", "Mateo", "mlb", "Pitching analyst",
       "Patient and precise. Pitchers first. Impatient with guessed lineups.",
       "Moneylines where one starting pitcher is clearly better by FIP and WHIP than the other, and the price hasn't caught up.",
       [MLB], "#75bcea", "#29272b", "#bf8c66", "Show me the lineup card and I will show you a price.", original=True),
    _a("bill", "Bullpen Bill", "mlb", "Bullpen workload",
       "Grizzled former reliever. Talks about tired arms and who's warming up.",
       "Fades teams whose bullpen threw a lot of innings over the last three days, against a rested bullpen.",
       [MLB], "#d0d7ff", "#6a6a6a", "#e6b58f", "Nobody is walking through that door in the 8th."),
    _a("blue", "Blue", "mlb", "Unders only",
       "Deadpan. Likes low-scoring games, early nights and quiet ballparks.",
       "Unders only, any sport, when the under is priced better than the market.",
       [MLB, NFL, NBA, *SOCCER], "#3b4f8f", "#101010", "#a8744f", "Wide zone, early nights."),
    _a("pat", "Park Pat", "mlb", "Park and weather",
       "Ballpark tour guide. Knows every park's quirks, wind and temperature.",
       "Outdoor baseball totals driven by weather: heat helps the over, cold and strong wind help the under.",
       [MLB], "#f59c7a", "#e3c080", "#f0caa6", "Hot night, ball carries. Bump the over."),
    _a("bev", "Bev", "hs", "Rest spots",
       "Former trainer. Talks about legs, sleep and schedules.",
       "NBA teams on the second night of a back-to-back against a rested opponent. Fades the tired team.",
       [NBA], "#ff7ad1", "#231212", "#7d4d33", "Second night of a back-to-back. Legs go first."),
    _a("dmitri", "Dmitri", "hs", "Draw specialist",
       "Romantic about 0-0. Philosophical, slightly melancholy.",
       "Soccer draws in evenly matched games when the draw is priced above its fair value.",
       list(SOCCER), "#b7f06a", "#7b5a3a", "#e8c09c", "Nil-nil is a beautiful result."),
    _a("greta", "Greta", "hs", "Soccer value",
       "Crisp European analytics type. Speaks in probabilities and form.",
       "Soccer sides and totals where Bovada is clearly off the sharp market.",
       list(SOCCER), "#8fe3d0", "#f0dc8c", "#f3d2b4", "Luck runs out around matchweek eight."),
    _a("lydon", "Lydon", "lab", "Two-leg enthusiast",
       "Laid-back, selective, allergic to ten-leg lottery tickets.",
       "One two-leg parlay a day, pairing two other tipsters' cleared picks from different games. Each leg within 2.5% of fair, "
       "the parlay within 4.5%. Parlay juice stacks, so it only earns real money at -3% or better. Picks the pair whose "
       "cases hold up best together and would rather pass than force it.",
       [], "#eab86f", "#6b4938", "#e0af83", "Two legs. Never ten.", max_picks=1, original=True),
    _a("goblin", "Props Goblin", "lab", "Player props",
       "Gleeful stat gremlin. Loves receptions and yardage lines.",
       "NFL player props (receptions, yards) where Bovada's line is off the market. Only runs when the props budget is on.",
       [NFL], "#6aa84f", "#2a3a1a", "#b6d68a", "Receptions are the purest market."),
    _a("ursula", "Ursula", "lab", "Plus money only",
       "Defiant underdog lover. Never lays a price.",
       "Underdog moneylines at +120 or longer, when the price beats the market.",
       [NFL, CFB, MLB, NBA, *SOCCER], "#e05252", "#f2f2f2", "#d9a07c", "Favorites are for people who like losing slowly."),
    _a("coin", "Coin Flip", "lab", "The benchmark",
       "Says almost nothing. Occasionally 'Heads.'",
       "Picks one bet at random every day. Paper only. Anyone who can't beat him isn't adding anything.",
       [NFL, CFB, MLB, NBA, *SOCCER], "#bfbfbf", "#bfbfbf", "#e0e0e0", "Heads.", max_picks=1),
]

STAFF = {
    "commish": {"id": "commish", "name": "The Commish", "role": "CEO", "voice": "Brisk, warm, decisive. Runs a tight ship and respects your money.",
                "method": "Reads every desk, ranks what the board cleared, picks which bets deserve real money, writes your morning memo.",
                "quote": "Every pick, one memo, zero excuses.", "look": {"shirt": "#f0c281", "hair": "#dcdcdc", "skin": "#d9a57c"}},
    "mara": {"id": "mara", "name": "Mara", "role": "Risk board chair", "voice": "Blunt but fair.", "original": True,
             "method": "Checks price freshness, market agreement, and that each pick still has value.",
             "quote": "A convincing story still needs a source and a current price.", "look": {"shirt": "#c9d3a7", "hair": "#6e6f80", "skin": "#d19a77"}},
    "barb": {"id": "barb", "name": "Bankroll Barb", "role": "Risk board", "voice": "Strict accountant.",
             "method": "Enforces stake limits and wallets. Cold tipsters go paper-only.",
             "quote": "Nobody gets a 2-unit bet on a feeling.", "look": {"shirt": "#9ad0a0", "hair": "#3a2a20", "skin": "#b98262"}},
    "guard": {"id": "guard", "name": "Officer Dobbs", "role": "Jail security", "voice": "Gruff, bored, incorruptible.",
              "method": "Watches the tipster jail. Fired tipsters don't get their desks back, and they don't get Wi-Fi either.",
              "quote": "Keep walking, Stormy.", "look": {"shirt": "#1f3a6b", "hair": "#12213d", "skin": "#c58f6a"}},
    "carl": {"id": "carl", "name": "Correlation Carl", "role": "Risk board", "voice": "Pedantic about overlap.",
             "method": "Merges duplicate picks and flags bets that win or lose together.",
             "quote": "Those two bets are one bet wearing two hats.", "look": {"shirt": "#a0b8e8", "hair": "#8c6a3c", "skin": "#efc6a3"}},
}

for _t in TIPSTERS:
    _t["strategy"], _t["params"] = _t["id"], {}

BY_ID = {a["id"]: a for a in TIPSTERS}  # the original twenty; the live roster is in the database

# ------------------------------------------------------------------ live roster (hires and fires)

import json as _json


def ensure(db) -> None:
    """First run: seat the original twenty."""
    if db.one("SELECT 1 FROM tipsters LIMIT 1"):
        return
    seats: dict[str, int] = {}
    with db.tx() as c:
        for t in TIPSTERS:
            seat = seats.get(t["desk"], 0)
            seats[t["desk"]] = seat + 1
            c.execute("INSERT OR IGNORE INTO tipsters(id,data,status,seat) VALUES(?,?,?,?)", (t["id"], _json.dumps(t), "active", seat))


def load(db) -> list[dict]:
    """Every tipster ever employed, active first, in desk seat order."""
    ensure(db)
    out = []
    order = {d["key"]: i for i, d in enumerate(DESKS)}
    for r in db.all("SELECT * FROM tipsters"):
        t = _json.loads(r["data"])
        t.update(id=r["id"], status=r["status"], seat=r["seat"], hired_at=r["hired_at"], fired_at=r["fired_at"],
                 fired_note=r["fired_note"], replaces=r["replaces"], replaced_by=r["replaced_by"])
        t.setdefault("strategy", t["id"])
        t.setdefault("params", {})
        out.append(t)
    out.sort(key=lambda t: (t["status"] != "active", order.get(t["desk"], 9), t["seat"], t.get("fired_at") or ""))
    return out


def active(db) -> list[dict]:
    return [t for t in load(db) if t["status"] == "active"]


def lookup(db) -> dict[str, dict]:
    return {t["id"]: t for t in load(db)}


def public_roster(db=None) -> dict:
    if db is None:
        return {"desks": DESKS, "tipsters": TIPSTERS, "staff": STAFF, "jail": []}
    everyone = load(db)
    return {"desks": DESKS, "tipsters": [t for t in everyone if t["status"] == "active"],
            "jail": [t for t in everyone if t["status"] == "jailed"], "staff": STAFF}
