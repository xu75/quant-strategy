#!/usr/bin/env python3
"""Detect signal changes across strategies and notify via Telegram + webhooks.

Usage (in GitHub Actions):
    python scripts/telegram_notify.py

Requires env vars (Telegram optional; webhooks fire independently):
    TELEGRAM_BOT_TOKEN  — Bot token from @BotFather
    TELEGRAM_CHAT_ID    — Channel/group chat ID

Detection logic (compares current latest.json vs previous git commit):
    Continuous-exposure strategies (V3-style, have current_exposure):
        Fire a POSITION update when executed exposure moves materially, crosses
        the 空仓/持仓 boundary, or the regime/target flips. The message reports
        the model's current position, the strategy target, and the state
        (reducing/increasing/holding) — never a bare BUY/SELL, because a daily
        snapshot collapses intraday events and 'action' is only an audit label.
    Binary strategies (no current_exposure):
        Fire on the durable position.in_position transition. Fall back to an
        action change only when either snapshot lacks the position contract;
        also report independent regime changes.

Webhook success is decided by the JSON body (data:true/false), not HTTP 200 —
api.chuckfang.com always returns 200. Failures make the process exit non-zero.
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

# Fire a position update when executed exposure moves at least this much.
EXPOSURE_NOTIFY_THRESHOLD = 0.035
# 空仓/持仓 display boundary (mirrors core.runner.DISPLAY_POSITION_THRESHOLD).
DISPLAY_THRESHOLD = 0.03
# Retry transient network failures this many extra times before giving up.
WEBHOOK_RETRIES = 2
TELEGRAM_RETRIES = 2

def get_previous_signal_data(strategy_id: str) -> dict | None:
    """Read the previous latest.json snapshot from git HEAD~1.

    Returns a flat dict of the fields detection needs, or None if HEAD~1 has no
    such file. current_exposure falls back to target_exposure for pre-contract
    snapshots (where target_exposure held the executed exposure).
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
            "regime": signal.get("regime"),
            "target_exposure": signal.get("target_exposure"),
            "current_exposure": signal.get("current_exposure"),
            "in_position": position.get("in_position"),
        }
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


