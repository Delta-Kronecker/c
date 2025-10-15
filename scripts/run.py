"""
Advanced Proxy Testing System using Clash
"""
import os
import sys
import json
import yaml
import time
import socket
import subprocess
import requests
import threading
import re
import hashlib
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager
from utils import proxy_to_clash_format, generate_clash_config, calculate_proxy_hash

warnings.filterwarnings('ignore', message='Unverified HTTPS request')
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)


class PortManager:
    def __init__(self, start_port: int = 17890, end_port: int = 27890):
        self.start_port = start_port
        self.end_port = end_port
        self.used_ports = set()
        self.lock = threading.Lock()

    def is_port_available(self, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('127.0.0.1', port))
                return True
        except:
            return False

    def acquire_port(self) -> Optional[int]:
        with self.lock:
            for port in range(self.start_port, self.end_port):
                if port not in self.used_ports and self.is_port_available(port):
                    self.used_ports.add(port)
                    return port
            return None

    def release_port(self, port: int):
        with self.lock:
            self.used_ports.discard(port)


class ClashInstance:
    def __init__(self, config_path: str, clash_binary: str, proxy_port: int, control_port: int):
        self.config_path = config_path
        self.clash_binary = clash_binary
        self.proxy_port = proxy_port
        self.control_port = control_port
        self.process = None
        self.is_ready = False

    def start(self, timeout: int = 10) -> bool:
        try:
            self.process = subprocess.Popen(
                [self.clash_binary, '-f', self.config_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            start_time = time.time()
            while time.time() - start_time < timeout:
                if self._check_health():
                    self.is_ready = True
                    return True
                time.sleep(0.3)

            return False
        except Exception:
            return False

    def _check_health(self) -> bool:
        try:
            response = requests.get(
                f'http://127.0.0.1:{self.control_port}/version',
                timeout=1
            )
            return response.status_code == 200
        except:
            return False

    def stop(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except:
                try:
                    self.process.kill()
                    self.process.wait(timeout=1)
                except:
                    pass
            finally:
                self.process = None


@contextmanager
def clash_context(config_path: str, clash_binary: str, proxy_port: int, control_port: int):
    instance = ClashInstance(config_path, clash_binary, proxy_port, control_port)
    try:
        if instance.start():
            yield instance
        else:
            yield None
    finally:
        instance.stop()
        try:
            if os.path.exists(config_path):
                os.remove(config_path)
        except:
            pass


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', name)
    if len(name) > 50:
        name_hash = hashlib.md5(name.encode()).hexdigest()[:8]
        name = name[:42] + '_' + name_hash
    return name if name else 'proxy'


def remove_duplicate_proxies(proxies: List[Dict]) -> List[Dict]:
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
        print(f"  Removed {duplicate_count} duplicate configs")
        print(f"  Unique configs: {len(unique_proxies)}")

    return unique_proxies


def load_parsed_proxies(file_path: str) -> List[Dict]:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            proxies = json.load(f)
        print(f"Loaded {len(proxies)} parsed proxies")

        # Remove duplicates before testing
        print("Removing duplicate configurations...")
        proxies = remove_duplicate_proxies(proxies)

        return proxies
    except Exception as e:
        print(f"Error loading proxies: {e}")
        return []


def create_clash_config(proxy: Dict, config_file: str, proxy_port: int, control_port: int) -> bool:
    try:
        clash_proxy = proxy_to_clash_format(proxy)

        config = {
            'port': proxy_port,
            'socks-port': proxy_port + 1,
            'allow-lan': False,
            'mode': 'global',
            'log-level': 'silent',
            'external-controller': f'127.0.0.1:{control_port}',
            'proxies': [clash_proxy],
            'proxy-groups': [
                {
                    'name': 'PROXY',
                    'type': 'select',
                    'proxies': [clash_proxy['name']]
                }
            ],
            'rules': ['MATCH,PROXY']
        }

        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        return True
    except Exception:
        return False


def test_proxy_connectivity(proxy_port: int, timeout: int = 8, retry: int = 2) -> Tuple[bool, float]:
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }

    test_targets = [
        {
            'url': 'http://connectivitycheck.gstatic.com/generate_204',
            'expected_code': 204,
            'min_size': None,
            'weight': 2
        },
        {
            'url': 'http://connectivitycheck.gstatic.com/generate_204',
            'expected_code': 204,
            'min_size': None,
            'weight': 2
        },
        {
            'url': 'http://connectivitycheck.gstatic.com/generate_204',
            'expected_code': 204,
            'min_size': None,
            'weight': 2
        },
        {
            'url': 'http://connectivitycheck.gstatic.com/generate_204',
            'expected_code': 204,
            'min_size': None,
            'weight': 2
        },
        {
            'url': 'http://connectivitycheck.gstatic.com/generate_204',
            'expected_code': 204,
            'min_size': None,
            'weight': 2
        }
    ]

    passed_tests = 0
    total_weight = sum(t['weight'] for t in test_targets)
    latencies = []

    for attempt in range(retry):
        for test in test_targets:
            try:
                start = time.time()
                response = requests.get(
                    test['url'],
                    proxies=proxies,
                    timeout=timeout,
                    allow_redirects=False,
                    verify=False
                )
                latency = (time.time() - start) * 1000

                if response.status_code != test['expected_code']:
                    continue

                if test['min_size'] and len(response.content) < test['min_size']:
                    continue

                passed_tests += test['weight']
                latencies.append(latency)

            except (requests.exceptions.ProxyError,
                    requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError):
                continue
            except Exception:
                continue

        if passed_tests >= total_weight * 0.75:
            break

        if attempt < retry - 1:
            time.sleep(1)
            passed_tests = 0
            latencies.clear()

    success = passed_tests >= total_weight * 0.75
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    return success, avg_latency


def test_single_proxy(proxy: Dict, clash_path: str, config_dir: str,
                      port_manager: PortManager, test_timeout: int = 8) -> Tuple[bool, float]:
    proxy_port = port_manager.acquire_port()
    if not proxy_port:
        return False, 0

    control_port = proxy_port + 1000

    try:
        safe_name = sanitize_filename(proxy.get('name', 'proxy'))
        unique_id = hashlib.md5(
            f"{proxy.get('server', '')}:{proxy.get('port', '')}{time.time()}".encode()
        ).hexdigest()[:8]
        config_file = os.path.join(config_dir, f"test_{unique_id}_{safe_name}.yaml")

        if not create_clash_config(proxy, config_file, proxy_port, control_port):
            return False, 0

        with clash_context(config_file, clash_path, proxy_port, control_port) as instance:
            if not instance or not instance.is_ready:
                return False, 0

            time.sleep(0.5)
            success, latency = test_proxy_connectivity(proxy_port, test_timeout)
            return success, latency

    except Exception:
        return False, 0
    finally:
        port_manager.release_port(proxy_port)


def test_batch_proxies(batch_num: int, batch_proxies: List[Dict],
                       clash_path: str, temp_dir: str, port_manager: PortManager,
                       max_workers: int, test_timeout: int) -> Tuple[List[Dict], int, Dict]:
    working_proxies = []
    completed = 0
    latencies = {}
    lock = threading.Lock()

    def test_proxy_wrapper(proxy_data):
        idx, proxy = proxy_data
        result, latency = test_single_proxy(proxy, clash_path, temp_dir, port_manager, test_timeout)
        return idx, proxy, result, latency

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(test_proxy_wrapper, (i, proxy)): i
                   for i, proxy in enumerate(batch_proxies, 1)}

        for future in as_completed(futures):
            try:
                idx, proxy, result, latency = future.result()

                with lock:
                    completed += 1
                    if result:
                        working_proxies.append(proxy)
                        latencies[proxy.get('name', f'proxy_{idx}')] = latency

            except Exception:
                with lock:
                    completed += 1

    return working_proxies, completed, latencies


def test_group_proxies(group_name: str, proxies: List[Dict], clash_path: str,
                       temp_dir: str, port_manager: PortManager, max_workers: int,
                       test_timeout: int, batch_size: int = 50) -> Tuple[List[Dict], Dict, Dict]:
    working_proxies = []
    all_latencies = {}
    total = len(proxies)

    print(f"\n{'='*60}")
    print(f"Testing {group_name.upper()} - {total} proxies")
    print(f"{'='*60}")
    sys.stdout.flush()

    num_batches = (total + batch_size - 1) // batch_size
    total_tested = 0

    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, total)
        batch_proxies = proxies[start_idx:end_idx]

        print(f"\n  Batch {batch_num + 1}/{num_batches} - Testing {len(batch_proxies)} configs...")

        batch_working, batch_tested, batch_latencies = test_batch_proxies(
            batch_num + 1,
            batch_proxies,
            clash_path,
            temp_dir,
            port_manager,
            max_workers,
            test_timeout
        )

        working_proxies.extend(batch_working)
        all_latencies.update(batch_latencies)
        total_tested += batch_tested

        batch_rate = (len(batch_working) / batch_tested * 100) if batch_tested > 0 else 0
        overall_rate = (len(working_proxies) / total_tested * 100) if total_tested > 0 else 0

        print(f"  Batch {batch_num + 1}: {len(batch_working)}/{batch_tested} ({batch_rate:.1f}%)")
        print(f"  Overall: {len(working_proxies)}/{total_tested} ({overall_rate:.1f}%)")
        sys.stdout.flush()

    success_rate = (len(working_proxies) / total * 100) if total > 0 else 0
    print(f"\n  {group_name.upper()} Complete: {len(working_proxies)}/{total} ({success_rate:.1f}%)")
    sys.stdout.flush()

    return working_proxies, {'total': total, 'working': len(working_proxies)}, all_latencies


