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


@dataclass
class Movers:
    bulls: list[Candidate]   # biggest gaps up: watch for a break over PMH
    bears: list[Candidate]   # biggest gaps down: watch for a break under PML
    extras: list[Candidate]  # your watchlist names that aren't on either list


def scan(cfg: dict, day: dt.date, as_of: dt.datetime | None = None) -> list[Candidate]:
    """The stocks to monitor for the long PMH / VWAP setup: bulls plus your watchlist."""
    movers = scan_movers(cfg, day, as_of)
    return movers.bulls + movers.extras


def scan_movers(cfg: dict, day: dt.date, as_of: dt.datetime | None = None) -> Movers:
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
    bulls, bears, watched = [], [], []
    for sym, s in stats.items():
        pc = prev.get(sym)
        gap = (s["last"] / pc - 1) * 100 if pc else None
        cand = Candidate(sym, s["last"], pc, gap, s["pm_high"], s["pm_low"], s["active_minutes"], sym in watchlist)
        liquid = s["active_minutes"] >= sc["min_premarket_active_minutes"]
        if gap is not None and liquid and gap >= sc["min_gap_percent"]:
            bulls.append(cand)
        elif gap is not None and liquid and gap <= -sc["min_gap_percent"]:
            bears.append(cand)
        if cand.from_watchlist:
            watched.append(cand)

    top = int(sc["max_stocks"])
    bulls = sorted(bulls, key=lambda c: c.gap_pct, reverse=True)[:top]
    bears = sorted(bears, key=lambda c: c.gap_pct)[:top]
    extras = [c for c in watched if c not in bulls] if sc["always_include_watchlist"] else []
    return Movers(bulls, bears, extras)


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


Levels = dict  # symbol -> (pm_high, pm_low) from the previous report


def _change_note(c: Candidate, previous: Levels | None, bull: bool) -> str:
    notes = []
    if previous:  # nothing to compare against on the first report (or if it came back empty)
        if c.symbol not in previous:
            notes.append("new on list")
        else:
            old_high, old_low = previous[c.symbol]
            if c.pm_high > old_high:
                notes.append(f"NEW PMH (was {fmt_price(old_high)})")
            if c.pm_low < old_low:
                notes.append(f"new PML (was {fmt_price(old_low)})")
    if bull and c.last >= c.pm_high * 0.995:
        notes.append("pressing PMH")
    if not bull and c.last <= c.pm_low * 1.005:
        notes.append("pressing PML")
    return ", ".join(notes)


def movers_levels(movers: Movers) -> Levels:
    return {c.symbol: (c.pm_high, c.pm_low) for c in movers.bulls + movers.bears + movers.extras}


def format_movers(movers: Movers, day: dt.date, title: str, previous: Levels | None = None) -> tuple[str, str]:
    """Plain-text and HTML versions of the bulls / bears premarket report."""
    sections = [("BULLS - biggest gaps up (long setup: break over PMH)", movers.bulls, True, "#0a7d38"),
                ("BEARS - biggest gaps down (weak: watch a break under PML)", movers.bears, False, "#b42318")]
    if movers.extras:
        sections.append(("YOUR WATCHLIST", movers.extras, True, "#555555"))

    text = [f"{title} - {day:%a %b %d}", ""]
    html = [f'<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;font-size:14px;color:#111">',
            f"<h2 style=\"margin:0 0 4px\">{title}</h2><div style=\"color:#666\">{day:%A %B %d, %Y}</div>"]
    for heading, cands, bull, color in sections:
        text.append(heading)
        html.append(f'<h3 style="color:{color};margin:18px 0 6px">{heading}</h3>')
        if not cands:
            text += ["  none", ""]
            html.append('<div style="color:#666">none</div>')
            continue
        html.append('<table style="border-collapse:collapse;width:100%;max-width:640px">'
                    '<tr style="text-align:left;color:#666;font-size:12px">'
                    + "".join(f'<th style="padding:4px 8px;border-bottom:1px solid #ddd">{h}</th>'
                              for h in ("Ticker", "Price", "Gap", "PMH", "PML", "Change")) + "</tr>")
        for i, c in enumerate(cands, 1):
            gap = f"{c.gap_pct:+.1f}%" if c.gap_pct is not None else "n/a"
            note = _change_note(c, previous, bull)
            text.append(f"{i:>2}. {c.symbol:<6} ${fmt_price(c.last):<8} {gap:>8}   "
                        f"PMH {fmt_price(c.pm_high)} | PML {fmt_price(c.pm_low)}" + (f"   <- {note}" if note else ""))
            strong = "font-weight:600;" if "NEW PMH" in note or "pressing" in note else ""
            cells = [f"<b>{c.symbol}</b>", f"${fmt_price(c.last)}", f'<span style="color:{color}">{gap}</span>',
                     fmt_price(c.pm_high), fmt_price(c.pm_low), f'<span style="{strong}">{note}</span>']
            html.append("<tr>" + "".join(f'<td style="padding:4px 8px;border-bottom:1px solid #eee">{x}</td>'
                                         for x in cells) + "</tr>")
        html.append("</table>")
        text.append("")
    rules = ("Bulls: over PMH with VWAP under PMH = trade the PMH retest; VWAP over PMH = trade the VWAP retest. "
             "Sell at high of day.")
    text.append(rules)
    html.append(f'<p style="color:#666;font-size:12px;margin-top:18px">{rules}</p></div>')
    return "\n".join(text), "".join(html)
