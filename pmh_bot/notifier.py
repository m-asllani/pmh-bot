"""Sends alerts to the terminal, macOS notifications, your phone (ntfy / Telegram), Discord and email."""
from __future__ import annotations

import logging
import platform
import smtplib
import subprocess
from email.message import EmailMessage

import requests

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: dict):
        self.cfg = cfg["notifications"]

    def send(self, title: str, body: str, urgent: bool = False, report: bool = False) -> None:
        """`report` marks the daily watchlist / levels / summary (vs. live trade alerts)."""
        n = self.cfg
        if n["console"]:
            print(f"\n=== {title} ===\n{body}\n", flush=True)
        if n["desktop"] and platform.system() == "Darwin":
            self._desktop(title, body)
        if n["ntfy_topic"]:
            self._post(n["ntfy_server"], json={
                "topic": n["ntfy_topic"], "title": title, "message": body,
                "priority": 5 if urgent else 3,
            })
        if n["telegram_bot_token"] and n["telegram_chat_id"]:
            self._post(f"https://api.telegram.org/bot{n['telegram_bot_token']}/sendMessage",
                       json={"chat_id": n["telegram_chat_id"], "text": f"{title}\n\n{body}"})
        if n["discord_webhook"]:
            self._post(n["discord_webhook"], json={"content": f"**{title}**\n{body}"[:2000]})
        if n["email_to"] and n["smtp_user"] and n["smtp_password"] and (report or not n["email_reports_only"]):
            self._email(title, body)

    def _email(self, title: str, body: str) -> None:
        n = self.cfg
        msg = EmailMessage()
        msg["Subject"] = f"[PMH bot] {title}"
        msg["From"] = n["email_from"] or n["smtp_user"]
        msg["To"] = n["email_to"]
        msg.set_content(body)
        try:
            with smtplib.SMTP(n["smtp_host"], int(n["smtp_port"]), timeout=20) as smtp:
                smtp.starttls()
                smtp.login(n["smtp_user"], n["smtp_password"])
                smtp.send_message(msg)
        except Exception as exc:
            log.warning("Email failed: %s", exc)

    @staticmethod
    def _desktop(title: str, body: str) -> None:
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{esc(body[:240])}" with title "{esc(title)}" sound name "Glass"'
        try:
            subprocess.run(["osascript", "-e", script], check=False, timeout=10, capture_output=True)
        except Exception as exc:
            log.debug("Desktop notification failed: %s", exc)

    @staticmethod
    def _post(url: str, **kwargs) -> None:
        try:
            resp = requests.post(url, timeout=10, **kwargs)
            if resp.status_code >= 300:
                log.warning("Notification to %s failed: %s %s", url.split("/bot")[0], resp.status_code, resp.text[:200])
        except Exception as exc:
            log.warning("Notification failed: %s", exc)
