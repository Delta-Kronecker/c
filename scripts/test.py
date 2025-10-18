"""
Advanced Proxy Testing System using Clash - Fixed False Positives
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
        self.port_release_delay = {}  # Track when ports were released

    def is_port_available(self, port: int) -> bool:
        """Check if port is truly available"""
        try:
            # First check if we recently released this port
            if port in self.port_release_delay:
                release_time = self.port_release_delay[port]
                if time.time() - release_time < 2:  # Wait 2 seconds after release
                    return False
            
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('127.0.0.1', port))
                sock.close()
                
            # Double check - try to connect
            time.sleep(0.1)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                result = sock.connect_ex(('127.0.0.1', port))
                return result != 0  # Port is free if connection fails
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
            self.port_release_delay[port] = time.time()
            
            # Clean old entries
            current_time = time.time()
            self.port_release_delay = {
                p: t for p, t in self.port_release_delay.items() 
                if current_time - t < 5
            }


class ClashInstance:
    def __init__(self, config_path: str, clash_binary: str, proxy_port: int, control_port: int):
        self.config_path = config_path
        self.clash_binary = clash_binary
        self.proxy_port = proxy_port
        self.control_port = control_port
        self.process = None
        self.is_ready = False
        self.start_time = None

    def start(self, timeout: int = 15) -> bool:
        """Start Clash with proper initialization checks"""
        try:
            self.start_time = time.time()
            
            # Start process
            self.process = subprocess.Popen(
                [self.clash_binary, '-f', self.config_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            # Wait for process to initialize
            time.sleep(0.5)
            
            # Check if process is still running
            if self.process.poll() is not None:
                return False

            # Wait for health check
            start_time = time.time()
            consecutive_success = 0
            
            while time.time() - start_time < timeout:
                if self._check_health():
                    consecutive_success += 1
                    if consecutive_success >= 3:  # Require 3 consecutive successful checks
                        self.is_ready = True
                        time.sleep(0.5)  # Extra stabilization time
                        return True
                else:
                    consecutive_success = 0
                    
                time.sleep(0.3)

            return False
        except Exception as e:
            return False

    def _check_health(self) -> bool:
        """Enhanced health check"""
        try:
            # Check if process is still running
            if self.process and self.process.poll() is not None:
                return False
            
            # Check control port
            response = requests.get(
                f'http://127.0.0.1:{self.control_port}/version',
                timeout=2
            )
            
            if response.status_code != 200:
                return False
            
            # Verify proxy port is listening
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', self.proxy_port))
                return result == 0
                
        except:
            return False

    def stop(self):
        """Properly stop Clash instance"""
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
        
        # Wait for port to be fully released
        time.sleep(0.5)


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
        
        # Ensure ports are fully released
        time.sleep(0.3)


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


def test_proxy_connectivity(proxy_port: int, timeout: int = 10, max_retries: int = 2) -> Tuple[bool, float, str]:
    """
    Enhanced connectivity test with content validation
    Returns: (success, latency, failure_reason)
    """
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }

    # Multi-stage testing strategy
    test_stages = [
        {
            'name': 'Basic HTTP',
            'tests': [
                {
                    'url': 'http://connectivitycheck.gstatic.com/generate_204',
                    'expected_code': 204,
                    'min_size': None,
                    'max_size': 10,  # Should be empty
                    'timeout': timeout,
                    'weight': 3
                },
                {
                    'url': 'http://cp.cloudflare.com',
                    'expected_code': 204,
                    'min_size': None,
                    'max_size': 10,
                    'timeout': timeout,
                    'weight': 3
                }
            ],
            'required_pass': 1  # At least 1 test must pass
        },
        {
            'name': 'HTTPS Validation',
            'tests': [
                {
                    'url': 'https://www.gstatic.com/generate_204',
                    'expected_code': 204,
                    'min_size': None,
                    'max_size': 10,
                    'timeout': timeout,
                    'weight': 4
                },
                {
                    'url': 'https://1.1.1.1',
                    'expected_code': [200, 301, 302],  # Allow redirects
                    'min_size': None,
                    'max_size': None,
                    'timeout': timeout,
                    'weight': 3
                }
            ],
            'required_pass': 1
        },
        {
            'name': 'Content Fetch',
            'tests': [
                {
                    'url': 'https://www.google.com',
                    'expected_code': [200],
                    'min_size': 1000,  # Must return real content
                    'max_size': None,
                    'timeout': timeout,
                    'content_check': lambda c: b'google' in c.lower() or b'html' in c.lower(),
                    'weight': 5
                },
                {
                    'url': 'https://www.cloudflare.com',
                    'expected_code': [200],
                    'min_size': 500,
                    'max_size': None,
                    'timeout': timeout,
                    'content_check': lambda c: b'cloudflare' in c.lower() or b'html' in c.lower(),
                    'weight': 4
                }
            ],
            'required_pass': 1
        }
    ]

    all_latencies = []
    stage_results = []
    last_error = "Unknown error"

    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(2)  # Wait before retry
        
        attempt_latencies = []
        attempt_passed_stages = 0
        
        for stage in test_stages:
            stage_passed_tests = 0
            stage_latencies = []
            
            for test in stage['tests']:
                try:
                    start = time.time()
                    
                    response = requests.get(
                        test['url'],
                        proxies=proxies,
                        timeout=test['timeout'],
                        allow_redirects=True,  # Follow redirects
                        verify=False,
                        headers={
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                        }
                    )
                    
                    latency = (time.time() - start) * 1000

                    # Check status code
                    expected_codes = test['expected_code']
                    if isinstance(expected_codes, int):
                        expected_codes = [expected_codes]
                    
                    if response.status_code not in expected_codes:
                        last_error = f"Bad status: {response.status_code}"
                        continue

                    # Check content size
                    content_len = len(response.content)
                    
                    if test.get('min_size') and content_len < test['min_size']:
                        last_error = f"Content too small: {content_len} bytes"
                        continue
                    
                    if test.get('max_size') and content_len > test['max_size']:
                        last_error = f"Content too large: {content_len} bytes"
                        continue

                    # Check content validation
                    if test.get('content_check'):
                        if not test['content_check'](response.content):
                            last_error = "Content validation failed"
                            continue

                    # Test passed
                    stage_passed_tests += test['weight']
                    stage_latencies.append(latency)
                    last_error = None

                except requests.exceptions.ProxyError as e:
                    last_error = "Proxy connection failed"
                    continue
                except requests.exceptions.Timeout:
                    last_error = "Request timeout"
                    continue
                except requests.exceptions.SSLError:
                    last_error = "SSL error"
                    continue
                except requests.exceptions.ConnectionError as e:
                    last_error = "Connection error"
                    continue
                except Exception as e:
                    last_error = f"Unexpected error: {type(e).__name__}"
                    continue
            
            # Check if stage passed
            if stage_passed_tests > 0 and len(stage_latencies) >= stage['required_pass']:
                attempt_passed_stages += 1
                attempt_latencies.extend(stage_latencies)
            
            stage_results.append({
                'name': stage['name'],
                'passed': stage_passed_tests > 0,
                'tests_passed': len(stage_latencies)
            })
        
        # Check if attempt was successful
        # Must pass at least 2 out of 3 stages
        if attempt_passed_stages >= 2 and len(attempt_latencies) >= 3:
            all_latencies.extend(attempt_latencies)
            avg_latency = sum(all_latencies) / len(all_latencies)
            return True, avg_latency, ""
        
        # If failed, clear results for next attempt
        if attempt < max_retries - 1:
            all_latencies.clear()
            stage_results.clear()

    # All attempts failed
    avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0
    return False, avg_latency, last_error or "Failed multiple stages"


def test_single_proxy(proxy: Dict, clash_path: str, config_dir: str,
                      port_manager: PortManager, test_timeout: int = 10) -> Tuple[bool, float, str]:
    """
    Test single proxy with detailed error reporting
    Returns: (success, latency, error_message)
    """
    proxy_port = port_manager.acquire_port()
    if not proxy_port:
        return False, 0, "No available ports"

    control_port = proxy_port + 1000
    error_msg = ""

    try:
        safe_name = sanitize_filename(proxy.get('name', 'proxy'))
        unique_id = hashlib.md5(
            f"{proxy.get('server', '')}:{proxy.get('port', '')}{time.time()}".encode()
        ).hexdigest()[:8]
        config_file = os.path.join(config_dir, f"test_{unique_id}_{safe_name}.yaml")

        if not create_clash_config(proxy, config_file, proxy_port, control_port):
            return False, 0, "Config creation failed"

        with clash_context(config_file, clash_path, proxy_port, control_port) as instance:
            if not instance or not instance.is_ready:
                return False, 0, "Clash instance failed to start"

            # Give Clash extra time to stabilize
            time.sleep(1)
            
            success, latency, error = test_proxy_connectivity(proxy_port, test_timeout)
            
            if not success:
                error_msg = error
            
            return success, latency, error_msg

    except Exception as e:
        return False, 0, f"Exception: {type(e).__name__}"
    finally:
        port_manager.release_port(proxy_port)
        time.sleep(0.2)  # Small delay before next test


def test_batch_proxies(batch_num: int, batch_proxies: List[Dict],
                       clash_path: str, temp_dir: str, port_manager: PortManager,
                       max_workers: int, test_timeout: int) -> Tuple[List[Dict], int, Dict, Dict]:
    """Test batch with error tracking"""
    working_proxies = []
    completed = 0
    latencies = {}
    error_stats = {}
    lock = threading.Lock()

    def test_proxy_wrapper(proxy_data):
        idx, proxy = proxy_data
        result, latency, error = test_single_proxy(proxy, clash_path, temp_dir, port_manager, test_timeout)
        return idx, proxy, result, latency, error

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(test_proxy_wrapper, (i, proxy)): i
                   for i, proxy in enumerate(batch_proxies, 1)}

        for future in as_completed(futures):
            try:
                idx, proxy, result, latency, error = future.result()

                with lock:
                    completed += 1
                    if result:
                        working_proxies.append(proxy)
                        latencies[proxy.get('name', f'proxy_{idx}')] = latency
                    else:
                        # Track error types
                        error_stats[error] = error_stats.get(error, 0) + 1

            except Exception as e:
                with lock:
                    completed += 1
                    error_stats[f"Exception: {type(e).__name__}"] = error_stats.get(f"Exception: {type(e).__name__}", 0) + 1

    return working_proxies, completed, latencies, error_stats


def test_group_proxies(group_name: str, proxies: List[Dict], clash_path: str,
                       temp_dir: str, port_manager: PortManager, max_workers: int,
                       test_timeout: int, batch_size: int = 30) -> Tuple[List[Dict], Dict, Dict]:
    """Test group with reduced batch size for stability"""
    working_proxies = []
    all_latencies = {}
    all_errors = {}
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

        batch_working, batch_tested, batch_latencies, batch_errors = test_batch_proxies(
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
        
        # Merge error stats
        for error, count in batch_errors.items():
            all_errors[error] = all_errors.get(error, 0) + count
        
        total_tested += batch_tested

        batch_rate = (len(batch_working) / batch_tested * 100) if batch_tested > 0 else 0
        overall_rate = (len(working_proxies) / total_tested * 100) if total_tested > 0 else 0

        print(f"  Batch {batch_num + 1}: {len(batch_working)}/{batch_tested} ({batch_rate:.1f}%)")
        print(f"  Overall: {len(working_proxies)}/{total_tested} ({overall_rate:.1f}%)")
        
        # Show top errors for this batch
        if batch_errors:
            top_errors = sorted(batch_errors.items(), key=lambda x: x[1], reverse=True)[:3]
            print(f"  Top errors: {', '.join([f'{e}({c})' for e, c in top_errors])}")
        
        sys.stdout.flush()
        
        # Add delay between batches for stability
        if batch_num < num_batches - 1:
            time.sleep(1)

    success_rate = (len(working_proxies) / total * 100) if total > 0 else 0
    print(f"\n  {group_name.upper()} Complete: {len(working_proxies)}/{total} ({success_rate:.1f}%)")
    
    # Show overall error summary
    if all_errors:
        print(f"\n  Error Summary:")
        for error, count in sorted(all_errors.items(), key=lambda x: x[1], reverse=True)[:5]:
            print(f"    {error}: {count}")
    
    sys.stdout.flush()

    return working_proxies, {'total': total, 'working': len(working_proxies)}, all_latencies


def test_all_proxies(proxies: List[Dict], clash_path: str, temp_dir: str,
                     max_workers: int = 30) -> Tuple[List[Dict], Dict, Dict]:
    """Test all proxies with reduced concurrency for accuracy"""
    # Reduce default workers to prevent false positives
    max_workers = min(int(os.environ.get('TEST_WORKERS', max_workers)), 30)
    test_timeout = int(os.environ.get('TEST_TIMEOUT', 10))
    batch_size = min(int(os.environ.get('BATCH_SIZE', 30)), 30)

    port_manager = PortManager()

    groups = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        if ptype not in groups:
            groups[ptype] = []
        groups[ptype].append(proxy)

    print(f"\n{'='*60}")
    print(f"Test Configuration (False Positive Prevention Mode)")
    print(f"{'='*60}")
    print(f"Total proxies: {len(proxies)}")
    print(f"Workers: {max_workers} | Timeout: {test_timeout}s | Batch: {batch_size}")
    print(f"Multi-stage validation: HTTP + HTTPS + Content")
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
            
            # Add delay between protocol groups
            time.sleep(2)

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
        'test_method': 'multi_stage_validation',
        'validation_stages': 'HTTP + HTTPS + Content',
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
    print("Advanced Clash Proxy Tester - False Positive Prevention")
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
        print(f"Results directory: {output_dir}/")
    else:
        print("No working proxies found")


if __name__ == '__main__':
    main()
