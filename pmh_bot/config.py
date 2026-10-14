"""Loads config.yaml and fills in defaults for anything left out."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "schedule": {
        "scan_time": "09:15",
        "monitor_until": "12:00",
        "poll_seconds": 60,
    },
    "scanner": {
        "universe_size": 1000,
        "min_price": 1.0,
        "max_price": 100.0,
        "min_avg_volume": 500000,
        "min_gap_percent": 4.0,
        "min_premarket_active_minutes": 150,
        "max_stocks": 10,
        "watchlist_file": "watchlist.txt",
        "always_include_watchlist": True,
    },
    "strategy": {
        "vwap_includes_premarket": False,
        "touch_tolerance_percent": 0.3,
        "require_green_candle": True,
        "lose_pmh_percent": 1.0,
        "stop_buffer_percent": 0.5,
        "min_reward_percent": 1.0,
        "max_entries_per_stock": 2,
    },
    "notifications": {
        "console": True,
        "desktop": True,
        "ntfy_topic": "",
        "ntfy_server": "https://ntfy.sh",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "discord_webhook": "",
        "email_to": "",
        "email_from": "",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "email_reports_only": False,
        "send_breakouts": True,
        "send_failed": False,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


# Secrets can come from the environment (e.g. GitHub Actions secrets) instead of a file.
SECRET_ENV = {
    "PMH_NTFY_TOPIC": "ntfy_topic",
    "PMH_TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    "PMH_TELEGRAM_CHAT_ID": "telegram_chat_id",
    "PMH_DISCORD_WEBHOOK": "discord_webhook",
    "PMH_EMAIL_TO": "email_to",
    "PMH_SMTP_USER": "smtp_user",
    "PMH_SMTP_PASSWORD": "smtp_password",
}


def load_config(path: str | Path | None = None) -> dict:
    """config.yaml, then config.local.yaml (private, not committed), then PMH_* environment variables."""
    cfg = copy.deepcopy(DEFAULTS)
    files = [Path(path)] if path else [ROOT / "config.yaml", ROOT / "config.local.yaml"]
    for file in files:
        if file.exists():
            cfg = _merge(cfg, yaml.safe_load(file.read_text()) or {})
    for env, key in SECRET_ENV.items():
        if os.environ.get(env):
            cfg["notifications"][key] = os.environ[env]
    return cfg


def load_watchlist(cfg: dict) -> list[str]:
    path = ROOT / cfg["scanner"]["watchlist_file"]
    if not path.exists():
        return []
    symbols = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip().upper()
        if line:
            symbols.append(line)
    return symbols