def load_subscribers() -> list[dict]:
    """Load webhook subscribers from config file."""
    if not SUBSCRIBERS_FILE.exists():
        return []
    try:
        with open(SUBSCRIBERS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def strategy_slug(strategy_id: str) -> str:
    """Convert strategy_id to URL slug (underscores to hyphens)."""
    return strategy_id.replace("_", "-")


def _prev_executed_exposure(prev: dict | None) -> float | None:
    """Executed exposure from a previous snapshot, tolerating the old format."""
    if not prev:
        return None
    ce = prev.get("current_exposure")
    if ce is not None:
        return ce
    return prev.get("target_exposure")  # pre-contract fallback


def build_notifications(strategy_id: str, current: dict, prev: dict | None) -> list[dict]:
    """Compare current vs previous snapshot and return notification dicts.

    Pure function (no I/O) so detection is fully unit-testable. Each note has a
    'kind': "position" (continuous), "action" (binary), or "regime".
    """
    notes: list[dict] = []
    strat = current.get("strategy", {})
    sig = current.get("current_signal", {})
    name = strat.get("name", strategy_id)
    slug = strategy_slug(strategy_id)
    symbol = strat.get("symbol", "")
    price = sig.get("price", 0)
    regime = sig.get("regime")

    curr_exp = sig.get("current_exposure")

    if curr_exp is not None:
        # --- Continuous-exposure contract (V3-style) ---
        prev_exp = _prev_executed_exposure(prev)
        target = sig.get("target_exposure")
        prev_regime = prev.get("regime") if prev else None
        prev_target = prev.get("target_exposure") if prev else None

        moved = prev_exp is None or abs(curr_exp - (prev_exp or 0)) >= EXPOSURE_NOTIFY_THRESHOLD
        crossed = prev_exp is not None and (
            (prev_exp <= DISPLAY_THRESHOLD) != (curr_exp <= DISPLAY_THRESHOLD)
        )
        regime_changed = regime is not None and prev_regime is not None and regime != prev_regime
        target_changed = (
            target is not None and prev_target is not None
            and prev.get("current_exposure") is not None  # only trust new-format targets
            and abs(target - prev_target) >= EXPOSURE_NOTIFY_THRESHOLD
        )

        if moved or crossed or regime_changed or target_changed:
            if prev_exp is None:
                direction = "increase" if curr_exp > 0 else "flat"
            elif curr_exp > prev_exp + 1e-9:
                direction = "increase"
            elif curr_exp < prev_exp - 1e-9:
                direction = "decrease"
            else:
                direction = "flat"
            notes.append({
                "kind": "position",
                "strategy": name, "slug": slug, "symbol": symbol, "price": price,
                "regime": regime, "direction": direction,
                "prev_exposure": prev_exp,
                "current_exposure": curr_exp,
                "target_exposure": target,
                "exposure_state": sig.get("exposure_state", ""),
                "reason": sig.get("reason", ""),
            })
        return notes

    # --- Binary strategies (no current_exposure) ---
    # `action` is an event on the latest bar, not durable state. A daily
    # pipeline can therefore observe hold -> hold even though a 4H entry or
    # exit occurred between runs. Prefer the persisted position transition;
    # retain action comparison only for older strategies/snapshots without a
    # boolean position contract.
    new_action = sig.get("action", "")
    old_action = prev.get("action") if prev else None
    curr_in_position = current.get("position", {}).get("in_position")
    prev_in_position = prev.get("in_position") if prev else None
    has_position_contract = (
        isinstance(prev_in_position, bool) and isinstance(curr_in_position, bool)
    )
    position_changed = has_position_contract and prev_in_position != curr_in_position

    if position_changed:
        inferred_action = "buy" if curr_in_position else "sell"
        old_state = "long" if prev_in_position else "flat"
        new_state = "long" if curr_in_position else "flat"
        notes.append({
            "kind": "action",
            "strategy": name, "slug": slug, "symbol": symbol, "price": price,
            "old_action": old_action or "hold", "new_action": inferred_action,
            "reason": (
                f"Model position changed from {old_state} to {new_state} "
                "since the previous pipeline run"
            ),
            "detection_source": "position_transition",
            "old_in_position": prev_in_position,
            "new_in_position": curr_in_position,
            "observed_at": sig.get("timestamp"),
        })
    elif (
        not has_position_contract
        and old_action
        and old_action != new_action
        and new_action in ("buy", "sell")
    ):
        notes.append({
            "kind": "action",
            "strategy": name, "slug": slug, "symbol": symbol, "price": price,
            "old_action": old_action, "new_action": new_action,
            "reason": sig.get("reason", ""),
        })

    prev_regime = prev.get("regime") if prev else None
    if regime and prev_regime and regime != prev_regime:
        notes.append({
            "kind": "regime",
            "strategy": name, "slug": slug, "symbol": symbol, "price": price,
            "old_regime": prev_regime, "new_regime": regime,
            "exposure": sig.get("target_exposure"),
        })
    return notes


def _pct(x) -> str:
    return f"{x * 100:.0f}%" if x is not None else "—"


def _state_label(state: str) -> str:
    return {"reducing": "减仓中 Reducing", "increasing": "加仓中 Increasing",
            "holding": "已到位 Holding"}.get(state, state or "—")


def format_position_message(note: dict) -> str:
    """Telegram HTML for a continuous-exposure position update.

    Headline is the executed position + direction of the change. The single
    actionable line tells the user the exact model exposure to sync to; target
    is shown as context (where the strategy is heading), never as the action.
    """
    arrow = {"increase": "⬆️", "decrease": "⬇️"}.get(note["direction"], "↔️")
    curr = _pct(note["current_exposure"])
    tgt = _pct(note.get("target_exposure"))
    prev = _pct(note.get("prev_exposure"))
    price = note.get("price", 0)

    lines = [
        f"{arrow} <b>{note['strategy']}</b> — Position Update",
        "",
        f"Model allocation: <code>{prev} → {curr}</code> of total portfolio",
        f"👉 Set {note['symbol']} to <b>{curr}</b> of your total portfolio",
        f"Strategy target: {tgt} · State: {_state_label(note.get('exposure_state',''))}",
        f"Regime: {note['regime']}" if note.get("regime") else "",
        f"Price: ${price:,.2f}" if price else "",
        "",
        f'<a href="{SITE_URL}/strategy/{note["slug"]}">View Strategy →</a>',
    ]
    return "\n".join(line for line in lines if line != "" or True).replace("\n\n\n", "\n\n")


def format_position_webhook_json(note: dict) -> dict:
    """Webhook payload for a position update (includes required `msg` field)."""
    arrow = {"increase": "⬆️", "decrease": "⬇️"}.get(note["direction"], "↔️")
    curr = _pct(note["current_exposure"])
    tgt = _pct(note.get("target_exposure"))
    price = note.get("price", 0)
    msg = (
        f"{arrow} {note['strategy']} | Set {note['symbol']} to {curr} of total portfolio "
        f"(target {tgt}, {note.get('exposure_state','')}) "
        f"| ${price:,.2f} | {SITE_URL}/strategy/{note['slug']}"
    )
    return {
        "msg": msg,
        "event": "position_change",
        "strategy": note["strategy"],
        "symbol": note["symbol"],
        "direction": note["direction"],
        "prev_exposure": note.get("prev_exposure"),
        "current_exposure": note["current_exposure"],
        "target_exposure": note.get("target_exposure"),
        "exposure_state": note.get("exposure_state", ""),
        "regime": note.get("regime"),
        "price": price,
        "reason": note.get("reason", ""),
        "url": f"{SITE_URL}/strategy/{note['slug']}",
    }


def format_message(strategy_name: str, slug: str,
                   old_action: str, new_action: str, signal_data: dict) -> str:
    """Telegram HTML for a binary action change."""
    emoji = {"buy": "\U0001f7e2", "sell": "\U0001f534"}.get(new_action, "\U0001f4ca")
    price = signal_data.get("price", 0)
    price_label = signal_data.get("price_label", "Price")
    symbol = signal_data.get("symbol", "")
    reason = signal_data.get("reason", "")
    lines = [
        f"{emoji} <b>{strategy_name}</b> — Signal Change",
        "",
        f"<code>{old_action.upper()} → {new_action.upper()}</code>",
        f"Symbol: {symbol}",
        f"{price_label}: ${price:,.2f}" if price else "",
        f"Reason: {reason}" if reason else "",
        "",
        f'<a href="{SITE_URL}/strategy/{slug}">View Strategy →</a>',
    ]
    return "\n".join(line for line in lines if line is not None)


def format_regime_message(strategy_name: str, slug: str,
                          old_regime: str, new_regime: str, signal_data: dict) -> str:
    """Telegram HTML for a regime change."""
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


def format_webhook_json(note: dict) -> dict:
    """Webhook payload for a binary action change (includes `msg`)."""
    action_emoji = {"buy": "🟢", "sell": "🔴"}.get(note["new_action"], "📊")
    price = note.get("price", 0)
    price_text = (
        f"latest ${price:,.2f}"
        if note.get("detection_source") == "position_transition"
        else f"${price:,.2f}"
    )
    msg = (
        f"{action_emoji} {note['strategy']} | {note['old_action'].upper()} → "
        f"{note['new_action'].upper()} | {note['symbol']} {price_text} "
        f"| {SITE_URL}/strategy/{note['slug']}"
    )
    payload = {
        "msg": msg,
        "event": "signal_change",
        "strategy": note["strategy"],
        "symbol": note["symbol"],
        "old_signal": note["old_action"],
        "new_signal": note["new_action"],
        "price": price,
        "reason": note.get("reason", ""),
        "url": f"{SITE_URL}/strategy/{note['slug']}",
    }
    if note.get("detection_source") == "position_transition":
        payload.update({
            "detection_source": "position_transition",
            "old_in_position": note.get("old_in_position"),
            "new_in_position": note.get("new_in_position"),
            "observed_at": note.get("observed_at"),
            "price_semantics": "latest_snapshot",
        })
    return payload


def format_webhook_json_regime(note: dict) -> dict:
    """Webhook payload for a regime change (includes `msg`)."""
    regime_emoji = {"bull": "📈", "bear": "📉", "neutral": "📊"}
    emoji = regime_emoji.get(note["new_regime"], "🔄")
    price = note.get("price", 0)
    msg = (
        f"{emoji} {note['strategy']} | Regime {note['old_regime'].upper()} → "
        f"{note['new_regime'].upper()} | {note['symbol']} ${price:,.2f} "
        f"| {SITE_URL}/strategy/{note['slug']}"
    )
    return {
        "msg": msg,
        "event": "regime_change",
        "strategy": note["strategy"],
        "symbol": note["symbol"],
        "old_regime": note["old_regime"],
        "new_regime": note["new_regime"],
        "price": price,
        "exposure": note.get("exposure"),
        "url": f"{SITE_URL}/strategy/{note['slug']}",
    }


def _note_to_text(note: dict) -> str:
    """Plain-text one-liner for text/bark channels."""
    if note["kind"] == "position":
        return (f"{note['strategy']} | Set {note['symbol']} to "
                f"{_pct(note['current_exposure'])} of total portfolio "
                f"(target {_pct(note.get('target_exposure'))})")
    if note["kind"] == "action":
        return (f"{note['strategy']} | {note['old_action'].upper()} → "
                f"{note['new_action'].upper()} | {note['symbol']}")
    return (f"{note['strategy']} | Regime {note['old_regime'].upper()} → "
            f"{note['new_regime'].upper()} | {note['symbol']}")


def _webhook_payload(note: dict, fmt: str):
    """Build the payload for a subscriber format from a note."""
    if fmt == "discord":
        text = _note_to_text(note)
        return {"content": text, "embeds": [{"title": note["strategy"], "description": text,
                "url": f"{SITE_URL}/strategy/{note['slug']}"}]}
    if fmt == "text":
        text = _note_to_text(note)
        return {"text": text, "content": text}
    # default json
    if note["kind"] == "position":
        return format_position_webhook_json(note)
    if note["kind"] == "regime":
        return format_webhook_json_regime(note)
    return format_webhook_json(note)


def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API (retries transient failures)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID, "text": message,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }).encode()
    for attempt in range(TELEGRAM_RETRIES + 1):
        req = Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    print("[telegram] Message sent successfully")
                    return True
                print(f"[telegram] Unexpected status: {resp.status}")
        except (URLError, OSError) as e:
            print(f"[telegram] Send failed (attempt {attempt + 1}): {e}")
    return False


