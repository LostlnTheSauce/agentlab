"""Tipster and CEO decisions. Uses Claude when ANTHROPIC_API_KEY is set, otherwise a rule-based fallback.

The model only ever chooses among numbered options it was given (each with a real Bovada price),
so it cannot invent a bet or a line. It decides whether to bet, how much (1-2 units), and explains
why in the agent's voice.
"""

from __future__ import annotations

import json
import logging
import threading

from . import oddsmath as om
from .research import briefing
from .sources.odds import book_name, short

log = logging.getLogger("lab.agents")

# $ per million tokens (input, output). Used only to show spend in the UI.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0), "claude-fable-5-1": (10.0, 50.0)}

FIRM_RULES = """You are a tipster at Agent Lab, a small sports betting research firm. The owner reads your picks and \
decides whether to place them on Bovada with real money (usually $1-2 per bet). Every option you see already has a \
real price at one of the owner's sportsbooks (the best one available) and a fair price from the sharp-book consensus;
"edge" is expected value against that consensus. Because of the books' cut, most prices sit around -2% edge. A bet at -1.5% or better is a fair price; worse than that it
needs a genuinely strong read. Your method is your claim that the market is missing something: say what, concretely.

Rules:
- Choose only from the numbered options. You may choose none. Passing is respectable; forcing bets is not.
- Stake is 1 unit normally, 2 units only when the evidence and the edge are both strong.
- Confidence is 1 (coin flip with a small edge) to 5 (rare, everything lines up).
- Reasoning: at most 45 words, in your own voice, citing the concrete facts that matter (numbers, injuries, weather, price).
- Never claim certainty, never promise wins, never invent facts that aren't in the notes.
"""

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "option": {"type": "integer"},
                    "stake_units": {"type": "integer", "enum": [1, 2]},
                    "confidence": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                    "reasoning": {"type": "string"},
                },
                "required": ["option", "stake_units", "confidence", "reasoning"],
                "additionalProperties": False,
            },
        },
        "pass_note": {"type": "string"},
    },
    "required": ["picks", "pass_note"],
    "additionalProperties": False,
}

