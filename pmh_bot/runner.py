"""Daily schedule: premarket scan -> levels locked at the open -> live monitoring -> summary."""
from __future__ import annotations

import contextlib
import csv
import datetime as dt
import logging
import os
import platform
import subprocess

from . import data
from .config import ROOT
from .market import (OPEN, PREMARKET_START, at, close_time, next_trading_day, now_et, parse_hhmm,
                     sleep_until)
from .notifier import Notifier
from .scanner import (Candidate, Levels, format_movers, format_watchlist, movers_levels, premarket_stats,
                      scan_movers)
from .strategy import Signal, Tracker, fmt_price

log = logging.getLogger(__name__)
SIGNALS_CSV = ROOT / "logs" / "signals.csv"
FRESH = dt.timedelta(minutes=3)

TITLES = {
    "BREAKOUT": "{sym} broke PMH",
    "ENTRY": "BUY {sym} - retest held",
    "TARGET": "SELL {sym} - high of day hit",
    "STOP": "STOP {sym}",
    "FAILED": "{sym} lost PMH",
}


def session_end(cfg: dict, day: dt.date) -> dt.datetime:
    until = parse_hhmm(cfg["schedule"]["monitor_until"])
    return at(day, min(until, close_time(day)))


@contextlib.contextmanager
def keep_awake():
    """Stop the Mac from idle-sleeping while a session is running."""
    proc = None
    if platform.system() == "Darwin":
        with contextlib.suppress(Exception):
            proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    try:
        yield
    finally:
        if proc:
            proc.terminate()


def record(signal: Signal, replay: bool = False) -> None:
    SIGNALS_CSV.parent.mkdir(exist_ok=True)
    new_file = not SIGNALS_CSV.exists()
    with SIGNALS_CSV.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(["time_et", "symbol", "signal", "price", "replay", "message"])
        writer.writerow([signal.time.strftime("%Y-%m-%d %H:%M"), signal.symbol, signal.kind,
                         fmt_price(signal.price), replay, signal.text.replace("\n", " ")])


def should_send(cfg: dict, signal: Signal) -> bool:
    n = cfg["notifications"]
    if signal.kind == "BREAKOUT":
        return n["send_breakouts"] and signal.details.get("count", 1) == 1  # no spam when it chops around PMH
    if signal.kind == "FAILED":
        return n["send_failed"]
    return True


def notify_signal(notifier: Notifier, signal: Signal) -> None:
    title = TITLES[signal.kind].format(sym=signal.symbol)
    body = f"{signal.time:%H:%M} ET\n{signal.text}"
    notifier.send(title, body, urgent=signal.kind in ("ENTRY", "TARGET", "STOP"))


def feed_bars(tracker: Tracker, frame, cutoff: dt.datetime | None = None) -> list[Signal]:
    if cutoff is not None:
        frame = frame[frame.index <= cutoff]
    if tracker.last_ts is not None:
        frame = frame[frame.index > tracker.last_ts]
    signals = []
    for ts, row in frame.iterrows():
        signals += tracker.feed(ts.to_pydatetime(), float(row["Open"]), float(row["High"]),
                                float(row["Low"]), float(row["Close"]), float(row["Volume"]))
    return signals


def summarize(signals: list[Signal], day: dt.date) -> str:
    trades, open_entries = [], {}
    for s in signals:
        if s.kind == "ENTRY":
            open_entries[s.symbol] = s
        elif s.kind in ("TARGET", "STOP") and s.symbol in open_entries:
            e = open_entries.pop(s.symbol)
            pct = (s.price / e.price - 1) * 100
            trades.append(f"{s.symbol}: {e.time:%H:%M} buy {fmt_price(e.price)} -> "
                          f"{s.time:%H:%M} {s.kind.lower()} {fmt_price(s.price)}  ({pct:+.1f}%)")
    for sym, e in open_entries.items():
        trades.append(f"{sym}: {e.time:%H:%M} buy {fmt_price(e.price)} -> still open")
    breakouts = sorted({s.symbol for s in signals if s.kind == "BREAKOUT"})
    lines = [f"Session summary - {day:%a %b %d}",
             f"Broke PMH: {', '.join(breakouts) if breakouts else 'none'}"]
    lines += ["Trades:"] + (trades or ["none"])
    return "\n".join(lines)


