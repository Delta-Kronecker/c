"""
Download and decode subscription links from sub.txt
"""
import os
import sys
import requests
import json
from typing import List, Set
from utils import decode_base64, is_base64, parse_proxy_url


def read_subscription_urls(file_path: str = 'sub.txt') -> List[str]:
    """
    Read subscription URLs from file
    """
    urls = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    urls.append(line)
        print(f"Loaded {len(urls)} subscription URLs")
        return urls
    except Exception as e:
        print(f"Error reading subscription file: {e}")
        return []


def download_subscription(url: str, timeout: int = 30) -> str:
    """
    Download subscription content from URL
    """
    try:
        print(f"Downloading: {url}")
        headers = {
            'User-Agent': 'ClashForAndroid/2.5.12'
        }
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        return ""


def parse_subscription_content(content: str) -> List[str]:
    """
    Parse subscription content and extract proxy URLs
    """
    proxy_urls = []

    if not content:
        return proxy_urls

    # Check if content is base64 encoded
    lines = content.strip().split('\n')

    # If it's a single line and looks like base64, try to decode
    if len(lines) == 1 and is_base64(content.strip()):
        try:
            decoded = decode_base64(content.strip())
            lines = decoded.split('\n')
        except:
            pass

    # Extract proxy URLs
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        # Check if line is a valid proxy URL
        if any(line.startswith(prefix) for prefix in ['ss://', 'vmess://', 'vless://', 'trojan://', 'ssr://']):
            proxy_urls.append(line)

    return proxy_urls


def download_all_subscriptions(sub_file: str = 'sub.txt', output_dir: str = 'temp_configs') -> List[str]:
    """
    Download all subscriptions and return list of proxy URLs
    """
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Read subscription URLs
    urls = read_subscription_urls(sub_file)

    if not urls:
        print("No subscription URLs found")
        return []

    # Download and parse all subscriptions
    all_proxy_urls = []
    seen_urls = set()  # To avoid duplicates

    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] Processing: {url}")

        # Download subscription
        content = download_subscription(url)

        if not content:
            continue

        # Parse proxy URLs
        proxy_urls = parse_subscription_content(content)
        print(f"  Found {len(proxy_urls)} proxy URLs")

        # Add to list (avoid duplicates)
        for proxy_url in proxy_urls:
            if proxy_url not in seen_urls:
                all_proxy_urls.append(proxy_url)
                seen_urls.add(proxy_url)

    print(f"\n{'=' * 60}")
    print(f"Total unique proxy URLs: {len(all_proxy_urls)}")
    print(f"{'=' * 60}\n")

    # Save raw proxy URLs to file
    raw_file = os.path.join(output_dir, 'raw_proxies.txt')
    with open(raw_file, 'w', encoding='utf-8') as f:
        for proxy_url in all_proxy_urls:
            f.write(proxy_url + '\n')

    print(f"Raw proxy URLs saved to: {raw_file}")

    return all_proxy_urls


def parse_all_proxies(proxy_urls: List[str], output_dir: str = 'temp_configs') -> List[dict]:
    """
    Parse all proxy URLs to structured format
    """
    parsed_proxies = []

    print(f"\nParsing {len(proxy_urls)} proxy URLs...")

    for i, url in enumerate(proxy_urls, 1):
        if i % 100 == 0:
            print(f"  Parsed {i}/{len(proxy_urls)}...")

        proxy = parse_proxy_url(url)
        if proxy:
            # Add unique name if duplicate
            name = proxy['name']
            counter = 1
            original_name = name

            # Check for duplicate names
            while any(p['name'] == name for p in parsed_proxies):
                name = f"{original_name}_{counter}"
                counter += 1

            proxy['name'] = name
            parsed_proxies.append(proxy)

    print(f"Successfully parsed {len(parsed_proxies)} proxies")

    # Save parsed proxies to JSON
    json_file = os.path.join(output_dir, 'parsed_proxies.json')
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(parsed_proxies, f, indent=2, ensure_ascii=False)

    print(f"Parsed proxies saved to: {json_file}")

    # Print statistics
    stats = {}
    for proxy in parsed_proxies:
        ptype = proxy.get('type', 'unknown')
        stats[ptype] = stats.get(ptype, 0) + 1

    print(f"\nProxy Statistics:")
    for ptype, count in sorted(stats.items()):
        print(f"  {ptype}: {count}")

    return parsed_proxies


def main():
    """
    Main function
    """
    print("=" * 60)
    print("Clash Config Subscription Downloader")
    print("=" * 60 + "\n")

    # Get paths
    sub_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'sub.txt')
    output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'temp_configs')

    # Download all subscriptions
    proxy_urls = download_all_subscriptions(sub_file, output_dir)

    if not proxy_urls:
        print("No proxy URLs found. Exiting.")
        sys.exit(1)

    # Parse all proxies
    parsed_proxies = parse_all_proxies(proxy_urls, output_dir)

    if not parsed_proxies:
        print("No proxies could be parsed. Exiting.")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"Download complete! Found {len(parsed_proxies)} valid proxies")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()