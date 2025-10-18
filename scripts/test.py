"""
Advanced Proxy Testing System using Clash - CRITICAL FIX
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
from utils import proxy_to_clash_format, calculate_proxy_hash

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

    def start(self, timeout: int = 15) -> bool:
        try:
            self.process = subprocess.Popen(
                [self.clash_binary, '-f', self.config_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            # Wait longer for initialization
            time.sleep(2)
            
            # Check if process crashed
            if self.process.poll() is not None:
                stderr = self.process.stderr.read().decode('utf-8', errors='ignore')
                return False

            # Multiple health checks
            start_time = time.time()
            success_count = 0
            
            while time.time() - start_time < timeout:
                if self._check_health():
                    success_count += 1
                    if success_count >= 3:
                        self.is_ready = True
                        time.sleep(1)  # Extra stabilization
                        return True
                else:
                    success_count = 0
                
                time.sleep(0.5)

            return False
        except Exception as e:
            return False

    def _check_health(self) -> bool:
        try:
            if self.process and self.process.poll() is not None:
                return False
            
            response = requests.get(
                f'http://127.0.0.1:{self.control_port}/version',
                timeout=2
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


def test_proxy_connectivity(proxy_port: int, timeout: int = 15) -> Tuple[bool, float, str]:
    """
    CRITICAL: Test with REAL external connectivity validation
    """
    session = requests.Session()
    session.proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    latencies = []
    errors = []
    
    # Stage 1: Simple connectivity (must pass)
    stage1_pass = False
    for url in ['http://www.gstatic.com/generate_204', 'http://connectivitycheck.gstatic.com/generate_204']:
        try:
            start = time.time()
            resp = session.get(url, timeout=timeout, verify=False)
            latency = (time.time() - start) * 1000
            
            if resp.status_code == 204 and len(resp.content) == 0:
                latencies.append(latency)
                stage1_pass = True
                break
        except Exception as e:
            errors.append(f"Stage1: {type(e).__name__}")
            continue
    
    if not stage1_pass:
        return False, 0, "Stage1 failed: Basic connectivity"
    
    # Stage 2: HTTPS with TLS (must pass)
    stage2_pass = False
    for url in ['https://www.gstatic.com/generate_204', 'https://www.cloudflare.com/cdn-cgi/trace']:
        try:
            start = time.time()
            resp = session.get(url, timeout=timeout, verify=False)
            latency = (time.time() - start) * 1000
            
            if resp.status_code in [200, 204] and len(resp.content) >= 0:
                latencies.append(latency)
                stage2_pass = True
                break
        except Exception as e:
            errors.append(f"Stage2: {type(e).__name__}")
            continue
    
    if not stage2_pass:
        return False, 0, "Stage2 failed: HTTPS/TLS"
    
    # Stage 3: Real content fetch with validation (CRITICAL)
    stage3_pass = False
    test_sites = [
        {
            'url': 'https://www.google.com/humans.txt',
            'min_size': 50,
            'keywords': [b'Google', b'humans']
        },
        {
            'url': 'https://www.cloudflare.com',
            'min_size': 1000,
            'keywords': [b'cloudflare', b'html', b'<!DOCTYPE']
        }
    ]
    
    for site in test_sites:
        try:
            start = time.time()
            resp = session.get(site['url'], timeout=timeout, verify=False, allow_redirects=True)
            latency = (time.time() - start) * 1000
            
            # Must be 200 OK
            if resp.status_code != 200:
                errors.append(f"Stage3: Status {resp.status_code}")
                continue
            
            content = resp.content.lower()
            content_len = len(content)
            
            # Must have minimum size
            if content_len < site['min_size']:
                errors.append(f"Stage3: Content too small ({content_len})")
                continue
            
            # Must contain expected keywords
            keyword_found = False
            for keyword in site['keywords']:
                if keyword.lower() in content:
                    keyword_found = True
                    break
            
            if not keyword_found:
                errors.append(f"Stage3: Content validation failed")
                continue
            
            latencies.append(latency)
            stage3_pass = True
            break
            
        except requests.exceptions.Timeout:
            errors.append("Stage3: Timeout")
            continue
        except requests.exceptions.ProxyError:
            errors.append("Stage3: Proxy error")
            continue
        except requests.exceptions.SSLError:
            errors.append("Stage3: SSL error")
            continue
        except Exception as e:
            errors.append(f"Stage3: {type(e).__name__}")
            continue
    
    if not stage3_pass:
        error_msg = "; ".join(errors[-3:]) if errors else "Unknown"
        return False, 0, f"Stage3 failed: {error_msg}"
    
    # Stage 4: IP leak check (verify we're actually using proxy)
    try:
        # Get IP through proxy
        resp = session.get('https://api.ipify.org?format=json', timeout=10, verify=False)
        if resp.status_code == 200:
            proxy_ip = resp.json().get('ip', '')
            
            # Get direct IP (without proxy)
            direct_resp = requests.get('https://api.ipify.org?format=json', timeout=10, verify=False)
            if direct_resp.status_code == 200:
                direct_ip = direct_resp.json().get('ip', '')
                
                # IPs must be different
                if proxy_ip == direct_ip:
                    return False, 0, "Stage4 failed: IP leak detected (not using proxy)"
    except Exception as e:
        # This stage is optional, don't fail if check fails
        pass
    
    avg_latency = sum(latencies) / len(latencies) if latencies else 999999
    return True, avg_latency, ""


def test_single_proxy(proxy: Dict, clash_path: str, config_dir: str,
                      port_manager: PortManager, test_timeout: int = 15) -> Tuple[bool, float, str]:
    proxy_port = port_manager.acquire_port()
    if not proxy_port:
        return False, 0, "No ports available"

    control_port = proxy_port + 1000

    try:
        # Create config
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', proxy.get('name', 'proxy'))[:50]
        unique_id = hashlib.md5(
            f"{proxy.get('server', '')}:{proxy.get('port', '')}{time.time()}".encode()
        ).hexdigest()[:8]
        config_file = os.path.join(config_dir, f"test_{unique_id}_{safe_name}.yaml")

        clash_proxy = proxy_to_clash_format(proxy)
        config = {
            'port': proxy_port,
            'socks-port': proxy_port + 1,
            'allow-lan': False,
            'mode': 'global',
            'log-level': 'silent',
            'external-controller': f'127.0.0.1:{control_port}',
            'proxies': [clash_proxy],
            'proxy-groups': [{
                'name': 'PROXY',
                'type': 'select',
                'proxies': [clash_proxy['name']]
            }],
            'rules': ['MATCH,PROXY']
        }

        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        # Test with Clash
        with clash_context(config_file, clash_path, proxy_port, control_port) as instance:
            if not instance or not instance.is_ready:
                return False, 0, "Clash failed to start"

            # Wait for full initialization
            time.sleep(2)
            
            success, latency, error = test_proxy_connectivity(proxy_port, test_timeout)
            return success, latency, error

    except Exception as e:
        return False, 0, f"Exception: {str(e)}"
    finally:
        port_manager.release_port(proxy_port)
        time.sleep(0.3)


def test_batch_proxies(batch_num: int, batch_proxies: List[Dict],
                       clash_path: str, temp_dir: str, port_manager: PortManager,
                       max_workers: int, test_timeout: int) -> Tuple[List[Dict], int, Dict, Dict]:
    working_proxies = []
    completed = 0
    latencies = {}
    error_stats = {}
    lock = threading.Lock()

    def test_wrapper(proxy_data):
        idx, proxy = proxy_data
        result, latency, error = test_single_proxy(proxy, clash_path, temp_dir, port_manager, test_timeout)
        return idx, proxy, result, latency, error

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(test_wrapper, (i, proxy)): i
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
                        error_stats[error] = error_stats.get(error, 0) + 1

            except Exception as e:
                with lock:
                    completed += 1
                    error_stats[f"Exception: {type(e).__name__}"] = error_stats.get(f"Exception: {type(e).__name__}", 0) + 1

    return working_proxies, completed, latencies, error_stats


def remove_duplicate_proxies(proxies: List[Dict]) -> List[Dict]:
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

    return unique_proxies


def load_parsed_proxies(file_path: str) -> List[Dict]:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            proxies = json.load(f)
        print(f"Loaded {len(proxies)} parsed proxies")
        proxies = remove_duplicate_proxies(proxies)
        return proxies
    except Exception as e:
        print(f"Error: {e}")
        return []


def test_group_proxies(group_name: str, proxies: List[Dict], clash_path: str,
                       temp_dir: str, port_manager: PortManager, max_workers: int,
                       test_timeout: int, batch_size: int = 20) -> Tuple[List[Dict], Dict, Dict]:
    working_proxies = []
    all_latencies = {}
    all_errors = {}
    total = len(proxies)

    print(f"\n{'='*70}")
    print(f"Testing {group_name.upper()} - {total} proxies")
    print(f"{'='*70}")

    num_batches = (total + batch_size - 1) // batch_size
    total_tested = 0

    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, total)
        batch_proxies = proxies[start_idx:end_idx]

        print(f"\n  Batch {batch_num + 1}/{num_batches} - Testing {len(batch_proxies)} configs...")
        sys.stdout.flush()

        batch_working, batch_tested, batch_latencies, batch_errors = test_batch_proxies(
            batch_num + 1, batch_proxies, clash_path, temp_dir,
            port_manager, max_workers, test_timeout
        )

        working_proxies.extend(batch_working)
        all_latencies.update(batch_latencies)
        
        for error, count in batch_errors.items():
            all_errors[error] = all_errors.get(error, 0) + count
        
        total_tested += batch_tested
        batch_rate = (len(batch_working) / batch_tested * 100) if batch_tested > 0 else 0
        overall_rate = (len(working_proxies) / total_tested * 100) if total_tested > 0 else 0

        print(f"  Batch {batch_num + 1}: {len(batch_working)}/{batch_tested} working ({batch_rate:.1f}%)")
        print(f"  Overall: {len(working_proxies)}/{total_tested} working ({overall_rate:.1f}%)")
        
        if batch_errors:
            top_errors = sorted(batch_errors.items(), key=lambda x: x[1], reverse=True)[:2]
            print(f"  Errors: {', '.join([f'{e}({c})' for e, c in top_errors])}")
        
        sys.stdout.flush()
        time.sleep(1)

    print(f"\n  {group_name.upper()}: {len(working_proxies)}/{total} working ({len(working_proxies)/total*100:.1f}%)")
    if all_errors:
        print(f"  Top errors:")
        for error, count in sorted(all_errors.items(), key=lambda x: x[1], reverse=True)[:3]:
            print(f"    - {error}: {count}")
    sys.stdout.flush()

    return working_proxies, {'total': total, 'working': len(working_proxies)}, all_latencies


def test_all_proxies(proxies: List[Dict], clash_path: str, temp_dir: str,
                     max_workers: int = 20) -> Tuple[List[Dict], Dict, Dict]:
    max_workers = min(int(os.environ.get('TEST_WORKERS', max_workers)), 20)
    test_timeout = int(os.environ.get('TEST_TIMEOUT', 15))
    batch_size = min(int(os.environ.get('BATCH_SIZE', 20)), 20)

    port_manager = PortManager()
    groups = {}
    
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        if ptype not in groups:
            groups[ptype] = []
        groups[ptype].append(proxy)

    print(f"\n{'='*70}")
    print(f"STRICT VALIDATION MODE - Real Connectivity Testing")
    print(f"{'='*70}")
    print(f"Total: {len(proxies)} | Workers: {max_workers} | Timeout: {test_timeout}s")
    print(f"Validation: HTTP + HTTPS + Content + IP Leak Check")
    for ptype, plist in sorted(groups.items()):
        print(f"  {ptype.upper()}: {len(plist)}")
    print(f"{'='*70}")

    all_working = []
    group_stats = {}
    all_latencies = {}

    for group_name, group_proxies in sorted(groups.items()):
        try:
            working, stats, latencies = test_group_proxies(
                group_name, group_proxies, clash_path, temp_dir,
                port_manager, max_workers, test_timeout, batch_size
            )
            all_working.extend(working)
            group_stats[group_name] = stats
            all_latencies.update(latencies)
            time.sleep(2)
        except Exception as e:
            print(f"Error testing {group_name}: {e}")
            group_stats[group_name] = {'total': len(group_proxies), 'working': 0}

    return all_working, group_stats, all_latencies


def save_working_configs(proxies: List[Dict], output_dir: str, latencies: Dict):
    os.makedirs(output_dir, exist_ok=True)

    with open(os.path.join(output_dir, 'working_proxies.json'), 'w', encoding='utf-8') as f:
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
        with open(os.path.join(by_protocol_dir, f'{ptype}.txt'), 'w', encoding='utf-8') as f:
            for proxy in plist:
                from utils import proxy_to_share_url
                f.write(proxy_to_share_url(proxy) + '\n')

    with open(os.path.join(output_dir, 'all_working.txt'), 'w', encoding='utf-8') as f:
        for proxy in proxies:
            from utils import proxy_to_share_url
            f.write(proxy_to_share_url(proxy) + '\n')

    latency_values = [v for v in latencies.values() if v > 0]
    metadata = {
        'total_working': len(proxies),
        'by_protocol': {ptype: len(plist) for ptype, plist in protocols.items()},
        'latency': {
            'average': sum(latency_values) / len(latency_values) if latency_values else 0,
            'min': min(latency_values) if latency_values else 0,
            'max': max(latency_values) if latency_values else 0
        },
        'validation': '4-stage: HTTP + HTTPS + Content + IP',
        'last_updated': datetime.now().isoformat()
    }

    with open(os.path.join(output_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)


def find_clash_binary() -> Optional[str]:
    paths = ['/usr/local/bin/clash', '/usr/bin/clash', './clash', 'clash.exe']
    for path in paths:
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
    print("="*70)
    print("Clash Proxy Tester - STRICT VALIDATION")
    print("="*70 + "\n")

    base_dir = os.path.dirname(os.path.dirname(__file__))
    temp_dir = os.path.join(base_dir, 'temp_configs')
    output_dir = os.path.join(base_dir, 'working_configs')

    proxies = load_parsed_proxies(os.path.join(temp_dir, 'parsed_proxies.json'))
    if not proxies:
        sys.exit(1)

    clash_path = find_clash_binary()
    if not clash_path:
        print("Error: Clash not found")
        sys.exit(1)

    print(f"Clash: {clash_path}")

    working_proxies, group_stats, latencies = test_all_proxies(proxies, clash_path, temp_dir)

    print(f"\n{'='*70}")
    print(f"RESULTS")
    print(f"{'='*70}")
    for protocol, stats in sorted(group_stats.items()):
        rate = (stats['working'] / stats['total'] * 100) if stats['total'] > 0 else 0
        print(f"{protocol.upper()}: {stats['working']}/{stats['total']} ({rate:.1f}%)")
    
    total_rate = (len(working_proxies) / len(proxies) * 100) if proxies else 0
    print(f"TOTAL: {len(working_proxies)}/{len(proxies)} ({total_rate:.1f}%)")
    print(f"{'='*70}\n")

    if working_proxies:
        save_working_configs(working_proxies, output_dir, latencies)
        print(f"Saved {len(working_proxies)} VERIFIED working proxies")
    else:
        print("No working proxies found")


if __name__ == '__main__':
    main()