CEO_SCHEMA = {
    "type": "object",
    "properties": {
        "memo": {"type": "string"},
        "real_money": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["memo", "real_money"],
    "additionalProperties": False,
}


def describe(c: dict) -> str:
    if c["market"] == "parlay":
        what = f"Parlay: {c['selection']}"
    elif c.get("player"):
        what = f"{c['player']} {c['selection']} {c['point']:g} {c['market'].replace('player_', '').replace('_', ' ')}"
    elif c["market"] == "h2h":
        what = "Draw" if c["selection"] == "Draw" else f"{c['selection']} moneyline"
    elif c["market"] == "spreads":
        what = f"{c['selection']} {c['point']:+g}"
    else:
        what = f"{c['selection']} {c['point']:g}"
    return f"{what} {om.fmt(c['price'])}"


def matchup(c: dict) -> str:
    return f"{c['away']} at {c['home']}"


class Brain:
    def __init__(self, settings):
        self.s = settings
        self._client = None
        self.usage = {"input": 0, "output": 0, "calls": 0, "errors": 0, "cost": 0.0}
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None and self.s.llm_enabled:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.s.anthropic_api_key, max_retries=2, timeout=120)
        return self._client

    def _ask(self, model: str, effort: str, system: str, user: str, schema: dict) -> dict | None:
        import anthropic
        kwargs = dict(
            model=model, max_tokens=8000, system=system, messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            try:
                resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            except anthropic.BadRequestError:
                resp = self.client.messages.create(**kwargs)
            tin = (getattr(resp.usage, "input_tokens", 0) or 0) + (getattr(resp.usage, "cache_creation_input_tokens", 0) or 0)
            tcache = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
            tout = getattr(resp.usage, "output_tokens", 0) or 0
            pin, pout = PRICES.get(model, (4.0, 20.0))
            with self._lock:
                self.usage["calls"] += 1
                self.usage["input"] += tin + tcache
                self.usage["output"] += tout
                self.usage["cost"] = round(self.usage["cost"] + (tin * pin + tcache * pin * 0.1 + tout * pout) / 1e6, 4)
            if resp.stop_reason in ("refusal", "max_tokens"):
                return None
            text = next((b.text for b in resp.content if b.type == "text"), "")
            return json.loads(text)
        except anthropic.RateLimitError:
            log.warning("Claude rate limited; using fallback")
        except anthropic.APIStatusError as e:
            log.warning("Claude API error %s; using fallback", e.status_code)
        except anthropic.APIConnectionError:
            log.warning("Claude unreachable; using fallback")
        except (json.JSONDecodeError, StopIteration):
            log.warning("Claude returned unparseable output; using fallback")
        with self._lock:
            self.usage["errors"] += 1
        return None

    # ------------------------------------------------------------------ tipsters

    def decide(self, agent: dict, options: list[dict], ctx: dict, record: str) -> tuple[list[dict], str]:
        """Returns (picks, note). Each pick: {cand, signal, stake_units, confidence, reasoning}."""
        if not options:
            return [], ""
        if agent["id"] == "coin" or not self.s.llm_enabled:
            return self._fallback(agent, options)
        lines = []
        for i, o in enumerate(options, 1):
            c = o["cand"]
            lines.append(
                f"[{i}] {short(c['sport'])} · {matchup(c)} · starts {c['commence']}\n"
                f"    Bet: {describe(c)}{' at ' + book_name(c.get('book')) if c.get('book') else ''} | fair {om.fmt(om.american_from_prob(c['fair_prob']))} | edge {c['edge'] * 100:+.1f}% | "
                f"{c['books']} books{' (fair estimated)' if c.get('estimated') else ''}\n"
                f"    Why it's on your desk: {o['signal']}\n"
                f"    Research: {briefing(ctx.get(c['event_id'], {}))}"
            )
        system = (
            FIRM_RULES
            + f"\nYou are {agent['name']}, the firm's {agent['role'].lower()}. Personality: {agent['voice']}\n"
            + f"Your method: {agent['method']}\nYou may make at most {agent['max_picks']} picks today."
        )
        user = f"Your season so far: {record}.\n\nToday's options:\n\n" + "\n\n".join(lines)
        out = self._ask(self.s.tipster_model, self.s.tipster_effort, system, user, PICK_SCHEMA)
        if out is None:
            return self._fallback(agent, options)
        picks, seen = [], set()
        for p in out.get("picks", [])[: agent["max_picks"]]:
            i = p.get("option")
            if not isinstance(i, int) or not 1 <= i <= len(options) or i in seen:
                continue
            seen.add(i)
            o = options[i - 1]
            picks.append({**o, "stake_units": min(int(p.get("stake_units", 1)), self.s.max_units),
                          "confidence": int(p.get("confidence", 3)), "reasoning": str(p.get("reasoning", ""))[:400]})
        return picks, str(out.get("pass_note", ""))[:300]

    def _fallback(self, agent: dict, options: list[dict]) -> tuple[list[dict], str]:
        picks = []
        for o in options[: agent["max_picks"]]:
            e = o["cand"]["edge"]
            strong = e >= 0.03 and not o["cand"].get("estimated")
            picks.append({**o, "stake_units": 2 if strong and self.s.max_units >= 2 else 1,
                          "confidence": 4 if e >= 0.01 else 3 if e >= -0.015 else 2,
                          "reasoning": o["signal"] if o["signal"].strip() == agent["quote"].strip() else f"{o['signal']} {agent['quote']}"})
        return picks, ""

    # ------------------------------------------------------------------ CEO

    def memo(self, cleared: list[dict], flagged: list[dict], vetoed: list[dict], standings: str, max_real: int) -> tuple[str, list[str]]:
        """Returns (memo text, ids of picks recommended for real money, in priority order)."""
        ranked = sorted(cleared, key=rank_score, reverse=True)
        fallback_ids = pick_real([p for p in ranked if p.get("kind") == "price"], max_real)
        if not cleared and not flagged:
            return ("Quiet board this morning. Nobody found a price worth your money, and that's fine. "
                    "The desks will look again this afternoon."), []
        if not self.s.llm_enabled:
            return _template_memo(ranked, flagged, vetoed, fallback_ids), fallback_ids
        lines = [f"[{i}] {'PRICE' if p.get('kind') == 'price' else 'STORY'} PICK. {p['agent_name']}: {describe(p)}{' at ' + book_name(p['book']) if p.get('book') else ''} ({short(p['sport'])}, {matchup(p)}) edge {p['edge'] * 100:+.1f}%, "
                 f"{p['stake_units']:g}u, confidence {p['confidence']}. {p['reasoning']}" for i, p in enumerate(ranked, 1)]
        other = [f"- {p['agent_name']}: {describe(p)} — {p['board_status'].upper()}: {'; '.join(p['board_notes'])}" for p in flagged + vetoed]
        system = (
            "You are The Commish, CEO of Agent Lab, a 20-tipster sports betting research firm. Voice: brisk, warm, decisive; "
            "you respect the owner's money. The owner bets small ($1-2) on Bovada and reads your memo each morning.\n"
            f"Choose up to {max_real} cleared picks (by number) that deserve real money, best first. Only picks marked PRICE PICK "
            "are eligible: the owner's book pays at least the fair price on them. STORY PICKs ride on the tipster's read at a "
            "normal bookmaker price and stay on paper; you may mention a good one as worth watching, but never recommend it for "
            "money. Prefer better prices and specific reads, diversify across games, and skip anything shaky; choosing fewer, "
            "or none, is fine, and on a day with no price picks say so plainly. Then write a memo of at most 110 words: "
            "the headline pick and why (say which sportsbook to use when the pick names one), anything the owner should watch, and total real-money exposure. Plain text, no lists, no hype, no promises."
        )
        user = ("Cleared by the risk board:\n" + ("\n".join(lines) or "(none)")
                + "\n\nFlagged or vetoed:\n" + ("\n".join(other) or "(none)")
                + f"\n\nTipster standings (paper): {standings}")
        out = self._ask(self.s.ceo_model, self.s.ceo_effort, system, user, CEO_SCHEMA)
        if not out:
            return _template_memo(ranked, flagged, vetoed, fallback_ids), fallback_ids
        ids = []
        for i in out.get("real_money", []):
            if isinstance(i, int) and 1 <= i <= len(ranked) and ranked[i - 1]["id"] not in ids and ranked[i - 1].get("kind") == "price":
                ids.append(ranked[i - 1]["id"])
        return str(out.get("memo", ""))[:1200] or _template_memo(ranked, flagged, vetoed, ids), ids[:max_real]


def rank_score(p: dict) -> float:
    return (p.get("edge") or 0) * 100 * (0.7 if p.get("estimated") else 1) + p.get("confidence", 3) * 0.5 + (p.get("group_size", 1) - 1)


def pick_real(ranked: list[dict], max_real: int) -> list[str]:
    ids, games = [], set()
    for p in ranked:
        if len(ids) >= max_real:
            break
        floor = -0.03 if p["market"] == "parlay" else -0.015
        if p["event_id"] in games or p.get("paper_only") or (p.get("edge") if p.get("edge") is not None else -1) < floor or p.get("confidence", 3) < 3:
            continue
        ids.append(p["id"])
        games.add(p["event_id"])
    return ids


def _template_memo(ranked, flagged, vetoed, real_ids) -> str:
    by_id = {p["id"]: p for p in ranked}
    real = [by_id[i] for i in real_ids if i in by_id]
    if not real:
        return (f"The board cleared {len(ranked)} picks, but none is a price pick: no book is paying at least the fair price today. "
                "Let them ride on paper and we'll learn something either way.")
    top = real[0]
    exposure = sum(p["stake_units"] for p in real)
    return (f"Morning. {len(ranked)} picks cleared the board, {len(flagged)} flagged, {len(vetoed)} vetoed. "
            f"Headline: {top['agent_name']}'s {describe(top)} ({matchup(top)}), edge {top['edge'] * 100:+.1f}%. "
            f"I'm recommending {len(real)} for real money, {exposure:g} units total. Check each price before you bet; "
            "if Bovada has moved past the floor on the card, skip it.")