def test_all_proxies(proxies: List[Dict], clash_path: str, temp_dir: str,
                     max_workers: int = 50) -> Tuple[List[Dict], Dict, Dict]:
    max_workers = int(os.environ.get('TEST_WORKERS', max_workers))
    test_timeout = int(os.environ.get('TEST_TIMEOUT', 8))
    batch_size = int(os.environ.get('BATCH_SIZE', 50))

    port_manager = PortManager()

    groups = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        if ptype not in groups:
            groups[ptype] = []
        groups[ptype].append(proxy)

    print(f"\n{'='*60}")
    print(f"Test Configuration")
    print(f"{'='*60}")
    print(f"Total proxies: {len(proxies)}")
    print(f"Workers: {max_workers} | Timeout: {test_timeout}s | Batch: {batch_size}")
    print(f"\nProtocols:")
    for ptype, plist in sorted(groups.items()):
        print(f"  {ptype.upper()}: {len(plist)}")
    print(f"{'='*60}")
    sys.stdout.flush()

    all_working = []
    group_stats = {}
    all_latencies = {}

    for group_name, group_proxies in sorted(groups.items()):
        try:
            working, stats, latencies = test_group_proxies(
                group_name,
                group_proxies,
                clash_path,
                temp_dir,
                port_manager,
                max_workers,
                test_timeout,
                batch_size
            )

            all_working.extend(working)
            group_stats[group_name] = stats
            all_latencies.update(latencies)

        except Exception as e:
            print(f"Error testing {group_name}: {e}")
            sys.stdout.flush()
            group_stats[group_name] = {'total': len(group_proxies), 'working': 0}

    return all_working, group_stats, all_latencies


