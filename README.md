# Agent Lab

Twenty AI tipsters with their own strategies and personalities research NFL, college football, MLB, NBA and soccer every
morning, price every bet against the sharp market, and hand you a short slate. A risk board checks each pick, the CEO
decides which few deserve real money, and **you** place those on Bovada yourself. Everything else is tracked on paper so
you can see, over time, which tipsters actually know something.

## How a day works

| When (your time zone) | What happens | Costs |
|---|---|---|
| 8:00 | **Morning slate.** Pull Bovada + 9 other books from The Odds API, research every game, each tipster picks from bets its strategy allows, risk board reviews, CEO writes the memo and picks real-money bets. Phone alert if enabled. | ~3 odds credits per sport, Claude calls |
| 12:00, 16:00 | **Rescans** for sports with games in the next 18 hours. New strong picks are flagged LATE and alerted. | same, fewer calls |
| every 5 min | **Closing lines** for games about to start (for CLV), **grading** of finished games from ESPN. | closing: ~3 credits per sport; grading free |
| Sunday 9 PM | **Board meeting.** The Commish writes the weekly minutes, names an MVP, and may fire one tipster (only from a shortlist: enough bets, losing money *and* losing to the closing line, or an empty wallet). The fired tipster goes to **tipster jail** in the office; Claude designs a replacement for the empty seat. | 2 Claude calls |
| 3 AM | **Backup** of the database to `data/backups/` (last 14 kept). | free |

## The firm

- **20 tipsters on 5 desks** (`lab/roster.py`). Each has a strategy in `lab/strategies.py` that decides which bets it may even consider (Rhea: injury reports; Stormy: wind; Mateo: starting pitchers; Quinn: pure price; Coin Flip: random, as a benchmark...).
- **Claude** (when `ANTHROPIC_API_KEY` is set) decides which of those options to actually bet, how much, and explains it in character. It can only choose among real priced options, so it cannot invent a bet or a line. Without a key, a rule-based fallback picks.
- **Risk board** (`lab/board.py`): Mara vetoes bad prices (worse than normal juice) and flags stale quotes; Barb caps stakes and puts cold or broke tipsters on paper only; Carl merges duplicate picks and flags conflicts.
- **The Commish** picks up to 6 real-money bets per day and writes the memo.
- **Lydon** builds one two-leg parlay a day from other tipsters' cleared picks (each leg within 2.5% of fair, the parlay within 4.5%; real money only at -3% or better).
- **Memory**: each tipster sees its last 8 graded picks with results and CLV before deciding (`LAB_AGENT_MEMORY=0` turns this off).
- **New hires** get a strategy assembled from safe parts (sports, bet types, side, price range, and one signal: wind, heat, cold, injuries, rest, ESPN predictor, pitching, line movement), so a hire can never run arbitrary code.
- **Check price** on a card re-reads that one game from The Odds API (1 credit) and says whether the price is still at or above the floor.

## Reading a pick card

- **Bovada / Fair / Vs fair**: Bovada's price, the no-vig consensus of the other books (Pinnacle weighted in), and expected value against it. Because of Bovada's cut, most bets sit around −2%. A **fair price** is −1.5% or better; only those get real money.
- **Floor**: the worst price still worth taking. If Bovada has moved past it when you go to bet, pass.
- **CLV** (after the game): did the bet beat the closing line? Over a few weeks this separates skill from luck much faster than win-loss.

## Money

- 1 unit = $1 (configurable). Every tipster has a $10 real wallet; paper never runs dry.
- When you place a bet, press **Bet** on the card, place it on Bovada, and enter the price you actually got. A merged pick splits stake and result between every tipster who made it.
- A tipster whose real wallet can't cover a bet goes paper-only. Nothing here places bets or touches your Bovada account.

