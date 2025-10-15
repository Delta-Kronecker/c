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
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict
from utils import proxy_to_clash_format, generate_clash_config

# Suppress urllib3 warnings
warnings.filterwarnings('ignore', message='Unverified HTTPS request')
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)


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


def test_proxy_connectivity(proxy_port: int = 7890, timeout: int = 5) -> bool:
    """
    Test proxy connectivity with VERY STRICT multi-URL verification
    ALL tests must pass for the proxy to be considered working
    """
    try:
        proxies = {
            'http': f'http://127.0.0.1:{proxy_port}',
            'https': f'http://127.0.0.1:{proxy_port}'
        }

        # Multiple test URLs - ALL must succeed for strict testing
        test_urls = [
            ('http://www.gstatic.com/generate_204', [204], None),  # Must be 204
            ('http://connectivitycheck.gstatic.com/generate_204', [204], None),  # Must be 204
            ('https://www.google.com/favicon.ico', [200], 100),  # Must be 200 and have content
        ]

        # ALL tests must pass - if any fail, the proxy is not working
        for test_url, valid_codes, min_size in test_urls:
            try:
                response = requests.get(
                    test_url,
                    proxies=proxies,
                    timeout=timeout,
                    allow_redirects=False,
                    verify=False
                )
                # Check if status code is valid
                if response.status_code not in valid_codes:
                    return False

                # If min_size specified, verify response has content
                if min_size is not None:
                    if len(response.content) < min_size:
                        return False

            except requests.exceptions.ProxyError:
                # Proxy connection failed - definite failure
                return False
            except requests.exceptions.Timeout:
                # Timeout - proxy is too slow or not working
                return False
            except requests.exceptions.ConnectionError:
                # Connection failed - proxy not working
                return False
            except Exception as e:
                # Any other error means proxy is not working
                return False

        # All tests passed - proxy is working
        return True

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


def test_batch_proxies(batch_num: int, batch_proxies: List[Dict], clash_path: str, temp_dir: str, max_workers: int, test_timeout: int) -> tuple:
    """
    Test a single batch of proxies (up to 100) in parallel
    """
    working_proxies = []
    batch_size = len(batch_proxies)
    completed = 0
    lock = threading.Lock()

    def test_proxy_wrapper(proxy_data):
        """Wrapper function for parallel testing"""
        idx, proxy = proxy_data
        result = test_single_proxy(proxy, clash_path, temp_dir, test_timeout=test_timeout)
        return idx, proxy, result

    # Use ThreadPoolExecutor for parallel testing within batch
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks for this batch
        futures = {executor.submit(test_proxy_wrapper, (i, proxy)): i
                   for i, proxy in enumerate(batch_proxies, 1)}

        # Process results as they complete
        for future in as_completed(futures):
            try:
                idx, proxy, result = future.result()

                with lock:
                    completed += 1
                    if result:
                        working_proxies.append(proxy)

            except Exception as e:
                with lock:
                    completed += 1

    return working_proxies, completed


def test_group_proxies(group_name: str, proxies: List[Dict], clash_path: str, temp_dir: str, max_workers: int, test_timeout: int, batch_size: int = 100) -> tuple:
    """
    Test proxies for a specific group/protocol in batches of 100
    Each batch is tested in parallel, but batches are processed sequentially
    """
    working_proxies = []
    total = len(proxies)

    print(f"\n{'='*60}")
    print(f"🔍 Testing {group_name.upper()} Group - {total} proxies")
    print(f"{'='*60}")

    # Split proxies into batches
    num_batches = (total + batch_size - 1) // batch_size  # Ceiling division

    total_tested = 0

    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, total)
        batch_proxies = proxies[start_idx:end_idx]

        batch_count = len(batch_proxies)

        print(f"\n  📦 Batch {batch_num + 1}/{num_batches} - Testing {batch_count} configs...")

        # Test this batch in parallel
        batch_working, batch_tested = test_batch_proxies(
            batch_num + 1,
            batch_proxies,
            clash_path,
            temp_dir,
            max_workers,
            test_timeout
        )

        # Update totals
        working_proxies.extend(batch_working)
        total_tested += batch_tested

        # Print batch report
        batch_success_rate = (len(batch_working) / batch_tested * 100) if batch_tested > 0 else 0
        overall_success_rate = (len(working_proxies) / total_tested * 100) if total_tested > 0 else 0

        print(f"  ✓ Batch {batch_num + 1} Complete:")
        print(f"     - Batch: {len(batch_working)}/{batch_tested} working ({batch_success_rate:.1f}%)")
        print(f"     - Overall: {len(working_proxies)}/{total_tested} working ({overall_success_rate:.1f}%)")

    # Final summary for this group
    success_rate = (len(working_proxies) / total * 100) if total > 0 else 0
    print(f"\n  ✅ {group_name.upper()} COMPLETE: {len(working_proxies)}/{total} working ({success_rate:.1f}%)")

    return working_proxies, {'total': total, 'working': len(working_proxies)}


def test_all_proxies(proxies: List[Dict], clash_path: str, temp_dir: str, max_workers: int = 100) -> List[Dict]:
    """
    Test all proxies grouped by protocol type - protocols tested SEQUENTIALLY
    Each protocol is divided into batches of 100 that run in parallel
    """
    # Get max workers from environment or use default
    max_workers = int(os.environ.get('TEST_WORKERS', max_workers))
    test_timeout = int(os.environ.get('TEST_TIMEOUT', 5))
    batch_size = int(os.environ.get('BATCH_SIZE', 100))

    # Group proxies by protocol type
    groups = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        if ptype not in groups:
            groups[ptype] = []
        groups[ptype].append(proxy)

    print(f"\n{'='*60}")
    print(f"📊 Test Overview")
    print(f"{'='*60}")
    print(f"Total proxies: {len(proxies)}")
    print(f"Workers per batch: {max_workers} | Timeout: {test_timeout}s | Batch size: {batch_size}")
    print(f"\nGroups found:")
    for ptype, plist in sorted(groups.items()):
        print(f"  • {ptype.upper()}: {len(plist)} proxies")
    print(f"{'='*60}")
    print(f"\n🚀 Testing protocols SEQUENTIALLY (batches of {batch_size})...\n")

    # Test each group SEQUENTIALLY (one protocol at a time)
    all_working = []
    group_stats = {}

    # Process protocols in sorted order for consistent output
    for group_name, group_proxies in sorted(groups.items()):
        try:
            # Test this protocol group
            working, stats = test_group_proxies(
                group_name,
                group_proxies,
                clash_path,
                temp_dir,
                max_workers,
                test_timeout,
                batch_size
            )

            all_working.extend(working)
            group_stats[group_name] = stats

        except Exception as e:
            print(f"Error testing {group_name} group: {e}")
            group_stats[group_name] = {'total': len(group_proxies), 'working': 0}

    return all_working, group_stats


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
