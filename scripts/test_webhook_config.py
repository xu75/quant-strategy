#!/usr/bin/env python3
"""Test webhook configuration loading (env var vs file).

Usage:
    # Test with environment variable
    export WEBHOOK_SUBSCRIBERS_JSON='[{"id":"test","url":"https://example.com"}]'
    python scripts/test_webhook_config.py

    # Test with local file (config/subscribers.json)
    unset WEBHOOK_SUBSCRIBERS_JSON
    python scripts/test_webhook_config.py
"""

import json
import os
import sys
from pathlib import Path

# Simulate the script's loading logic
SUBSCRIBERS_FILE = Path("config/subscribers.json")
WEBHOOK_SUBSCRIBERS_JSON = os.environ.get("WEBHOOK_SUBSCRIBERS_JSON", "")


def load_subscribers() -> list[dict]:
    """Load webhook subscribers from env var or config file."""
    # Priority 1: Environment variable
    if WEBHOOK_SUBSCRIBERS_JSON:
        try:
            subscribers = json.loads(WEBHOOK_SUBSCRIBERS_JSON)
            if not isinstance(subscribers, list):
                print("❌ ERROR: WEBHOOK_SUBSCRIBERS_JSON must be a JSON array")
                return []
            return subscribers
        except json.JSONDecodeError as e:
            print(f"❌ ERROR: Invalid JSON in WEBHOOK_SUBSCRIBERS_JSON: {e}")
            return []

    # Priority 2: Local config file
    if not SUBSCRIBERS_FILE.exists():
        return []
    try:
        with open(SUBSCRIBERS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"❌ ERROR: Failed to read {SUBSCRIBERS_FILE}: {e}")
        return []


def main():
    print("🔍 Webhook Configuration Test\n")

    # Check environment variable
    if WEBHOOK_SUBSCRIBERS_JSON:
        print("✅ Environment variable WEBHOOK_SUBSCRIBERS_JSON is set")
        print(f"   Length: {len(WEBHOOK_SUBSCRIBERS_JSON)} characters")
    else:
        print("⚠️  Environment variable WEBHOOK_SUBSCRIBERS_JSON is NOT set")

    # Check file
    if SUBSCRIBERS_FILE.exists():
        print(f"✅ Local file {SUBSCRIBERS_FILE} exists")
    else:
        print(f"⚠️  Local file {SUBSCRIBERS_FILE} does NOT exist")

    print("\n" + "="*60 + "\n")

    # Load subscribers
    subscribers = load_subscribers()

    if not subscribers:
        print("❌ No subscribers loaded!")
        print("\nTroubleshooting:")
        print("  1. Set WEBHOOK_SUBSCRIBERS_JSON environment variable, OR")
        print("  2. Create config/subscribers.json file")
        print("\nExample:")
        print('  export WEBHOOK_SUBSCRIBERS_JSON=\'[{"id":"test","url":"https://example.com"}]\'')
        return 1

    print(f"✅ Loaded {len(subscribers)} subscriber(s):\n")
    for i, sub in enumerate(subscribers, 1):
        sub_id = sub.get("id", "?")
        url = sub.get("url", "?")
        fmt = sub.get("format", "json")
        # Mask webhook URL for security (show first 30 chars + last 10 chars)
        if len(url) > 50:
            masked_url = url[:30] + "..." + url[-10:]
        else:
            masked_url = url[:15] + "..." if len(url) > 20 else url
        print(f"  {i}. ID: {sub_id}")
        print(f"     URL: {masked_url}")
        print(f"     Format: {fmt}")
        print()

    # Determine source
    if WEBHOOK_SUBSCRIBERS_JSON:
        print("📍 Source: Environment variable (WEBHOOK_SUBSCRIBERS_JSON)")
        print("   ✅ Safe for GitHub Actions secrets")
    else:
        print(f"📍 Source: Local file ({SUBSCRIBERS_FILE})")
        print("   ⚠️  Make sure this file is in .gitignore!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