## Run it locally (Windows)

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # then fill in ODDS_API_KEY (and ANTHROPIC_API_KEY)
.venv\Scripts\python -m lab set-password
.venv\Scripts\python -m lab serve    # http://127.0.0.1:8000
```

Useful commands:

```bash
python -m lab run slate      # run the morning slate now (spends odds credits)
python -m lab tick           # what cron does every 5 minutes
python -m lab grade          # grade finished games (free)
python -m lab status         # credits, recent runs
python -m lab meeting        # hold this week's board meeting now (--force to redo it)
python -m lab backup         # copy the database to data/backups now
python -m lab demo           # fill data/demo.sqlite3 with two synthetic weeks (no network)
python -m lab serve --db demo.sqlite3
python -m unittest discover -s tests
```

## Deploy on Namecheap cPanel

1. **Upload** this folder (without `.venv`, `data`, `legacy`) to e.g. `/home/USER/agentlab`. Keep it *outside* `public_html`.
2. **cPanel → Setup Python App → Create application**
   - Python version **3.13**
   - Application root: `agentlab`
   - Application URL: a subdomain like `lab.yoursite.com` (simplest), or a path like `yoursite.com/lab`
   - Startup file: `passenger_wsgi.py`, entry point: `application`
3. In the app page, copy the "Enter to the virtual environment" command, run it in **cPanel → Terminal**, then:
   ```bash
   cd ~/agentlab
   pip install -r requirements.txt
   cp .env.example .env && nano .env      # keys, LAB_PUBLIC_URL=https://lab.yoursite.com
   python -m lab set-password
   python -m lab run slate                # first slate, to check everything works
   ```
   Then press **Restart** on the Python App page.
4. **cPanel → Cron Jobs**, every 5 minutes (`*/5 * * * *`), with the virtualenv python path shown on the app page:
   ```
   cd /home/USER/agentlab && /home/USER/virtualenv/agentlab/3.13/bin/python -m lab tick >> data/cron.log 2>&1
   ```
5. Turn on **SSL** for the subdomain (cPanel → SSL/TLS Status → AutoSSL) so the login cookie is sent over HTTPS only.

The **Run slate now** button starts a background process from the web app. If your host blocks that, the cron job still runs everything on schedule.

## Updating the live site (automatic)

The code lives in a public GitHub repo. A cron job on the server runs `deploy.sh` every 5 minutes: if GitHub has a
newer commit, it pulls it, copies the app into `~/agentlab`, installs any new packages, and restarts the app. So an
update is just `git push`; it is live within about 5 minutes. `data/` and `.env` on the server are never touched,
and deploys are logged in `data/deploy.log`.

```
*/5 * * * * /bin/sh $HOME/repositories/agentlab/deploy.sh >> $HOME/agentlab/data/deploy.log 2>&1
```

Manual fallback: cPanel -> Git Version Control -> Manage -> Pull or Deploy -> Update from Remote -> Deploy HEAD Commit.

## Costs

- **The Odds API**: free tier is 500 credits/month; keep `LAB_DAILY_CREDIT_CAP=16` (about one full scan a day plus a couple of closing reads). The $30 plan (20,000) comfortably covers three scans a day plus closing lines; raise the cap to ~400.
- **Claude API**: roughly $0.50/day with Opus for everyone. `LAB_TIPSTER_MODEL=claude-sonnet-5-5` roughly halves that; `claude-haiku-4-5` cuts it by ~75%. The Ledger tab shows actual spend per day after the first real runs. Set a monthly limit in the Anthropic console.

## Honest notes

Most bets at a retail book lose slightly on average; that's the cut. The point of the lab is to find out, with real prices and CLV, whether any tipster's read beats the market before you trust it with more than a dollar. Bet only what you're fine losing.

## Layout

```
lab/            the app (pipeline, strategies, agents, board, ledger, grading, web)
lab/sources/    The Odds API, ESPN, MLB Stats API, Open-Meteo adapters
web/            the pixel office UI
tests/          unit + end-to-end tests
legacy/         the earlier ChatGPT version, kept for reference
data/           SQLite databases (not deployed, not served)
```
