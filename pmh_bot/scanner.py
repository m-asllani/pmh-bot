"""Premarket scan: find today's gappers and mark their premarket high / low."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from . import data
from .config import load_watchlist
from .market import OPEN, PREMARKET_START
from .strategy import fmt_price

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    symbol: str
    last: float
    prev_close: float | None
    gap_pct: float | None
    pm_high: float
    pm_low: float
    active_minutes: int
    from_watchlist: bool


def premarket_stats(bars, as_of: dt.datetime | None = None) -> dict | None:
    times = bars.index.time
    pm = bars[(times >= PREMARKET_START) & (times < OPEN)]
    if as_of is not None:
        pm = pm[pm.index <= as_of]
    if pm.empty:
        return None
    return {
        "pm_high": float(pm["High"].max()),
        "pm_low": float(pm["Low"].min()),
        "active_minutes": len(pm),  # Yahoo only emits a premarket bar when something traded
        "last": float(pm["Close"].iloc[-1]),
    }


def scan(cfg: dict, day: dt.date, as_of: dt.datetime | None = None) -> list[Candidate]:
    sc = cfg["scanner"]
    watchlist = load_watchlist(cfg)
    universe = data.liquid_universe(cfg)
    symbols = list(dict.fromkeys(watchlist + universe))
    log.info("Scanning %d symbols (%d from watchlist) for premarket gappers...", len(symbols), len(watchlist))

    bars = data.intraday_bars(symbols, day)
    stats = {}
    for sym, frame in bars.items():
        s = premarket_stats(frame, as_of)
        if not s:
            continue
        in_range = sc["min_price"] <= s["last"] <= sc["max_price"]
        if sym in watchlist or (in_range and s["active_minutes"] >= sc["min_premarket_active_minutes"]):
            stats[sym] = s

    prev = data.previous_closes(list(stats), day)
    gappers, extras = [], []
    for sym, s in stats.items():
        pc = prev.get(sym)
        gap = (s["last"] / pc - 1) * 100 if pc else None
        cand = Candidate(sym, s["last"], pc, gap, s["pm_high"], s["pm_low"], s["active_minutes"], sym in watchlist)
        liquid = s["active_minutes"] >= sc["min_premarket_active_minutes"]
        if gap is not None and gap >= sc["min_gap_percent"] and liquid:
            gappers.append(cand)
        elif cand.from_watchlist and sc["always_include_watchlist"]:
            extras.append(cand)

    gappers.sort(key=lambda c: c.gap_pct, reverse=True)
    picked = gappers[: int(sc["max_stocks"])]
    return picked + [c for c in extras if c not in picked]


def format_watchlist(cands: list[Candidate], day: dt.date, title: str) -> str:
    if not cands:
        return f"{title} - {day:%a %b %d}\nNo stocks passed the premarket filters today."
    lines = [f"{title} - {day:%a %b %d}", ""]
    for i, c in enumerate(cands, 1):
        gap = f"{c.gap_pct:+.1f}%" if c.gap_pct is not None else "n/a"
        tag = " (watchlist)" if c.from_watchlist else ""
        lines.append(f"{i}. {c.symbol}{tag}  ${fmt_price(c.last)}  {gap}")
        lines.append(f"   PMH {fmt_price(c.pm_high)} | PML {fmt_price(c.pm_low)}")
    lines += ["", "Over PMH: VWAP under PMH = PMH retest, VWAP over PMH = VWAP retest. Sell at HOD.",
              "You'll get an alert on each breakout, retest entry and high-of-day exit."]
    return "\n".join(lines)