def refresh_levels(cands: list[Candidate], day: dt.date) -> None:
    bars = data.intraday_bars([c.symbol for c in cands], day)
    for c in cands:
        stats = bars.get(c.symbol) is not None and premarket_stats(bars[c.symbol])
        if stats:
            c.pm_high, c.pm_low, c.active_minutes, c.last = (
                stats["pm_high"], stats["pm_low"], stats["active_minutes"], stats["last"])
            if c.prev_close:
                c.gap_pct = (c.last / c.prev_close - 1) * 100


def monitor(cfg: dict, day: dt.date, symbols: list[str], end_at: dt.datetime, notifier: Notifier) -> None:
    trackers = {s: Tracker(s, cfg["strategy"], close_time(day)) for s in symbols}
    all_signals: list[Signal] = []
    poll = max(15, int(cfg["schedule"]["poll_seconds"]))
    log.info("Monitoring %s until %s ET", ", ".join(symbols), end_at.strftime("%H:%M"))

    while now_et() < end_at:
        bars = data.intraday_bars(symbols, day)
        now = now_et()
        for sym, frame in bars.items():
            for signal in feed_bars(trackers[sym], frame, cutoff=now - dt.timedelta(minutes=1)):
                all_signals.append(signal)
                record(signal)
                fresh = now - signal.time <= FRESH
                if fresh and should_send(cfg, signal):
                    notify_signal(notifier, signal)
                else:
                    log.info("(catch-up, not sent) %s %s", signal.kind, signal.symbol)
        # wake a few seconds after the next bar closes
        nxt = (now + dt.timedelta(seconds=poll)).replace(second=5, microsecond=0)
        sleep_until(min(nxt if nxt > now else now + dt.timedelta(seconds=poll), end_at))

    notifier.send("Session summary", summarize(all_signals, day), report=True)


def premarket_reports(cfg: dict, day: dt.date, notifier: Notifier, until: dt.datetime) -> Levels | None:
    """Bulls & bears reports at each configured time before the scan. Returns the last levels sent."""
    previous = None
    for t in sorted(parse_hhmm(x) for x in cfg["schedule"]["premarket_reports"]):
        when = at(day, t)
        if when >= until or now_et() > when + dt.timedelta(minutes=10):
            continue
        sleep_until(when)
        with keep_awake():
            movers = scan_movers(cfg, day)
        text, html = format_movers(movers, day, f"PREMARKET REPORT {t:%H:%M} ET", previous)
        notifier.send(f"Premarket bulls & bears {t:%H:%M} ET", text, report=True, html=html)
        previous = movers_levels(movers)
    return previous


def run_session(cfg: dict, day: dt.date, notifier: Notifier, wait_for_scan: bool = True) -> None:
    scan_at = at(day, parse_hhmm(cfg["schedule"]["scan_time"]))
    open_at = at(day, OPEN)
    end_at = session_end(cfg, day)

    previous = None
    if wait_for_scan:
        log.info("Next session %s. Scan at %s ET (%s your time).", day, scan_at.strftime("%H:%M"),
                 scan_at.astimezone().strftime("%H:%M"))
        previous = premarket_reports(cfg, day, notifier, until=scan_at)
        sleep_until(scan_at)
    if now_et() >= end_at:
        log.info("Session for %s already over.", day)
        return

    with keep_awake():
        movers = scan_movers(cfg, day)
        text, html = format_movers(movers, day, "FINAL PREMARKET REPORT", previous)
        notifier.send("Premarket bulls & bears + watchlist", text, report=True, html=html)
        cands = movers.bulls + movers.extras
        if not cands:
            return
        if now_et() < open_at:
            sleep_until(open_at + dt.timedelta(seconds=20))
            refresh_levels(cands, day)
            notifier.send("Levels locked at the open", format_watchlist(cands, day, "FINAL PMH / PML AT THE OPEN"),
                          report=True)
        monitor(cfg, day, [c.symbol for c in cands], end_at, notifier)


