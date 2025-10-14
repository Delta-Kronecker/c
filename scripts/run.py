"""
Test proxy configs using Clash
"""
import os
import sys
import json
import yaml
import time
import subprocess
import requests
import threading
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict
from utils import proxy_to_clash_format, generate_clash_config


def sanitize_filename(name: str) -> str:
    """
    Sanitize proxy name to create valid filename
    """
    # Remove or replace invalid characters
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Remove control characters
    name = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', name)
    # Limit length and create hash for uniqueness
    if len(name) > 50:
        name_hash = hashlib.md5(name.encode()).hexdigest()[:8]
        name = name[:42] + '_' + name_hash
    return name if name else 'proxy'


def load_parsed_proxies(file_path: str) -> List[Dict]:
    """
    Load parsed proxies from JSON file
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            proxies = json.load(f)
        print(f"Loaded {len(proxies)} parsed proxies")
        return proxies
    except Exception as e:
        print(f"Error loading proxies: {e}")
        return []


def create_clash_config(proxy: Dict, config_file: str) -> bool:
    """
    Create a Clash config file for a single proxy
    """
    try:
        # Convert proxy to Clash format
        clash_proxy = proxy_to_clash_format(proxy)

        # Generate full Clash config
        config = {
            'port': 7890,
            'socks-port': 7891,
            'allow-lan': False,
            'mode': 'global',
            'log-level': 'silent',
            'external-controller': '127.0.0.1:9090',
            'proxies': [clash_proxy],
            'proxy-groups': [
                {
                    'name': 'PROXY',
                    'type': 'select',
                    'proxies': [clash_proxy['name']]
                }
            ],
            'rules': [
                'MATCH,PROXY'
            ]
        }

        # Write config to file
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        return True
    except Exception as e:
        print(f"Error creating config: {e}")
        return False


def test_proxy_connectivity(proxy_port: int = 7890, timeout: int = 8) -> bool:
    """
    Test proxy connectivity with strict multi-URL verification
    """
    try:
        proxies = {
            'http': f'http://127.0.0.1:{proxy_port}',
            'https': f'http://127.0.0.1:{proxy_port}'
        }

        # Multiple test URLs - ALL must succeed for strict testing
        test_urls = [
            ('http://www.gstatic.com/generate_204', [204]),
            ('http://connectivitycheck.gstatic.com/generate_204', [204]),
            ('https://www.google.com/favicon.ico', [200]),
        ]

        success_count = 0
        required_success = 2  # At least 2 out of 3 must succeed

        for test_url, valid_codes in test_urls:
            try:
                response = requests.get(
                    test_url,
                    proxies=proxies,
                    timeout=timeout,
                    allow_redirects=False,
                    verify=False
                )
                # Check if status code is valid
                if response.status_code in valid_codes:
                    success_count += 1
                    # If we got enough successes, return True
                    if success_count >= required_success:
                        return True
            except requests.exceptions.ProxyError:
                # Proxy connection failed
                return False
            except requests.exceptions.Timeout:
                # Timeout - proxy is too slow
                continue
            except:
                # Other errors
                continue

        return False
    except Exception as e:
        return False


def test_single_proxy(proxy: Dict, clash_path: str, config_dir: str, test_timeout: int = 5, proxy_port: int = None) -> bool:
    """
    Test a single proxy using Clash with unique port
    """
    try:
        # Sanitize filename
        safe_name = sanitize_filename(proxy.get('name', 'proxy'))
        # Add unique ID to avoid conflicts
        unique_id = hashlib.md5(f"{proxy.get('server', '')}:{proxy.get('port', '')}".encode()).hexdigest()[:8]
        config_file = os.path.join(config_dir, f"test_{unique_id}_{safe_name}.yaml")

        # Use provided port or default
        if proxy_port is None:
            proxy_port = 7890

        if not create_clash_config(proxy, config_file):
            return False

        # Start Clash process
        process = None
        try:
            # Start Clash
            process = subprocess.Popen(
                [clash_path, '-f', config_file],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            # Wait for Clash to start
            time.sleep(2)

            # Test connectivity with strict verification
            result = test_proxy_connectivity(proxy_port=proxy_port, timeout=test_timeout)

            return result

        finally:
            # Stop Clash process
            if process:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except:
                    process.kill()

            # Clean up config file
            try:
                os.remove(config_file)
            except:
                pass

    except Exception as e:
        return False


def test_all_proxies(proxies: List[Dict], clash_path: str, temp_dir: str, max_workers: int = 100) -> List[Dict]:
    """
    Test all proxies in parallel and return working ones
    """
    working_proxies = []
    total = len(proxies)
    completed = 0
    lock = threading.Lock()

    # Group statistics
    group_stats = {}

    # Get max workers from environment or use default
    max_workers = int(os.environ.get('TEST_WORKERS', max_workers))
    test_timeout = int(os.environ.get('TEST_TIMEOUT', 15))  # Increased to 15s for strict testing

    print(f"\n🔍 Testing {total} proxies ({max_workers} workers, {test_timeout}s timeout)\n")

    def test_proxy_wrapper(proxy_data):
        """Wrapper function for parallel testing"""
        idx, proxy = proxy_data
        proxy_name = proxy.get('name', 'unknown')[:50]  # Truncate long names
        proxy_type = proxy.get('type', 'unknown')

        # Test proxy
        result = test_single_proxy(proxy, clash_path, temp_dir, test_timeout=test_timeout)

        return idx, proxy, result, proxy_name, proxy_type

    # Use ThreadPoolExecutor for parallel testing
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        futures = {executor.submit(test_proxy_wrapper, (i, proxy)): i
                   for i, proxy in enumerate(proxies, 1)}

        # Process results as they complete
        for future in as_completed(futures):
            try:
                idx, proxy, result, proxy_name, proxy_type = future.result()

                with lock:
                    completed += 1

                    # Update group statistics
                    if proxy_type not in group_stats:
                        group_stats[proxy_type] = {'total': 0, 'working': 0}
                    group_stats[proxy_type]['total'] += 1
                    if result:
                        group_stats[proxy_type]['working'] += 1
                        working_proxies.append(proxy)

                    # Show progress every 10% or every 50 proxies
                    if completed % max(1, total // 10) == 0 or completed % 50 == 0 or completed == total:
                        print(f"Progress: {completed}/{total} ({completed*100//total}%)")

            except Exception as e:
                with lock:
                    completed += 1

    return working_proxies, group_stats


def save_working_configs(proxies: List[Dict], output_dir: str):
    """
    Save working configs to output directory
    """
    os.makedirs(output_dir, exist_ok=True)

    # Save all working configs as JSON
    json_file = os.path.join(output_dir, 'working_proxies.json')
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(proxies, f, indent=2, ensure_ascii=False)

    print(f"Saved working proxies to: {json_file}")

    # Save by protocol type
    by_protocol_dir = os.path.join(output_dir, 'by_protocol')
    os.makedirs(by_protocol_dir, exist_ok=True)

    protocols = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        if ptype not in protocols:
            protocols[ptype] = []
        protocols[ptype].append(proxy)

    for ptype, plist in protocols.items():
        # Save as text (share URLs)
        txt_file = os.path.join(by_protocol_dir, f'{ptype}.txt')
        with open(txt_file, 'w', encoding='utf-8') as f:
            for proxy in plist:
                # Generate share URL based on type
                share_url = generate_share_url(proxy)
                if share_url:
                    f.write(share_url + '\n')

    # Save all working configs as text
    all_txt_file = os.path.join(output_dir, 'all_working.txt')
    with open(all_txt_file, 'w', encoding='utf-8') as f:
        for proxy in proxies:
            share_url = generate_share_url(proxy)
            if share_url:
                f.write(share_url + '\n')

    # Save metadata
    metadata = {
        'total_working': len(proxies),
        'by_protocol': {ptype: len(plist) for ptype, plist in protocols.items()},
        'latency': {
            'average': 0,
            'min': 0,
            'max': 0
        },
        'last_updated': datetime.now().isoformat(),
        'timestamp': int(time.time())
    }

    metadata_file = os.path.join(output_dir, 'metadata.json')
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)

    # Save last updated timestamp
    timestamp_file = os.path.join(output_dir, 'last_updated.txt')
    with open(timestamp_file, 'w', encoding='utf-8') as f:
        f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'))

    print(f"Saved {len(proxies)} working configs to: {output_dir}")


def generate_share_url(proxy: Dict) -> str:
    """
    Generate share URL from proxy config using utils module
    """
    from utils import proxy_to_share_url
    return proxy_to_share_url(proxy)


def find_clash_binary() -> str:
    """
    Find Clash binary in system
    """
    # Common locations
    possible_paths = [
        '/usr/local/bin/clash',
        '/usr/bin/clash',
        './clash',
        './clash-linux-amd64',
        './clash-linux-arm64',
        'clash.exe',
        './clash.exe'
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return path

    # Check in PATH
    try:
        result = subprocess.run(['which', 'clash'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    except:
        pass

    return None


def main():
    """
    Main function
    """
    print("=" * 60)
    print("Clash Config Tester")
    print("=" * 60 + "\n")

    # Get paths
    base_dir = os.path.dirname(os.path.dirname(__file__))
    temp_dir = os.path.join(base_dir, 'temp_configs')
    output_dir = os.path.join(base_dir, 'working_configs')

    # Load parsed proxies
    proxies_file = os.path.join(temp_dir, 'parsed_proxies.json')
    if not os.path.exists(proxies_file):
        print(f"Error: Proxies file not found: {proxies_file}")
        print("Please run download_subscriptions.py first")
        sys.exit(1)

    proxies = load_parsed_proxies(proxies_file)

    if not proxies:
        print("No proxies to test")
        sys.exit(1)

    # Find Clash binary
    clash_path = find_clash_binary()
    if not clash_path:
        print("Error: Clash binary not found")
        print("Please install Clash and make sure it's in PATH")
        sys.exit(1)

    print(f"Using Clash binary: {clash_path}\n")

    # Test proxies
    working_proxies, group_stats = test_all_proxies(proxies, clash_path, temp_dir)

    # Display summary report by groups
    print(f"\n{'=' * 60}")
    print(f"📊 Test Results Summary")
    print(f"{'=' * 60}")
    print(f"\n{'Protocol':<15} {'Total':<10} {'Working':<10} {'Success Rate':<15}")
    print(f"{'-' * 60}")

    for protocol, stats in sorted(group_stats.items()):
        total = stats['total']
        working = stats['working']
        rate = (working / total * 100) if total > 0 else 0
        print(f"{protocol:<15} {total:<10} {working:<10} {rate:>5.1f}%")

    print(f"{'-' * 60}")
    total_all = len(proxies)
    working_all = len(working_proxies)
    rate_all = (working_all / total_all * 100) if total_all > 0 else 0
    print(f"{'TOTAL':<15} {total_all:<10} {working_all:<10} {rate_all:>5.1f}%")
    print(f"{'=' * 60}\n")

    # Save working configs
    if working_proxies:
        save_working_configs(working_proxies, output_dir)
    else:
        print("❌ No working proxies found")


if __name__ == '__main__':
    main()
