"""
Balanced Proxy Testing - Accurate without being too strict
Optimized for real-world usage
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import re
import hashlib
import warnings

warnings.filterwarnings('ignore')
requests.packages.urllib3.disable_warnings()

from utils import proxy_to_clash_format, calculate_proxy_hash


class PortManager:
    def __init__(self):
        self.start_port = 20000
        self.end_port = 30000
        self.used_ports = set()
        self.lock = threading.Lock()
    
    def acquire(self) -> Optional[int]:
        with self.lock:
            for port in range(self.start_port, self.end_port):
                if port not in self.used_ports:
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.bind(('127.0.0.1', port))
                        sock.close()
                        self.used_ports.add(port)
                        return port
                    except:
                        continue
            return None
    
    def release(self, port: int):
        with self.lock:
            self.used_ports.discard(port)


def balanced_proxy_test(proxy_port: int, timeout: int = 10) -> Tuple[bool, float, str]:
    """
    Balanced testing approach:
    - Must pass basic connectivity
    - Must have different IP OR pass content validation
    - Not too strict on edge cases
    """
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    latencies = []
    has_different_ip = False
    
    # Test 1: Quick connectivity check
    connectivity_ok = False
    for url in ['http://www.gstatic.com/generate_204', 
                'http://connectivitycheck.gstatic.com/generate_204',
                'http://cp.cloudflare.com']:
        try:
            start = time.time()
            resp = requests.get(url, proxies=proxies, timeout=timeout, verify=False)
            lat = (time.time() - start) * 1000
            
            if resp.status_code in [200, 204]:
                latencies.append(lat)
                connectivity_ok = True
                break
        except requests.exceptions.ProxyError:
            continue
        except requests.exceptions.Timeout:
            continue
        except:
            continue
    
    if not connectivity_ok:
        return False, 0, "No connectivity"
    
    # Test 2: IP Check (soft fail - if this fails, we need stronger content validation)
    try:
        resp = requests.get('http://ip-api.com/json/?fields=query', 
                          proxies=proxies, timeout=8, verify=False)
        
        if resp.status_code == 200:
            proxy_ip = resp.json().get('query', '')
            
            # Try to get direct IP for comparison
            try:
                direct_resp = requests.get('http://ip-api.com/json/?fields=query', 
                                          timeout=3, verify=False)
                if direct_resp.status_code == 200:
                    direct_ip = direct_resp.json().get('query', '')
                    
                    if proxy_ip and direct_ip and proxy_ip != direct_ip:
                        has_different_ip = True
                        latencies.append((time.time() - start) * 1000)
            except:
                # If we can't get direct IP, assume proxy is working
                has_different_ip = True
    except:
        # IP check failed, we'll rely on content validation
        pass
    
    # Test 3: HTTPS + Content (required if IP check failed)
    https_ok = False
    
    # Try multiple HTTPS endpoints
    https_tests = [
        ('https://www.gstatic.com/generate_204', 204, 0, None),
        ('https://1.1.1.1', [200, 301, 302], 0, None),
        ('https://www.cloudflare.com/cdn-cgi/trace', 200, 10, [b'ip=', b'ts=']),
        ('https://www.google.com/humans.txt', 200, 20, [b'google', b'human'])
    ]
    
    for url, expected_status, min_size, keywords in https_tests:
        try:
            start = time.time()
            resp = requests.get(url, proxies=proxies, timeout=timeout, 
                              verify=False, allow_redirects=True)
            lat = (time.time() - start) * 1000
            
            # Check status
            if isinstance(expected_status, list):
                status_ok = resp.status_code in expected_status
            else:
                status_ok = resp.status_code == expected_status
            
            if not status_ok:
                continue
            
            # Check size
            if len(resp.content) < min_size:
                continue
            
            # Check keywords if provided
            if keywords:
                content = resp.content.lower()
                keyword_found = any(kw.lower() in content for kw in keywords)
                if not keyword_found:
                    continue
            
            latencies.append(lat)
            https_ok = True
            break
            
        except:
            continue
    
    # Decision logic:
    # - If we have different IP, we only need connectivity
    # - If we don't have different IP, we MUST have HTTPS + content validation
    if has_different_ip:
        # Proxy is verified by IP difference
        avg_latency = sum(latencies) / len(latencies) if latencies else 999999
        return True, avg_latency, ""
    
    elif https_ok:
        # Even without IP verification, HTTPS content validation passed
        avg_latency = sum(latencies) / len(latencies) if latencies else 999999
        return True, avg_latency, ""
    
    else:
        # Failed both IP check and content validation
        return False, 0, "Failed validation"


def start_clash(config_path: str, clash_bin: str, proxy_port: int, 
                control_port: int) -> Optional[subprocess.Popen]:
    """Start Clash instance"""
    try:
        proc = subprocess.Popen(
            [clash_bin, '-f', config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        # Wait for initialization
        time.sleep(1.5)
        
        if proc.poll() is not None:
            return None
        
        # Health check
        for _ in range(10):
            try:
                resp = requests.get(f'http://127.0.0.1:{control_port}/version', timeout=1)
                if resp.status_code == 200:
                    time.sleep(0.5)
                    return proc
            except:
                pass
            time.sleep(0.5)
        
        proc.kill()
        return None
        
    except:
        return None


def test_single_proxy(proxy: Dict, clash_bin: str, temp_dir: str,
                      port_mgr: PortManager, timeout: int) -> Tuple[bool, float, str]:
    """Test a single proxy"""
    port = port_mgr.acquire()
    if not port:
        return False, 0, "No ports"
    
    ctrl_port = port + 1000
    proc = None
    config_file = None
    
    try:
        # Create config
        name = re.sub(r'[<>:"/\\|?*]', '_', proxy.get('name', 'proxy'))[:50]
        uid = hashlib.md5(
            f"{proxy.get('server')}:{proxy.get('port')}{time.time()}".encode()
        ).hexdigest()[:8]
        config_file = os.path.join(temp_dir, f"test_{uid}.yaml")
        
        clash_proxy = proxy_to_clash_format(proxy)
        
        # Balanced config - allows some fallback but prefers proxy
        config = {
            'port': port,
            'socks-port': port + 1,
            'allow-lan': False,
            'mode': 'rule',
            'log-level': 'silent',
            'external-controller': f'127.0.0.1:{ctrl_port}',
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
        
        # Start Clash
        proc = start_clash(config_file, clash_bin, port, ctrl_port)
        if not proc:
            return False, 0, "Clash failed"
        
        # Test proxy
        success, latency, error = balanced_proxy_test(port, timeout)
        
        return success, latency, error
        
    except Exception as e:
        return False, 0, f"Error: {type(e).__name__}"
    
    finally:
        # Cleanup
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except:
                try:
                    proc.kill()
                except:
                    pass
        
        port_mgr.release(port)
        
        if config_file and os.path.exists(config_file):
            try:
                os.remove(config_file)
            except:
                pass
        
        time.sleep(0.1)


def test_batch(batch_proxies: List[Dict], clash_bin: str, temp_dir: str,
               workers: int, timeout: int) -> Tuple[List[Dict], Dict]:
    """Test a batch of proxies"""
    port_mgr = PortManager()
    working = []
    errors = {}
    lock = threading.Lock()
    
    def test_wrapper(idx_proxy):
        idx, proxy = idx_proxy
        result, latency, error = test_single_proxy(proxy, clash_bin, temp_dir, port_mgr, timeout)
        return idx, proxy, result, latency, error
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(test_wrapper, (i, p)): i 
                   for i, p in enumerate(batch_proxies, 1)}
        
        for future in as_completed(futures):
            try:
                idx, proxy, result, latency, error = future.result()
                
                with lock:
                    if result:
                        proxy['latency'] = latency
                        working.append(proxy)
                    else:
                        errors[error] = errors.get(error, 0) + 1
            except Exception as e:
                with lock:
                    errors[f"Exception: {type(e).__name__}"] = errors.get(f"Exception: {type(e).__name__}", 0) + 1
    
    return working, errors


def test_protocol_group(ptype: str, proxies: List[Dict], clash_bin: str,
                       temp_dir: str, workers: int, timeout: int, 
                       batch_size: int) -> List[Dict]:
    """Test all proxies of a specific protocol"""
    print(f"\n{'='*70}")
    print(f"Testing {ptype.upper()} - {len(proxies)} proxies")
    print(f"{'='*70}")
    
    all_working = []
    num_batches = (len(proxies) + batch_size - 1) // batch_size
    total_tested = 0
    
    for batch_idx in range(num_batches):
        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, len(proxies))
        batch_proxies = proxies[start_idx:end_idx]
        
        print(f"\n  Batch {batch_idx + 1}/{num_batches}: Testing {len(batch_proxies)} configs...", 
              end=' ', flush=True)
        
        batch_working, batch_errors = test_batch(
            batch_proxies, clash_bin, temp_dir, workers, timeout
        )
        
        all_working.extend(batch_working)
        total_tested += len(batch_proxies)
        
        batch_rate = (len(batch_working) / len(batch_proxies) * 100) if batch_proxies else 0
        overall_rate = (len(all_working) / total_tested * 100) if total_tested > 0 else 0
        
        print(f"{len(batch_working)}/{len(batch_proxies)} ({batch_rate:.1f}%)")
        
        if batch_errors:
            top_errors = sorted(batch_errors.items(), key=lambda x: x[1], reverse=True)[:2]
            print(f"    Top errors: {', '.join([f'{e}({c})' for e, c in top_errors])}")
        
        print(f"    Overall: {len(all_working)}/{total_tested} ({overall_rate:.1f}%)", flush=True)
        
        time.sleep(0.5)
    
    print(f"\n  {ptype.upper()} Complete: {len(all_working)}/{len(proxies)} ({len(all_working)/len(proxies)*100:.1f}%)")
    
    return all_working


def test_all_proxies(proxies: List[Dict], clash_bin: str, temp_dir: str) -> List[Dict]:
    """Test all proxies grouped by protocol"""
    
    # Get settings
    workers = min(int(os.environ.get('TEST_WORKERS', 50)), 60)
    timeout = int(os.environ.get('TEST_TIMEOUT', 10))
    batch_size = min(int(os.environ.get('BATCH_SIZE', 40)), 50)
    
    # Group by protocol
    groups = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        groups.setdefault(ptype, []).append(proxy)
    
    print(f"\n{'='*70}")
    print(f"BALANCED TESTING MODE")
    print(f"{'='*70}")
    print(f"Total Proxies: {len(proxies)}")
    print(f"Workers: {workers} | Timeout: {timeout}s | Batch: {batch_size}")
    print(f"\nValidation Strategy:")
    print(f"  - Basic connectivity (required)")
    print(f"  - IP verification OR content validation (one required)")
    print(f"  - Multiple fallback tests for reliability")
    print(f"\nProtocol Distribution:")
    for ptype, plist in sorted(groups.items()):
        print(f"  {ptype.upper()}: {len(plist)}")
    print(f"{'='*70}")
    
    all_working = []
    
    for ptype, plist in sorted(groups.items()):
        working = test_protocol_group(
            ptype, plist, clash_bin, temp_dir, workers, timeout, batch_size
        )
        all_working.extend(working)
        time.sleep(1)
    
    return all_working


def save_results(proxies: List[Dict], output_dir: str):
    """Save working proxies in multiple formats"""
    os.makedirs(output_dir, exist_ok=True)
    
    # JSON format
    with open(os.path.join(output_dir, 'working_proxies.json'), 'w', encoding='utf-8') as f:
        json.dump(proxies, f, indent=2, ensure_ascii=False)
    
    # By protocol directory
    by_proto_dir = os.path.join(output_dir, 'by_protocol')
    os.makedirs(by_proto_dir, exist_ok=True)
    
    protocols = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        protocols.setdefault(ptype, []).append(proxy)
    
    # Save each protocol separately
    for ptype, plist in protocols.items():
        with open(os.path.join(by_proto_dir, f'{ptype}.txt'), 'w', encoding='utf-8') as f:
            for proxy in plist:
                from utils import proxy_to_share_url
                url = proxy_to_share_url(proxy)
                if url:
                    f.write(url + '\n')
    
    # All proxies in one file
    with open(os.path.join(output_dir, 'all_working.txt'), 'w', encoding='utf-8') as f:
        for proxy in proxies:
            from utils import proxy_to_share_url
            url = proxy_to_share_url(proxy)
            if url:
                f.write(url + '\n')
    
    # Metadata
    latencies = [p.get('latency', 0) for p in proxies if p.get('latency', 0) > 0]
    metadata = {
        'total_working': len(proxies),
        'by_protocol': {ptype: len(plist) for ptype, plist in protocols.items()},
        'latency_stats': {
            'average_ms': round(sum(latencies) / len(latencies), 2) if latencies else 0,
            'min_ms': round(min(latencies), 2) if latencies else 0,
            'max_ms': round(max(latencies), 2) if latencies else 0
        },
        'test_method': 'balanced_validation',
        'validation': 'Connectivity + (IP Check OR Content Validation)',
        'last_updated': datetime.now().isoformat(),
        'timestamp': int(time.time())
    }
    
    with open(os.path.join(output_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)
    
    with open(os.path.join(output_dir, 'last_updated.txt'), 'w', encoding='utf-8') as f:
        f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'))


def find_clash_binary() -> Optional[str]:
    """Find Clash binary in common locations"""
    paths = [
        '/usr/local/bin/clash',
        '/usr/bin/clash',
        './clash',
        '/home/runner/.local/bin/clash',
        'clash.exe',
        './clash.exe'
    ]
    
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


def remove_duplicates(proxies: List[Dict]) -> List[Dict]:
    """Remove duplicate proxies based on hash"""
    seen = set()
    unique = []
    
    for proxy in proxies:
        h = calculate_proxy_hash(proxy)
        if h not in seen:
            seen.add(h)
            unique.append(proxy)
    
    dup_count = len(proxies) - len(unique)
    if dup_count > 0:
        print(f"Removed {dup_count} duplicate configs")
    
    return unique


def main():
    print("=" * 70)
    print("Balanced Proxy Tester - Accurate & Practical")
    print("=" * 70 + "\n")
    
    # Setup paths
    base_dir = os.path.dirname(os.path.dirname(__file__))
    temp_dir = os.path.join(base_dir, 'temp_configs')
    output_dir = os.path.join(base_dir, 'working_configs')
    
    # Load proxies
    proxies_file = os.path.join(temp_dir, 'parsed_proxies.json')
    if not os.path.exists(proxies_file):
        print(f"Error: {proxies_file} not found")
        print("Please run download script first")
        sys.exit(1)
    
    with open(proxies_file, 'r', encoding='utf-8') as f:
        proxies = json.load(f)
    
    print(f"Loaded {len(proxies)} parsed proxies")
    
    # Remove duplicates
    proxies = remove_duplicates(proxies)
    print(f"Unique proxies: {len(proxies)}\n")
    
    if not proxies:
        print("No proxies to test")
        sys.exit(1)
    
    # Find Clash binary
    clash_bin = find_clash_binary()
    if not clash_bin:
        print("Error: Clash binary not found")
        print("Please install Clash or Clash Meta")
        sys.exit(1)
    
    print(f"Using Clash: {clash_bin}")
    
    # Test all proxies
    start_time = time.time()
    working_proxies = test_all_proxies(proxies, clash_bin, temp_dir)
    elapsed = time.time() - start_time
    
    # Print results
    print(f"\n{'='*70}")
    print(f"TEST RESULTS")
    print(f"{'='*70}")
    print(f"Total Tested:     {len(proxies)}")
    print(f"Working Proxies:  {len(working_proxies)}")
    print(f"Success Rate:     {len(working_proxies)/len(proxies)*100:.1f}%")
    print(f"Time Elapsed:     {elapsed:.1f}s ({elapsed/60:.1f} minutes)")
    print(f"Test Speed:       {len(proxies)/elapsed:.1f} proxies/sec")
    
    # Protocol breakdown
    if working_proxies:
        print(f"\nBy Protocol:")
        protocols = {}
        for proxy in working_proxies:
            ptype = proxy.get('type', 'unknown')
            protocols[ptype] = protocols.get(ptype, 0) + 1
        
        for ptype, count in sorted(protocols.items()):
            print(f"  {ptype.upper():<10} {count}")
    
    print(f"{'='*70}\n")
    
    # Save results
    if working_proxies:
        save_results(working_proxies, output_dir)
        print(f"✓ Saved {len(working_proxies)} verified working proxies")
        print(f"  Output directory: {output_dir}/")
        print(f"  - working_proxies.json")
        print(f"  - all_working.txt")
        print(f"  - by_protocol/*.txt")
        print(f"  - metadata.json")
    else:
        print("⚠ No working proxies found")
        sys.exit(1)


if __name__ == '__main__':
    main()