def send_webhook(subscriber: dict, note: dict) -> bool:
    """Send one note to one subscriber. Verifies JSON body (data:true/false),
    not just HTTP status, and retries transient network errors."""
    sub_url = subscriber["url"]
    fmt = subscriber.get("format", "json")
    sub_id = subscriber.get("id", "?")

    if fmt == "bark":
        title = f"{note['strategy']}"
        body = _note_to_text(note)
        bark_url = f"{sub_url.rstrip('/')}/{title}/{body}"
        for attempt in range(WEBHOOK_RETRIES + 1):
            try:
                with urlopen(Request(bark_url, method="GET"), timeout=10) as resp:
                    return resp.status == 200
            except (URLError, OSError) as e:
                print(f"[webhook] bark failed for {sub_id} (attempt {attempt + 1}): {e}")
        return False

    payload = _webhook_payload(note, fmt)
    data = json.dumps(payload).encode()
    for attempt in range(WEBHOOK_RETRIES + 1):
        req = Request(sub_url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=10) as resp:
                if resp.status >= 300:
                    print(f"[webhook] HTTP {resp.status} for {sub_id}")
                    return False
                # api.chuckfang.com always returns 200; the JSON body's `data`
                # field carries the real result.
                try:
                    body = json.loads(resp.read().decode())
                    if isinstance(body, dict) and "data" in body:
                        ok = bool(body["data"])
                        if not ok:
                            print(f"[webhook] Endpoint rejected for {sub_id}: {body.get('msg', body)}")
                        else:
                            print(f"[webhook] Sent successfully to {sub_id}")
                        return ok
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass  # non-JSON body → trust the 2xx status
                print(f"[webhook] Sent successfully to {sub_id}")
                return True
        except (URLError, OSError) as e:
            print(f"[webhook] Failed for {sub_id} (attempt {attempt + 1}): {e}")
    return False


