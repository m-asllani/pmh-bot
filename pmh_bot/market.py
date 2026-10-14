"""US market clock: trading days, session times, and sleeping until a given ET time."""
from __future__ import annotations

import datetime as dt
import time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

PREMARKET_START = dt.time(4, 0)
OPEN = dt.time(9, 30)
CLOSE = dt.time(16, 0)

# NYSE/Nasdaq full-day closures
HOLIDAYS = {
    dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16), dt.date(2026, 4, 3),
    dt.date(2026, 5, 25), dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7),
    dt.date(2026, 11, 26), dt.date(2026, 12, 25),
    dt.date(2027, 1, 1), dt.date(2027, 1, 18), dt.date(2027, 2, 15), dt.date(2027, 3, 26),
    dt.date(2027, 5, 31), dt.date(2027, 6, 18), dt.date(2027, 7, 5), dt.date(2027, 9, 6),
    dt.date(2027, 11, 25), dt.date(2027, 12, 24),
}
# Half days: market closes at 13:00 ET
EARLY_CLOSES = {dt.date(2026, 11, 27), dt.date(2026, 12, 24), dt.date(2027, 11, 26)}


def now_et() -> dt.datetime:
    return dt.datetime.now(ET)


def is_trading_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS


def next_trading_day(day: dt.date) -> dt.date:
    """`day` itself if it is a trading day, otherwise the next one."""
    while not is_trading_day(day):
        day += dt.timedelta(days=1)
    return day


def close_time(day: dt.date) -> dt.time:
    return dt.time(13, 0) if day in EARLY_CLOSES else CLOSE


def at(day: dt.date, t: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, t, tzinfo=ET)


def parse_hhmm(value: str) -> dt.time:
    hours, minutes = str(value).split(":")
    return dt.time(int(hours), int(minutes))


def sleep_until(target: dt.datetime) -> None:
    """Sleep in short steps so the wake-up stays accurate even if the Mac slept in between."""
    while True:
        remaining = (target - now_et()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30))
