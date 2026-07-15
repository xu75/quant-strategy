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
SUBSCRIBERS_FILE = Path("config/subscribers.json")
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


def get_previous_regime(strategy_id: str) -> str | None:
    """Get the previous regime from git HEAD~1."""
    path = f"data/{strategy_id}/latest.json"
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD~1:{path}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("current_signal", {}).get("regime")
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
    """Format an HTML message for Telegram (action change)."""
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


def format_regime_message(strategy_name: str, slug: str,
                          old_regime: str, new_regime: str, signal_data: dict) -> str:
    """Format an HTML message for Telegram (regime change)."""
    regime_emoji = {"bull": "📈", "bear": "📉", "neutral": "📊"}
    emoji = regime_emoji.get(new_regime, "🔄")
    price = signal_data.get("price", 0)
    symbol = signal_data.get("symbol", "")
    exposure = signal_data.get("exposure", None)

    lines = [
        f"{emoji} <b>{strategy_name}</b> — Regime Change",
        "",
        f"<code>{old_regime.upper()} → {new_regime.upper()}</code>",
        f"Symbol: {symbol}",
        f"Price: ${price:,.2f}" if price else "",
        f"Exposure: {exposure:.1%}" if exposure is not None else "",
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


def load_subscribers() -> list[dict]:
    """Load webhook subscribers from config file."""
    if not SUBSCRIBERS_FILE.exists():
        return []
    try:
        with open(SUBSCRIBERS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def format_webhook_json(strategy_name: str, slug: str,
                        old_action: str, new_action: str, signal_data: dict) -> dict:
    """Format a JSON payload for generic webhooks (action change)."""
    return {
        "event": "signal_change",
        "strategy": strategy_name,
        "symbol": signal_data.get("symbol", ""),
        "old_signal": old_action,
        "new_signal": new_action,
        "price": signal_data.get("price", 0),
        "reason": signal_data.get("reason", ""),
        "url": f"{SITE_URL}/strategy/{slug}",
    }


def format_webhook_json_regime(strategy_name: str, slug: str,
                               old_regime: str, new_regime: str, signal_data: dict) -> dict:
    """Format a JSON payload for generic webhooks (regime change)."""
    return {
        "event": "regime_change",
        "strategy": strategy_name,
        "symbol": signal_data.get("symbol", ""),
        "old_regime": old_regime,
        "new_regime": new_regime,
        "price": signal_data.get("price", 0),
        "exposure": signal_data.get("exposure"),
        "url": f"{SITE_URL}/strategy/{slug}",
    }


def format_webhook_text(strategy_name: str, slug: str,
                        old_action: str, new_action: str, signal_data: dict) -> str:
    """Format a plain text message."""
    price = signal_data.get("price", 0)
    symbol = signal_data.get("symbol", "")
    return (
        f"{strategy_name} | {old_action.upper()} → {new_action.upper()} | "
        f"{symbol} ${price:,.2f} | {SITE_URL}/strategy/{slug}"
    )


def format_webhook_discord(strategy_name: str, slug: str,
                           old_action: str, new_action: str, signal_data: dict) -> dict:
    """Format a Discord webhook payload."""
    color = 0x00FF00 if new_action == "buy" else 0xFF0000
    price = signal_data.get("price", 0)
    symbol = signal_data.get("symbol", "")
    return {
        "embeds": [{
            "title": f"{strategy_name} — Signal Change",
            "description": f"**{old_action.upper()} → {new_action.upper()}**",
            "color": color,
            "fields": [
                {"name": "Symbol", "value": symbol, "inline": True},
                {"name": "Price", "value": f"${price:,.2f}", "inline": True},
                {"name": "Reason", "value": signal_data.get("reason", "—"), "inline": False},
            ],
            "url": f"{SITE_URL}/strategy/{slug}",
        }],
    }


def format_webhook_bark(strategy_name: str, slug: str,
                        old_action: str, new_action: str, signal_data: dict) -> None:
    """Bark uses URL path, returns None — URL is constructed at send time."""
    return None


def send_webhook(subscriber: dict, strategy_name: str, slug: str,
                 old_val: str, new_val: str, signal_data: dict,
                 event_type: str = "signal") -> bool:
    """Send notification to a single webhook subscriber.

    event_type: "signal" (buy/sell action change) or "regime" (regime change)
    """
    sub_url = subscriber["url"]
    fmt = subscriber.get("format", "json")

    try:
        if fmt == "bark":
            title = f"{strategy_name} {'Signal' if event_type == 'signal' else 'Regime'}"
            body = f"{old_val.upper()} → {new_val.upper()} | {signal_data.get('symbol', '')} ${signal_data.get('price', 0):,.2f}"
            bark_url = f"{sub_url.rstrip('/')}/{title}/{body}"
            req = Request(bark_url, method="GET")
            with urlopen(req, timeout=10) as resp:
                return resp.status == 200
        elif fmt == "discord":
            payload = format_webhook_discord(strategy_name, slug, old_val, new_val, signal_data)
        elif fmt == "text":
            text = format_webhook_text(strategy_name, slug, old_val, new_val, signal_data)
            payload = {"text": text, "content": text}
        else:
            if event_type == "regime":
                payload = format_webhook_json_regime(strategy_name, slug, old_val, new_val, signal_data)
            else:
                payload = format_webhook_json(strategy_name, slug, old_val, new_val, signal_data)

        data = json.dumps(payload).encode()
        req = Request(sub_url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            return resp.status < 300
    except (URLError, OSError) as e:
        print(f"[webhook] Failed for {subscriber.get('id', '?')}: {e}")
        return False


def main():
    has_telegram = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    subscribers = load_subscribers()

    if not has_telegram and not subscribers:
        print("[notify] No channels configured, skipping")
        sys.exit(0)

    channels = []
    if has_telegram:
        channels.append("Telegram")
    if subscribers:
        channels.append(f"Webhooks({len(subscribers)})")
    print(f"[notify] Active channels: {', '.join(channels)}")

    strategy_dirs = [d for d in DATA_DIR.iterdir()
                     if d.is_dir() and (d / "latest.json").exists()
                     and d.name != "market"]

    # Each entry: (name, sid, old_val, new_val, signal_data, event_type)
    notifications = []

    for sdir in sorted(strategy_dirs):
        sid = sdir.name
        current = get_current_signal(sid)
        if not current:
            continue

        strategy_info = current.get("strategy", {})
        signal_info = current.get("current_signal", {})
        strategy_name = strategy_info.get("name", sid)

        signal_data_base = {
            "price": signal_info.get("price", 0),
            "symbol": strategy_info.get("symbol", ""),
        }

        # --- Action change (buy/sell) ---
        new_action = signal_info.get("action", "")
        old_action = get_previous_signal(sid)
        if old_action and old_action != new_action and new_action in ("buy", "sell"):
            signal_data = {**signal_data_base, "reason": signal_info.get("reason", "")}
            notifications.append((strategy_name, sid, old_action, new_action, signal_data, "signal"))

        # --- Regime change ---
        new_regime = signal_info.get("regime")
        if new_regime:
            old_regime = get_previous_regime(sid)
            if old_regime and old_regime != new_regime:
                signal_data = {
                    **signal_data_base,
                    "exposure": signal_info.get("target_exposure"),
                }
                notifications.append((strategy_name, sid, old_regime, new_regime, signal_data, "regime"))

    if not notifications:
        print("[notify] No signal or regime changes detected")
        return

    print(f"[notify] Detected {len(notifications)} notification(s)")
    for name, sid, old_val, new_val, signal_data, event_type in notifications:
        slug = strategy_slug(sid)
        if has_telegram:
            if event_type == "regime":
                msg = format_regime_message(name, slug, old_val, new_val, signal_data)
            else:
                msg = format_message(name, slug, old_val, new_val, signal_data)
            send_telegram(msg)
        for sub in subscribers:
            send_webhook(sub, name, slug, old_val, new_val, signal_data, event_type)


if __name__ == "__main__":
    main()