def _telegram_message(note: dict) -> str:
    if note["kind"] == "position":
        return format_position_message(note)
    if note["kind"] == "regime":
        return format_regime_message(
            note["strategy"], note["slug"], note["old_regime"], note["new_regime"],
            {"price": note.get("price", 0), "symbol": note["symbol"], "exposure": note.get("exposure")},
        )
    return format_message(
        note["strategy"], note["slug"], note["old_action"], note["new_action"],
        {
            "price": note.get("price", 0),
            "price_label": (
                "Latest price"
                if note.get("detection_source") == "position_transition"
                else "Price"
            ),
            "symbol": note["symbol"],
            "reason": note.get("reason", ""),
        },
    )


def main() -> int:
    has_telegram = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    subscribers = load_subscribers()

    if not has_telegram and not subscribers:
        print("[notify] No channels configured, skipping")
        return 0

    channels = []
    if has_telegram:
        channels.append("Telegram")
    if subscribers:
        channels.append(f"Webhooks({len(subscribers)})")
    print(f"[notify] Active channels: {', '.join(channels)}")

    strategy_dirs = [d for d in DATA_DIR.iterdir()
                     if d.is_dir() and (d / "latest.json").exists()
                     and d.name != "market"]

    notes: list[dict] = []
    for sdir in sorted(strategy_dirs):
        sid = sdir.name
        current = get_current_signal(sid)
        if not current:
            continue
        prev = get_previous_signal_data(sid)
        notes.extend(build_notifications(sid, current, prev))

    if not notes:
        print("[notify] No signal changes detected")
        return 0

    print(f"[notify] Detected {len(notes)} notification(s)")
    failures = 0
    for note in notes:
        if has_telegram and not send_telegram(_telegram_message(note)):
            failures += 1
        for sub in subscribers:
            if not send_webhook(sub, note):
                failures += 1

    if failures:
        print(f"[notify] {failures} delivery failure(s)")
        return 1
    print("[notify] All deliveries succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
