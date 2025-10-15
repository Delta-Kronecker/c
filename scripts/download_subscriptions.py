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
from utils import parse_proxy_url, calculate_proxy_hash

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
        resp = requests.get(url.strip(), headers=headers, timeout=120)
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
    protocols = ["vmess://", "vless://", "ss://", "trojan://"]

    # First, try to parse as plain text (look for proxy URLs anywhere in the content)
    for line in data.strip().splitlines():
        line_stripped = line.strip()
        if any(line_stripped.startswith(p) for p in protocols):
            proxies.append(line_stripped)

    # If no proxies found, try base64 decoding
    if not proxies:
        decoded = decode_base64(data)
        if decoded:
            for line in decoded.strip().splitlines():
                line_stripped = line.strip()
                if any(line_stripped.startswith(p) for p in protocols):
                    proxies.append(line_stripped)

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


def remove_duplicate_proxies(proxies: list) -> list:
    """Remove duplicate proxies based on server:port:type combination"""
    seen_hashes = set()
    unique_proxies = []
    duplicate_count = 0

    for proxy in proxies:
        proxy_hash = calculate_proxy_hash(proxy)

        if proxy_hash not in seen_hashes:
            seen_hashes.add(proxy_hash)
            unique_proxies.append(proxy)
        else:
            duplicate_count += 1

    if duplicate_count > 0:
        print(f"🔄 Removed {duplicate_count} duplicate configs")
        print(f"✅ Unique configs: {len(unique_proxies)}")

    return unique_proxies


def main():
    print("🚀 Starting subscription download...")
    urls = load_subscriptions()

    all_proxy_urls = []
    for url in urls:
        print(f"→ Fetching: {url}")
        raw = fetch_subscription(url)
        parsed = parse_subscription_data(raw)
        print(f"   ↳ Found {len(parsed)} proxy URLs.")
        all_proxy_urls.extend(parsed)

    print(f"\n✅ Total proxy URLs collected: {len(all_proxy_urls)}")

    # Parse proxy URLs into dictionaries
    print("🔄 Parsing proxy configurations...")
    parsed_proxies = []
    failed_count = 0

    for proxy_url in all_proxy_urls:
        parsed = parse_proxy_url(proxy_url)
        if parsed:
            parsed_proxies.append(parsed)
        else:
            failed_count += 1

    print(f"✅ Successfully parsed: {len(parsed_proxies)} proxies")
    if failed_count > 0:
        print(f"⚠️  Failed to parse: {failed_count} proxies")

    # Remove duplicates
    print(f"\n🔍 Checking for duplicate configurations...")
    parsed_proxies = remove_duplicate_proxies(parsed_proxies)

    # Save parsed proxies
    parsed_path = TEMP_DIR / "parsed_proxies.json"
    save_json(parsed_path, parsed_proxies)

    stats = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "total_urls": len(all_proxy_urls),
        "total_parsed": len(parsed_proxies),  # After deduplication
        "failed": failed_count,
        "sources": len(urls),
        "unique_configs": len(parsed_proxies),
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
