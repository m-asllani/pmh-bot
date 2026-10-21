"""Command line: python -m pmh_bot <run|now|scan|replay|test-notify>"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import subprocess
import sys

from .config import ROOT, load_config
from .market import at, next_trading_day, now_et, parse_hhmm
from .notifier import Notifier
from .runner import replay, run_cloud, run_forever, run_now, run_today
from .scanner import format_movers, scan_movers


def setup_logging() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(ROOT / "logs" / "pmh_bot.log")],
    )


def start_next_job() -> None:
    """Start a fresh run of this workflow. Manual starts aren't delayed like GitHub's schedule."""
    workflow = os.environ.get("PMH_WORKFLOW", "pmh-bot.yml")
    subprocess.run(["gh", "workflow", "run", workflow, "--repo", os.environ["GITHUB_REPOSITORY"],
                    "-f", "command=cloud"], check=True)
    logging.info("Started the next job.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pmh_bot", description="Premarket high / VWAP retest alert bot")
    parser.add_argument("--config", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run every trading day automatically (leave this running)")
    sub.add_parser("today", help="wait for today's scan time, run the session, then exit (for schedulers)")
    p_cloud = sub.add_parser("cloud", help="GitHub Actions entry point: run the next session, relaying across jobs")
    p_cloud.add_argument("--relay-now", action="store_true", help="test: hand off to a fresh job immediately")
    sub.add_parser("now", help="scan right now and monitor today's session")
    p_scan = sub.add_parser("scan", help="scan premarket gappers once and print them")
    p_scan.add_argument("--send", action="store_true", help="also send the list to your notifications")
    p_replay = sub.add_parser("replay", help="simulate a past day to see what alerts it would have sent")
    p_replay.add_argument("--date", help="YYYY-MM-DD (default: most recent trading day)")
    p_replay.add_argument("--symbols", nargs="*", help="skip the scan and replay these tickers")
    p_replay.add_argument("--send", action="store_true", help="send the replayed alerts to your notifications")
    sub.add_parser("test-notify", help="send a test notification to every configured channel")
    args = parser.parse_args()

    setup_logging()
    cfg = load_config(args.config)
    notifier = Notifier(cfg)

    if args.command == "run":
        run_forever(cfg, notifier)
    elif args.command == "cloud":
        if args.relay_now:
            start_next_job()
        else:
            run_cloud(cfg, notifier, relay=start_next_job)
    elif args.command == "today":
        run_today(cfg, notifier)
    elif args.command == "now":
        run_now(cfg, notifier)
    elif args.command == "scan":
        day = next_trading_day(now_et().date())
        text, html = format_movers(scan_movers(cfg, day), day, f"PREMARKET REPORT {now_et():%H:%M} ET")
        if args.send:
            notifier.send("Premarket bulls & bears", text, report=True, html=html)
        else:
            print(text)
    elif args.command == "replay":
        if args.date:
            day = dt.date.fromisoformat(args.date)
        else:
            day = now_et().date()
            if now_et() < at(day, parse_hhmm(cfg["schedule"]["monitor_until"])) or day != next_trading_day(day):
                day -= dt.timedelta(days=1)
                while day != next_trading_day(day):
                    day -= dt.timedelta(days=1)
        replay(cfg, day, notifier, [s.upper() for s in args.symbols] if args.symbols else None, args.send)
    elif args.command == "test-notify":
        notifier.send("PMH bot test", "Notifications are working. You'll get the watchlist before the open "
                                      "and BUY / SELL alerts during the session.", urgent=True)


if __name__ == "__main__":
    main()
