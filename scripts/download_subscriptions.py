#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_subscriptions.py
-----------------------------------
Downloads proxy subscription links from sub.txt,
parses them, and saves them into temp_configs/.
Then it generates metadata for GitHub Actions.
"""

import os
import sys
import json
import base64
import pathlib
import requests
from datetime import datetime

BASE_DIR = pathlib.Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR.parent / "temp_configs"
WORKING_DIR = BASE_DIR.parent / "working_configs"
SUB_FILE = BASE_DIR.parent / "sub.txt"

TEMP_DIR.mkdir(exist_ok=True)
WORKING_DIR.mkdir(exist_ok=True)


def decode_base64(data: str) -> str:
    """Decode base64 safely"""
    try:
        # Fix padding if needed
        missing_padding = len(data) % 4
        if missing_padding:
            data += "=" * (4 - missing_padding)
        return base64.b64decode(data).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def fetch_subscription(url: str) -> str:
    """Download subscription content from URL"""
    try:
        headers = {
            "User-Agent": "ClashConfigTester/1.0 (+https://github.com/)"
        }
        resp = requests.get(url.strip(), headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.text.strip()
        print(f"⚠️ Failed to fetch ({resp.status_code}): {url}")
        return ""
    except Exception as e:
        print(f"❌ Error downloading {url}: {e}")
        return ""


def parse_subscription_data(data: str) -> list[str]:
    """Detects and parses subscription format"""
    proxies = []
    if data.startswith("vmess://") or data.startswith("ss://") or data.startswith("trojan://") or data.startswith("vless://"):
        # Single-line or multi-line plain links
        for line in data.strip().splitlines():
            if any(line.strip().startswith(p) for p in ["vmess://", "vless://", "ss://", "trojan://"]):
                proxies.append(line.strip())
    else:
        # Possibly base64-encoded
        decoded = decode_base64(data)
        for line in decoded.strip().splitlines():
            if any(line.strip().startswith(p) for p in ["vmess://", "vless://", "ss://", "trojan://"]):
                proxies.append(line.strip())
    return proxies


def load_subscriptions() -> list[str]:
    """Load subscription URLs from sub.txt"""
    if not SUB_FILE.exists():
        print("❌ sub.txt not found.")
        sys.exit(1)
    with open(SUB_FILE, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    print(f"📥 Found {len(urls)} subscription URLs.")
    return urls


def save_json(path: pathlib.Path, data):
    """Save dictionary or list to JSON"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    print("🚀 Starting subscription download...")
    urls = load_subscriptions()

    all_proxies = []
    for url in urls:
        print(f"→ Fetching: {url}")
        raw = fetch_subscription(url)
        parsed = parse_subscription_data(raw)
        print(f"   ↳ Found {len(parsed)} proxies.")
        all_proxies.extend(parsed)

    print(f"\n✅ Total proxies collected: {len(all_proxies)}")

    # Save parsed proxies
    parsed_path = TEMP_DIR / "parsed_proxies.json"
    save_json(parsed_path, all_proxies)

    stats = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "total_parsed": len(all_proxies),
        "sources": len(urls),
    }
    save_json(TEMP_DIR / "download_stats.json", stats)

    print(f"📦 Saved results to: {parsed_path}")
    print(f"🕒 Timestamp: {stats['timestamp']}")

    # If there are working configs, generate summary
    metadata_path = WORKING_DIR / "metadata.json"
    if metadata_path.exists():
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            print("\n📊 Summary of Working Proxies:")
            print(f"• Total Working: {data.get('total_working', 0)}")
            if 'latency' in data:
                print(f"• Average Latency: {data['latency'].get('average', 'N/A')} ms")
            if 'by_protocol' in data:
                print("\n🔹 By Protocol:")
                for proto, count in sorted(data['by_protocol'].items(), key=lambda x: -x[1]):
                    print(f"   - {proto}: {count}")
        except Exception as e:
            print(f"⚠ Error reading metadata.json: {e}")
    else:
        print("ℹ No metadata.json found — skipping summary.")

    print("\n✅ Subscription processing completed successfully.")


if __name__ == "__main__":
    main()