def run_forever(cfg: dict, notifier: Notifier) -> None:
    log.info("PMH bot started. Times are US Eastern; it sleeps between sessions.")
    while True:
        now = now_et()
        day = next_trading_day(now.date())
        if now >= session_end(cfg, day):
            day = next_trading_day(now.date() + dt.timedelta(days=1))
        try:
            run_session(cfg, day, notifier)
        except Exception:
            log.exception("Session crashed; retrying in 60s")
            sleep_until(now_et() + dt.timedelta(seconds=60))
            continue
        # don't loop on the same day again
        sleep_until(max(now_et(), session_end(cfg, day)) + dt.timedelta(seconds=5))


def run_today(cfg: dict, notifier: Notifier) -> None:
    """One session for today, then exit (for schedulers like GitHub Actions or cron)."""
    today = now_et().date()
    if today != next_trading_day(today):
        log.info("%s is not a US trading day. Nothing to do.", today)
        return
    run_session(cfg, today, notifier)


def run_now(cfg: dict, notifier: Notifier) -> None:
    now = now_et()
    if now.date() != next_trading_day(now.date()):
        print("The US market is closed today. Use `replay` to test on a past day.")
        return
    if now.time() < PREMARKET_START:
        print("Premarket hasn't started yet (4:00 ET). Use `run` to wait for it.")
        return
    run_session(cfg, now.date(), notifier, wait_for_scan=False)


def replay(cfg: dict, day: dt.date, notifier: Notifier, symbols: list[str] | None, send: bool) -> None:
    """Run the full day (scan + strategy) on historical bars to see what the bot would have sent."""
    scan_at = at(day, parse_hhmm(cfg["schedule"]["scan_time"]))
    if symbols:
        bars = data.intraday_bars(symbols, day)
        prev = data.previous_closes(symbols, day)
        cands = []
        for sym in symbols:
            s = bars.get(sym) is not None and premarket_stats(bars[sym])
            if s:
                pc = prev.get(sym)
                cands.append(Candidate(sym, s["last"], pc, (s["last"] / pc - 1) * 100 if pc else None,
                                       s["pm_high"], s["pm_low"], s["active_minutes"], True))
    out = (lambda title, body, html="": notifier.send(title, body, report=True, html=html)) if send else (
        lambda title, body, html="": print(f"\n=== {title} ===\n{body}\n"))
    if symbols:
        out("Premarket watchlist (replay)", format_watchlist(cands, day, f"WATCHLIST AS OF {scan_at:%H:%M} ET"))
    else:
        movers = scan_movers(cfg, day, as_of=scan_at)
        cands = movers.bulls + movers.extras
        out("Premarket bulls & bears (replay)",
            *format_movers(movers, day, f"PREMARKET REPORT AS OF {scan_at:%H:%M} ET"))
    if not cands:
        return

    end_at = session_end(cfg, day)
    bars = data.intraday_bars([c.symbol for c in cands], day)
    signals = []
    for c in cands:
        if c.symbol in bars:
            tracker = Tracker(c.symbol, cfg["strategy"], close_time(day))
            signals += feed_bars(tracker, bars[c.symbol], cutoff=end_at - dt.timedelta(minutes=1))
    signals.sort(key=lambda s: s.time)
    for s in signals:
        record(s, replay=True)
        if should_send(cfg, s):
            if send:
                notify_signal(notifier, s)
            else:
                print(f"[{s.time:%H:%M}] {TITLES[s.kind].format(sym=s.symbol)}\n    "
                      + s.text.replace("\n", "\n    "))
    out("Session summary (replay)", summarize(signals, day))