def save_working_configs(proxies: List[Dict], output_dir: str, latencies: Dict):
    os.makedirs(output_dir, exist_ok=True)

    json_file = os.path.join(output_dir, 'working_proxies.json')
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(proxies, f, indent=2, ensure_ascii=False)

    by_protocol_dir = os.path.join(output_dir, 'by_protocol')
    os.makedirs(by_protocol_dir, exist_ok=True)

    protocols = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        if ptype not in protocols:
            protocols[ptype] = []
        protocols[ptype].append(proxy)

    for ptype, plist in protocols.items():
        txt_file = os.path.join(by_protocol_dir, f'{ptype}.txt')
        with open(txt_file, 'w', encoding='utf-8') as f:
            for proxy in plist:
                from utils import proxy_to_share_url
                share_url = proxy_to_share_url(proxy)
                if share_url:
                    f.write(share_url + '\n')

    all_txt_file = os.path.join(output_dir, 'all_working.txt')
    with open(all_txt_file, 'w', encoding='utf-8') as f:
        for proxy in proxies:
            from utils import proxy_to_share_url
            share_url = proxy_to_share_url(proxy)
            if share_url:
                f.write(share_url + '\n')

    latency_values = [v for v in latencies.values() if v > 0]
    metadata = {
        'total_working': len(proxies),
        'by_protocol': {ptype: len(plist) for ptype, plist in protocols.items()},
        'latency': {
            'average': sum(latency_values) / len(latency_values) if latency_values else 0,
            'min': min(latency_values) if latency_values else 0,
            'max': max(latency_values) if latency_values else 0
        },
        'last_updated': datetime.now().isoformat(),
        'timestamp': int(time.time())
    }

    metadata_file = os.path.join(output_dir, 'metadata.json')
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)

    timestamp_file = os.path.join(output_dir, 'last_updated.txt')
    with open(timestamp_file, 'w', encoding='utf-8') as f:
        f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'))


def find_clash_binary() -> Optional[str]:
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

    try:
        result = subprocess.run(['which', 'clash'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    except:
        pass

    return None


def main():
    print("=" * 60)
    print("Advanced Clash Proxy Tester")
    print("=" * 60 + "\n")

    base_dir = os.path.dirname(os.path.dirname(__file__))
    temp_dir = os.path.join(base_dir, 'temp_configs')
    output_dir = os.path.join(base_dir, 'working_configs')

    proxies_file = os.path.join(temp_dir, 'parsed_proxies.json')
    if not os.path.exists(proxies_file):
        print(f"Error: {proxies_file} not found")
        print("Run download_subscriptions.py first")
        sys.exit(1)

    proxies = load_parsed_proxies(proxies_file)
    if not proxies:
        print("No proxies to test")
        sys.exit(1)

    clash_path = find_clash_binary()
    if not clash_path:
        print("Error: Clash binary not found")
        sys.exit(1)

    print(f"Clash: {clash_path}\n")

    working_proxies, group_stats, latencies = test_all_proxies(proxies, clash_path, temp_dir)

    print(f"\n{'=' * 60}")
    print(f"Test Results")
    print(f"{'=' * 60}")
    print(f"\n{'Protocol':<15} {'Total':<10} {'Working':<10} {'Rate':<10}")
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

    if working_proxies:
        save_working_configs(working_proxies, output_dir, latencies)
        print(f"\nSaved {len(working_proxies)} working proxies")
    else:
        print("No working proxies found")


if __name__ == '__main__':
    main()
