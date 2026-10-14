"""The PMH / VWAP retest strategy, fed one completed 1-minute bar at a time.

Rules:
  1. Premarket (4:00-9:30 ET) sets the premarket high (PMH) and premarket low (PML).
  2. After the open, a 1-minute close above PMH is the breakout.
  3. The retest level is whichever support is higher:
       VWAP under PMH -> trade the PMH retest
       VWAP over PMH  -> trade the VWAP retest
  4. Entry: a candle dips to the level and closes back above it (green, by default).
  5. Target: the high of day at entry ("sell at high of day"). Stop: just under the level / retest low.
  6. A close well back under PMH cancels the setup until it breaks out again.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .market import CLOSE, OPEN, PREMARKET_START

WATCHING, BROKE_OUT, IN_TRADE, DONE = "watching", "broke_out", "in_trade", "done"


def fmt_price(p: float) -> str:
    return f"{p:.4f}" if p < 1 else f"{p:.2f}"


@dataclass
class Signal:
    kind: str          # BREAKOUT, ENTRY, TARGET, STOP, FAILED
    symbol: str
    time: dt.datetime
    price: float
    text: str
    details: dict = field(default_factory=dict)


class Tracker:
    def __init__(self, symbol: str, strategy_cfg: dict, session_close: dt.time = CLOSE):
        self.symbol = symbol
        self.cfg = strategy_cfg
        self.session_close = session_close
        self.pm_high: float | None = None
        self.pm_low: float | None = None
        self.pm_volume = 0.0
        self.hod: float | None = None
        self.last_price: float | None = None
        self.last_ts: dt.datetime | None = None
        self._pv = 0.0
        self._vol = 0.0
        self.state = WATCHING
        self.entries = 0
        self.breakouts = 0
        self.entry = self.stop = self.target = None
        self.level_name = ""

    @property
    def vwap(self) -> float | None:
        return self._pv / self._vol if self._vol > 0 else self.last_price

    def plan(self) -> tuple[str, float | None]:
        """Which retest to trade right now, and at what price."""
        if self.pm_high is None:
            return "PMH", None
        vwap = self.vwap
        if vwap is not None and vwap > self.pm_high:
            return "VWAP", vwap
        return "PMH", self.pm_high

    def feed(self, ts: dt.datetime, o: float, h: float, low: float, c: float, volume: float) -> list[Signal]:
        if self.last_ts is not None and ts <= self.last_ts:
            return []
        t = ts.time()
        if t < PREMARKET_START or t >= self.session_close:
            return []
        self.last_ts = ts
        self.last_price = c
        premarket = t < OPEN

        if not premarket or self.cfg["vwap_includes_premarket"]:
            self._pv += (h + low + c) / 3 * volume
            self._vol += volume

        if premarket:
            self.pm_high = h if self.pm_high is None else max(self.pm_high, h)
            self.pm_low = low if self.pm_low is None else min(self.pm_low, low)
            self.pm_volume += volume
            self.hod = self.pm_high
            return []

        if self.pm_high is None:  # no premarket trading -> no levels to trade
            return []
        signals = self._step(ts, o, h, low, c)
        self.hod = h if self.hod is None else max(self.hod, h)
        return signals

    def _step(self, ts, o, h, low, c) -> list[Signal]:
        cfg = self.cfg
        pmh = self.pm_high
        vwap = self.vwap
        level_name, level = self.plan()
        sym = self.symbol

        if self.state == WATCHING:
            if c > pmh:
                self.state = BROKE_OUT
                self.breakouts += 1
                side = "ABOVE" if level_name == "VWAP" else "below"
                return [Signal("BREAKOUT", sym, ts, c,
                               f"{sym} broke the premarket high {fmt_price(pmh)} (now {fmt_price(c)}).\n"
                               f"VWAP {fmt_price(vwap)} is {side} PMH -> wait for the {level_name} retest "
                               f"near {fmt_price(level)}.",
                               {"pmh": pmh, "vwap": vwap, "plan": level_name, "count": self.breakouts})]

        elif self.state == BROKE_OUT:
            if c < pmh * (1 - cfg["lose_pmh_percent"] / 100):
                self.state = WATCHING
                return [Signal("FAILED", sym, ts, c,
                               f"{sym} fell back under PMH {fmt_price(pmh)} (now {fmt_price(c)}). Setup reset.")]
            touched = low <= level * (1 + cfg["touch_tolerance_percent"] / 100)
            held = c >= level and (c > o or not cfg["require_green_candle"])
            if touched and held:
                target = self.hod
                stop = min(low, level) * (1 - cfg["stop_buffer_percent"] / 100)
                if target < c * (1 + cfg["min_reward_percent"] / 100):
                    return []  # already sitting at the high, no room to the target
                self.state, self.entry, self.stop, self.target = IN_TRADE, c, stop, target
                self.level_name = level_name
                self.entries += 1
                reward_risk = (target - c) / (c - stop) if c > stop else 0
                return [Signal("ENTRY", sym, ts, c,
                               f"BUY {sym} - {level_name} retest held at {fmt_price(level)}.\n"
                               f"Entry ~{fmt_price(c)} | Stop {fmt_price(stop)} | "
                               f"Target (HOD) {fmt_price(target)} | R:R {reward_risk:.1f}",
                               {"level": level_name, "entry": c, "stop": stop, "target": target})]

        elif self.state == IN_TRADE:
            if low <= self.stop:
                return self._exit(ts, c, "STOP", self.stop,
                                  f"STOP {sym} at {fmt_price(self.stop)} (entry {fmt_price(self.entry)}). "
                                  f"{self.level_name} retest failed.")
            if h >= self.target:
                gain = (self.target / self.entry - 1) * 100
                return self._exit(ts, c, "TARGET", self.target,
                                  f"SELL {sym} - hit high of day {fmt_price(self.target)} "
                                  f"(entry {fmt_price(self.entry)}, +{gain:.1f}%).")
        return []

    def _exit(self, ts, close, kind, price, text) -> list[Signal]:
        signal = Signal(kind, self.symbol, ts, price, text, {"entry": self.entry})
        if self.entries >= self.cfg["max_entries_per_stock"]:
            self.state = DONE
        else:
            self.state = BROKE_OUT if close > self.pm_high else WATCHING
        self.entry = self.stop = self.target = None
        return [signal]
