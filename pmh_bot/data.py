"""Market data from Yahoo Finance (free, includes premarket 1-minute bars)."""
from __future__ import annotations

import datetime as dt
import logging
import time
import warnings

import pandas as pd
import yfinance as yf

from .market import ET

warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
log = logging.getLogger(__name__)

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame[[c for c in COLUMNS if c in frame.columns]].dropna(subset=["Open", "High", "Low", "Close"])
    if frame.empty:
        return frame
    idx = frame.index
    frame.index = (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(ET)
    frame = frame[frame.index.second == 0]  # drop the partial "live tick" rows Yahoo appends
    frame["Volume"] = frame["Volume"].fillna(0)
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def _split(frame: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    if frame is None or frame.empty:
        return out
    if isinstance(frame.columns, pd.MultiIndex):
        available = set(frame.columns.get_level_values(0))
        for sym in symbols:
            if sym in available:
                sub = _clean(frame[sym].copy())
                if not sub.empty:
                    out[sym] = sub
    elif len(symbols) == 1:
        sub = _clean(frame.copy())
        if not sub.empty:
            out[symbols[0]] = sub
    return out


def _download(symbols: list[str], attempts: int = 3, **kwargs) -> pd.DataFrame:
    """yf.download with retries: Yahoo occasionally returns an empty frame for a valid request."""
    for attempt in range(1, attempts + 1):
        try:
            frame = yf.download(symbols, progress=False, group_by="ticker", threads=True,
                                auto_adjust=False, **kwargs)
            if frame is not None and not frame.dropna(how="all").empty:
                return frame
        except Exception as exc:  # network hiccups should never kill the bot
            log.warning("Download failed for %d symbols (attempt %d): %s", len(symbols), attempt, exc)
        if attempt < attempts:
            time.sleep(2 * attempt)
    return pd.DataFrame()


def intraday_bars(symbols: list[str], day: dt.date, chunk: int = 200) -> dict[str, pd.DataFrame]:
    """1-minute bars (premarket + regular + after hours) for `day`, indexed in ET."""
    out: dict[str, pd.DataFrame] = {}
    live = (dt.date.today() - day).days <= 1
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        if live:
            frame = _download(part, period="2d", interval="1m", prepost=True)
        else:
            frame = _download(part, start=day.isoformat(), end=(day + dt.timedelta(days=1)).isoformat(),
                              interval="1m", prepost=True)
        for sym, bars in _split(frame, part).items():
            bars = bars[bars.index.date == day]
            if not bars.empty:
                out[sym] = bars
    return out


def previous_closes(symbols: list[str], day: dt.date) -> dict[str, float]:
    """Regular-session close of the trading day before `day`."""
    out: dict[str, float] = {}
    if not symbols:
        return out
    frame = _download(symbols, start=(day - dt.timedelta(days=10)).isoformat(), end=day.isoformat(), interval="1d")
    if frame is None or frame.empty:
        return out
    for sym in symbols:
        try:
            sub = frame[sym] if isinstance(frame.columns, pd.MultiIndex) else frame
            closes = sub["Close"].dropna()
            closes = closes[closes.index.date < day]
            if not closes.empty:
                out[sym] = float(closes.iloc[-1])
        except KeyError:
            continue
    return out


_universe_cache: dict[dt.date, list[str]] = {}


def liquid_universe(cfg: dict) -> list[str]:
    """US-listed stocks in the configured price range with real average volume, most active first.

    Fetched once per day (it barely changes) and retried, since Yahoo rate-limits the screener.
    """
    today = dt.date.today()
    if today in _universe_cache:
        return _universe_cache[today]
    for attempt, wait in enumerate((0, 30, 90), 1):
        time.sleep(wait)
        symbols = _screen_universe(cfg)
        if symbols:
            _universe_cache[today] = symbols
            return symbols
        log.warning("Universe screener returned nothing (attempt %d of 3).", attempt)
    log.warning("Scanning the watchlist only.")
    return []


def _screen_universe(cfg: dict) -> list[str]:
    sc = cfg["scanner"]
    query = yf.EquityQuery("and", [
        yf.EquityQuery("is-in", ["exchange", "NMS", "NYQ", "NGM", "NCM", "ASE"]),
        yf.EquityQuery("btwn", ["intradayprice", sc["min_price"], sc["max_price"]]),
        yf.EquityQuery("gt", ["avgdailyvol3m", sc["min_avg_volume"]]),
    ])
    symbols: list[str] = []
    try:
        for offset in range(0, int(sc["universe_size"]), 250):
            result = yf.screen(query, offset=offset, size=250, sortField="dayvolume", sortAsc=False)
            quotes = result.get("quotes", [])
            symbols += [q["symbol"] for q in quotes if "symbol" in q]
            if len(quotes) < 250:
                break
    except Exception as exc:
        log.warning("Universe screener failed: %s", exc)
        return []
    return symbols[: int(sc["universe_size"])]
