#!/usr/bin/env python3
"""Detect signal changes across strategies and notify via Telegram.

Usage (in GitHub Actions):
    python scripts/telegram_notify.py

Requires env vars:
    TELEGRAM_BOT_TOKEN  — Bot token from @BotFather
    TELEGRAM_CHAT_ID    — Channel/group chat ID

Detection logic:
    Compares current latest.json against the previous git commit's version.
    Only sends when current_signal.action changes to buy/sell.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

DATA_DIR = Path("data")
SITE_URL = "https://quant-strategy.mesh-hub.xyz"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def get_previous_signal(strategy_id: str) -> str | None:
    """Get the previous signal action from git HEAD~1."""
    path = f"data/{strategy_id}/latest.json"
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD~1:{path}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("current_signal", {}).get("action")
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_current_signal(strategy_id: str) -> dict | None:
    """Read current latest.json for a strategy."""
    path = DATA_DIR / strategy_id / "latest.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def format_message(strategy_name: str, slug: str,
                   old_action: str, new_action: str, signal_data: dict) -> str:
    """Format an HTML message for Telegram."""
    emoji = {"buy": "\U0001f7e2", "sell": "\U0001f534"}.get(new_action, "\U0001f4ca")
    price = signal_data.get("price", 0)
    symbol = signal_data.get("symbol", "")
    reason = signal_data.get("reason", "")

    lines = [
        f"{emoji} <b>{strategy_name}</b> — Signal Change",
        "",
        f"<code>{old_action.upper()} → {new_action.upper()}</code>",
        f"Symbol: {symbol}",
        f"Price: ${price:,.2f}" if price else "",
        f"Reason: {reason}" if reason else "",
        "",
        f'<a href="{SITE_URL}/strategy/{slug}">View Strategy →</a>',
    ]
    return "\n".join(line for line in lines if line is not None)


def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                print("[telegram] Message sent successfully")
                return True
            print(f"[telegram] Unexpected status: {resp.status}")
            return False
    except URLError as e:
        print(f"[telegram] Send failed: {e}")
        return False


def strategy_slug(strategy_id: str) -> str:
    """Convert strategy_id to URL slug (underscores to hyphens)."""
    return strategy_id.replace("_", "-")


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[notify] No credentials configured, skipping")
        sys.exit(0)

    strategy_dirs = [d for d in DATA_DIR.iterdir()
                     if d.is_dir() and (d / "latest.json").exists()
                     and d.name != "market"]

    changes = []
    for sdir in sorted(strategy_dirs):
        sid = sdir.name
        current = get_current_signal(sid)
        if not current:
            continue

        strategy_info = current.get("strategy", {})
        signal_info = current.get("current_signal", {})
        new_action = signal_info.get("action", "")
        old_action = get_previous_signal(sid)

        if old_action and old_action != new_action and new_action in ("buy", "sell"):
            signal_data = {
                "price": signal_info.get("price", 0),
                "symbol": strategy_info.get("symbol", ""),
                "reason": signal_info.get("reason", ""),
            }
            changes.append((strategy_info.get("name", sid), sid, old_action, new_action, signal_data))

    if not changes:
        print("[notify] No signal changes detected")
        return

    print(f"[notify] Detected {len(changes)} signal change(s)")
    for name, sid, old, new, signal_data in changes:
        slug = strategy_slug(sid)
        msg = format_message(name, slug, old, new, signal_data)
        send_telegram(msg)


if __name__ == "__main__":
    main()
