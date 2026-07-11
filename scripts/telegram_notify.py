#!/usr/bin/env python3
"""Detect signal changes across strategies and notify via Telegram.

Usage (in GitHub Actions):
    python scripts/telegram_notify.py

Requires env vars:
    TELEGRAM_BOT_TOKEN  — Bot token from @BotFather
    TELEGRAM_CHAT_ID    — Channel/group chat ID

Detection logic:
    Compares current latest.json against the previous git commit's version.

    Primary: in_position change (False→True = buy, True→False = sell).
    Catches entries that happen between daily runs — the 'action' field is
    only 'buy'/'sell' for the single crossover bar; after that it becomes
    'hold'. in_position persists for the entire trade, so daily diffs are
    always visible.

    Secondary: ETF rotation (current_etf change with rotation_reason present).

    Fallback: action field change to buy/sell, for strategies without
    position tracking.
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


def get_previous_signal_data(strategy_id: str) -> dict | None:
    """Get the previous signal snapshot from git HEAD~1.

    Returns a flat dict with keys: action, in_position, current_etf, holding.
    Returns None if HEAD~1 does not contain the file.
    """
    path = f"data/{strategy_id}/latest.json"
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD~1:{path}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        signal = data.get("current_signal", {})
        position = data.get("position", {})
        return {
            "action": signal.get("action"),
            "in_position": position.get("in_position"),
            "current_etf": signal.get("current_etf"),
            "holding": signal.get("holding"),
        }
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


# Keep for backward compatibility — thin wrapper around get_previous_signal_data.
def get_previous_signal(strategy_id: str) -> str | None:
    """Get the previous signal action from git HEAD~1."""
    prev = get_previous_signal_data(strategy_id)
    return prev.get("action") if prev else None


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


def format_rotation_message(strategy_name: str, slug: str,
                            old_etf: str, new_etf: str, signal_data: dict) -> str:
    """Format an HTML message for ETF rotation events."""
    old_name = signal_data.get("old_holding", old_etf)
    new_name = signal_data.get("holding", new_etf)
    rotation_reason = signal_data.get("rotation_reason")

    lines = [
        f"\U0001f504 <b>{strategy_name}</b> — ETF Rotation",
        "",
        f"<code>{old_etf} → {new_etf}</code>",
        f"{old_name} → {new_name}",
    ]

    if rotation_reason:
        old_z = rotation_reason.get("old_zscore", 0)
        new_z = rotation_reason.get("new_zscore", 0)
        z_diff = rotation_reason.get("zscore_diff", 0)
        z_thresh = rotation_reason.get("zscore_threshold", 0)
        old_p = rotation_reason.get("old_premium", 0)
        new_p = rotation_reason.get("new_premium", 0)
        p_diff = rotation_reason.get("premium_diff", 0)
        p_thresh = rotation_reason.get("premium_threshold", 0)
        hold_days = rotation_reason.get("holding_days", 0)
        hold_thresh = rotation_reason.get("holding_days_threshold", 0)

        lines += [
            "",
            f"Z-Score: {old_z:.2f} → {new_z:.2f}  差值 {z_diff:.2f} > 阈值 {z_thresh}",
            f"Premium: {old_p * 100:.4f}% → {new_p * 100:.4f}%  差值 {p_diff * 100:.4f}% > 阈值 {p_thresh * 100:.4f}%",
            f"持有天数: {hold_days} ≥ {hold_thresh}",
        ]

    lines += [
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
    """Format a JSON payload for generic webhooks."""
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


def format_rotation_webhook_json(strategy_name: str, slug: str,
                                 old_etf: str, new_etf: str, signal_data: dict) -> dict:
    """Format a JSON payload for ETF rotation webhook events."""
    rotation_reason = signal_data.get("rotation_reason", {})
    return {
        "event": "etf_rotation",
        "strategy": strategy_name,
        "old_etf": old_etf,
        "new_etf": new_etf,
        "old_name": rotation_reason.get("old_name", signal_data.get("old_holding", "")),
        "new_name": rotation_reason.get("new_name", signal_data.get("holding", "")),
        "old_zscore": rotation_reason.get("old_zscore"),
        "new_zscore": rotation_reason.get("new_zscore"),
        "zscore_diff": rotation_reason.get("zscore_diff"),
        "zscore_threshold": rotation_reason.get("zscore_threshold"),
        "old_premium": rotation_reason.get("old_premium"),
        "new_premium": rotation_reason.get("new_premium"),
        "premium_diff": rotation_reason.get("premium_diff"),
        "premium_threshold": rotation_reason.get("premium_threshold"),
        "holding_days": rotation_reason.get("holding_days"),
        "holding_days_threshold": rotation_reason.get("holding_days_threshold"),
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
                 old_action: str, new_action: str, signal_data: dict) -> bool:
    """Send notification to a single webhook subscriber."""
    sub_url = subscriber["url"]
    fmt = subscriber.get("format", "json")

    try:
        if fmt == "bark":
            title = f"{strategy_name} Signal"
            body = f"{old_action.upper()} → {new_action.upper()} | {signal_data.get('symbol', '')} ${signal_data.get('price', 0):,.2f}"
            bark_url = f"{sub_url.rstrip('/')}/{title}/{body}"
            req = Request(bark_url, method="GET")
            with urlopen(req, timeout=10) as resp:
                return resp.status == 200
        elif fmt == "discord":
            payload = format_webhook_discord(strategy_name, slug, old_action, new_action, signal_data)
        elif fmt == "text":
            text = format_webhook_text(strategy_name, slug, old_action, new_action, signal_data)
            payload = {"text": text, "content": text}
        else:
            payload = format_webhook_json(strategy_name, slug, old_action, new_action, signal_data)

        data = json.dumps(payload).encode()
        req = Request(sub_url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            return resp.status < 300
    except (URLError, OSError) as e:
        print(f"[webhook] Failed for {subscriber.get('id', '?')}: {e}")
        return False


def send_rotation_webhook(subscriber: dict, strategy_name: str, slug: str,
                          old_etf: str, new_etf: str, signal_data: dict) -> bool:
    """Send ETF rotation notification to a webhook subscriber."""
    sub_url = subscriber["url"]
    try:
        payload = format_rotation_webhook_json(strategy_name, slug, old_etf, new_etf, signal_data)
        data = json.dumps(payload).encode()
        req = Request(sub_url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            return resp.status < 300
    except (URLError, OSError) as e:
        print(f"[webhook] Rotation failed for {subscriber.get('id', '?')}: {e}")
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

    signal_changes = []   # (name, sid, old_action, new_action, signal_data)
    rotation_changes = []  # (name, sid, old_etf, new_etf, signal_data)

    for sdir in sorted(strategy_dirs):
        sid = sdir.name
        current = get_current_signal(sid)
        if not current:
            continue

        strategy_info = current.get("strategy", {})
        signal_info = current.get("current_signal", {})
        position_info = current.get("position", {})
        name = strategy_info.get("name", sid)

        prev = get_previous_signal_data(sid)
        if not prev:
            continue

        signal_data = {
            "price": signal_info.get("price", 0),
            "symbol": strategy_info.get("symbol", ""),
            "reason": signal_info.get("reason", ""),
        }

        # --- Primary: in_position change ---
        # Catches buy/sell that occur between daily runs — 'action' is
        # transient (one bar), in_position persists for the whole trade.
        prev_in_pos = prev.get("in_position")
        curr_in_pos = position_info.get("in_position")
        if prev_in_pos is not None and curr_in_pos is not None:
            if not prev_in_pos and curr_in_pos:
                signal_changes.append((name, sid, "hold", "buy", signal_data))
                continue
            if prev_in_pos and not curr_in_pos:
                signal_changes.append((name, sid, "hold", "sell", signal_data))
                continue

        # --- Secondary: ETF rotation (current_etf changed) ---
        prev_etf = prev.get("current_etf")
        curr_etf = signal_info.get("current_etf")
        if prev_etf and curr_etf and prev_etf != curr_etf:
            rotation_data = {
                "old_holding": prev.get("holding", prev_etf),
                "holding": signal_info.get("holding", curr_etf),
                "rotation_reason": signal_info.get("rotation_reason"),
            }
            rotation_changes.append((name, sid, prev_etf, curr_etf, rotation_data))
            continue

        # --- Fallback: action field change (strategies without position tracking) ---
        old_action = prev.get("action")
        new_action = signal_info.get("action", "")
        if old_action and old_action != new_action and new_action in ("buy", "sell"):
            signal_changes.append((name, sid, old_action, new_action, signal_data))

    total = len(signal_changes) + len(rotation_changes)
    if not total:
        print("[notify] No signal changes detected")
        return

    print(f"[notify] Detected {total} change(s): "
          f"{len(signal_changes)} signal, {len(rotation_changes)} rotation")

    for name, sid, old, new, signal_data in signal_changes:
        slug = strategy_slug(sid)
        if has_telegram:
            msg = format_message(name, slug, old, new, signal_data)
            send_telegram(msg)
        for sub in subscribers:
            send_webhook(sub, name, slug, old, new, signal_data)

    for name, sid, old_etf, new_etf, rotation_data in rotation_changes:
        slug = strategy_slug(sid)
        if has_telegram:
            msg = format_rotation_message(name, slug, old_etf, new_etf, rotation_data)
            send_telegram(msg)
        for sub in subscribers:
            send_rotation_webhook(sub, name, slug, old_etf, new_etf, rotation_data)


if __name__ == "__main__":
    main()
