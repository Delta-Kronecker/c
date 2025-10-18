"""
Perfect Balanced Proxy Tester
Fast but with proper validation to avoid false positives
Target: 15-25% success rate, 8-15 proxies/sec
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


class SmartPortManager:
    def __init__(self):
        self.current_port = 20000
        self.max_port = 27000
        self.lock = threading.Lock()
    
    def acquire(self) -> Optional[int]:
        with self.lock:
            if self.current_port >= self.max_port:
                return None
            port = self.current_port
            self.current_port += 1
            return port


def validated_test(proxy_port: int, timeout: int = 10) -> Tuple[bool, float, str]:
    """
    Balanced test with proper validation:
    - Must connect via HTTP
    - Must work via HTTPS 
    - Must return valid content OR have different IP
    """
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    latencies = []
    
    # Test 1: Basic HTTP connectivity (required)
    http_ok = False
    for url in ['http://www.gstatic.com/generate_204', 
                'http://cp.cloudflare.com',
                'http://connectivitycheck.gstatic.com/generate_204']:
        try:
            start = time.time()
            resp = requests.get(url, proxies=proxies, timeout=timeout, verify=False)
            lat = (time.time() - start) * 1000
            
            if resp.status_code in [200, 204]:
                latencies.append(lat)
                http_ok = True
                break
        except requests.exceptions.ProxyError:
            return False, 0, "Proxy error"
        except requests.exceptions.Timeout:
            continue
        except:
            continue
    
    if not http_ok:
        return False, 0, "HTTP failed"
    
    # Test 2: HTTPS validation (required)
    https_ok = False
    for url in ['https://www.gstatic.com/generate_204',
                'https://1.1.1.1',
                'https://www.cloudflare.com/cdn-cgi/trace']:
        try:
            start = time.time()
            resp = requests.get(url, proxies=proxies, timeout=timeout, 
                              verify=False, allow_redirects=True)
            lat = (time.time() - start) * 1000
            
            if resp.status_code in [200, 204, 301, 302, 400]:
                latencies.append(lat)
                https_ok = True
                break
        except:
            continue
    
    if not https_ok:
        return False, 0, "HTTPS failed"
    
    # Test 3: Content validation OR IP check (at least one must pass)
    
    # Try content validation first (faster)
    content_ok = False
    try:
        resp = requests.get('https://www.cloudflare.com/cdn-cgi/trace',
                          proxies=proxies, timeout=timeout, verify=False)
        if resp.status_code == 200:
            content = resp.content.lower()
            # Must contain real Cloudflare trace data
            if b'ip=' in content and b'ts=' in content and len(content) > 50:
                content_ok = True
    except:
        pass
    
    if content_ok:
        avg = sum(latencies) / len(latencies) if latencies else 999
        return True, avg, ""
    
    # If content validation failed, try IP check
    try:
        resp = requests.get('http://ip-api.com/json/?fields=query',
                          proxies=proxies, timeout=min(timeout, 8), verify=False)
        
        if resp.status_code == 200:
            proxy_ip = resp.json().get('query', '')
            
            if proxy_ip:
                # Try to get direct IP
                try:
                    direct_resp = requests.get('http://ip-api.com/json/?fields=query',
                                             timeout=3, verify=False)
                    if direct_resp.status_code == 200:
                        direct_ip = direct_resp.json().get('query', '')
                        
                        if proxy_ip != direct_ip:
                            avg = sum(latencies) / len(latencies) if latencies else 999
                            return True, avg, ""
                        else:
                            return False, 0, "IP leak"
                except:
                    # Can't get direct IP, assume proxy works
                    avg = sum(latencies) / len(latencies) if latencies else 999
                    return True, avg, ""
    except:
        pass
    
    return False, 0, "Validation failed"


def start_clash_fast(config_path: str, clash_bin: str, proxy_port: int,
                    control_port: int) -> Optional[subprocess.Popen]:
    """Fast Clash startup with validation"""
    try:
        proc = subprocess.Popen(
            [clash_bin, '-f', config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        time.sleep(1)
        
        if proc.poll() is not None:
            return None
        
        # Wait for ready
        for attempt in range(10):
            try:
                resp = requests.get(f'http://127.0.0.1:{control_port}/version', timeout=1)
                if resp.status_code == 200:
                    time.sleep(0.3)
                    return proc
            except:
                pass
            time.sleep(0.4)
        
        proc.kill()
        return None
    except:
        return None


def test_single_fast(proxy: Dict, clash_bin: str, temp_dir: str,
                    port_mgr: SmartPortManager, timeout: int) -> Tuple[bool, float, str]:
    """Fast single proxy test with validation"""
    port = port_mgr.acquire()
    if not port:
        return False, 0, "No ports"
    
    ctrl_port = port + 5000
    proc = None
    cfg = None
    
    try:
        uid = hashlib.md5(f"{time.time()}{port}".encode()).hexdigest()[:4]
        cfg = os.path.join(temp_dir, f"{uid}.yaml")
        
        config = {
            'port': port,
            'socks-port': port + 1,
            'allow-lan': False,
            'mode': 'rule',
            'log-level': 'silent',
            'external-controller': f'127.0.0.1:{ctrl_port}',
            'proxies': [proxy_to_clash_format(proxy)],
            'proxy-groups': [{
                'name': 'PROXY',
                'type': 'select',
                'proxies': [proxy.get('name', 'proxy')]
            }],
            'rules': ['MATCH,PROXY']
        }
        
        with open(cfg, 'w', encoding='utf-8') as f:
            yaml.dump(config, f)
        
        proc = start_clash_fast(cfg, clash_bin, port, ctrl_port)
        if not proc:
            return False, 0, "Clash failed"
        
        success, latency, error = validated_test(port, timeout)
        return success, latency, error
        
    except Exception as e:
        return False, 0, f"Error: {type(e).__name__}"
    finally:
        if proc:
            try:
                proc.kill()
            except:
                pass
        
        if cfg:
            try:
                os.remove(cfg)
            except:
                pass


def test_batch_fast(proxies: List[Dict], clash_bin: str, temp_dir: str,
                   workers: int, timeout: int) -> Tuple[List[Dict], Dict]:
    """Fast batch test with progress tracking"""
    port_mgr = SmartPortManager()
    working = []
    errors = {}
    lock = threading.Lock()
    completed = 0
    total = len(proxies)
    
    def test_wrapper(proxy):
        nonlocal completed
        success, latency, error = test_single_fast(proxy, clash_bin, temp_dir, port_mgr, timeout)
        
        with lock:
            completed += 1
            if error and error != "":
                errors[error] = errors.get(error, 0) + 1
            
            if completed % 20 == 0 or completed == total:
                print(f"\r    Testing: {completed}/{total} ({len(working)} working, {completed/total*100:.0f}%)", 
                      end='', flush=True)
        
        if success:
            proxy['latency'] = latency
            return proxy
        return None
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(test_wrapper, p) for p in proxies]
        
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    with lock:
                        working.append(result)
            except:
                pass
    
    print()
    return working, errors


def test_protocol_fast(ptype: str, proxies: List[Dict], clash_bin: str,
                      temp_dir: str, workers: int, timeout: int,
                      batch_size: int) -> List[Dict]:
    """Test protocol with proper batching"""
    print(f"\n{'='*70}")
    print(f"Testing {ptype.upper()} - {len(proxies)} proxies")
    print(f"{'='*70}")
    
    all_working = []
    num_batches = (len(proxies) + batch_size - 1) // batch_size
    
    for batch_idx in range(num_batches):
        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, len(proxies))
        batch = proxies[start_idx:end_idx]
        
        print(f"\n  Batch {batch_idx + 1}/{num_batches} ({len(batch)} configs):")
        
        working, errors = test_batch_fast(batch, clash_bin, temp_dir, workers, timeout)
        all_working.extend(working)
        
        batch_rate = (len(working) / len(batch) * 100)
        overall_rate = (len(all_working) / end_idx * 100)
        
        print(f"  Result: {len(working)}/{len(batch)} ({batch_rate:.1f}%)")
        print(f"  Total:  {len(all_working)}/{end_idx} ({overall_rate:.1f}%)")
        
        if errors:
            top = sorted(errors.items(), key=lambda x: x[1], reverse=True)[:2]
            print(f"  Errors: {', '.join([f'{e}({c})' for e, c in top])}")
        
        time.sleep(0.2)
    
    print(f"\n  {ptype.upper()} Complete: {len(all_working)}/{len(proxies)} ({len(all_working)/len(proxies)*100:.1f}%)")
    return all_working


def test_all_balanced(proxies: List[Dict], clash_bin: str, temp_dir: str) -> List[Dict]:
    """Balanced testing strategy"""
    
    total = len(proxies)
    
    # Balanced settings
    workers = min(int(os.environ.get('TEST_WORKERS', 100)), 120)
    batch_size = min(int(os.environ.get('BATCH_SIZE', 200)), 250)
    
    # Protocol timeouts
    timeouts = {
        'ss': 8,
        'vmess': 10,
        'vless': 12,
        'trojan': 10,
        'ssr': 8
    }
    
    # Group by protocol
    groups = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown').lower()
        groups.setdefault(ptype, []).append(proxy)
    
    print(f"\n{'='*70}")
    print(f"BALANCED MODE - Fast with Validation")
    print(f"{'='*70}")
    print(f"Total: {total} proxies")
    print(f"Workers: {workers} | Batch: {batch_size}")
    print(f"Validation: HTTP + HTTPS + (Content OR IP)")
    print(f"\nProtocol Distribution:")
    for ptype, plist in sorted(groups.items()):
        t = timeouts.get(ptype, 10)
        print(f"  {ptype.upper()}: {len(plist)} (timeout: {t}s)")
    print(f"{'='*70}")
    
    all_working = []
    
    for ptype, plist in sorted(groups.items()):
        timeout = timeouts.get(ptype, 10)
        
        working = test_protocol_fast(
            ptype, plist, clash_bin, temp_dir,
            workers, timeout, batch_size
        )
        
        all_working.extend(working)
        time.sleep(0.3)
    
    return all_working


def save_results(proxies: List[Dict], output_dir: str):
    """Save results in multiple formats"""
    os.makedirs(output_dir, exist_ok=True)
    
    # JSON
    with open(os.path.join(output_dir, 'working_proxies.json'), 'w', encoding='utf-8') as f:
        json.dump(proxies, f, indent=2, ensure_ascii=False)
    
    # By protocol
    by_proto = os.path.join(output_dir, 'by_protocol')
    os.makedirs(by_proto, exist_ok=True)
    
    protocols = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        protocols.setdefault(ptype, []).append(proxy)
    
    for ptype, plist in protocols.items():
        with open(os.path.join(by_proto, f'{ptype}.txt'), 'w', encoding='utf-8') as f:
            for proxy in plist:
                from utils import proxy_to_share_url
                url = proxy_to_share_url(proxy)
                if url:
                    f.write(url + '\n')
    
    # All proxies
    with open(os.path.join(output_dir, 'all_working.txt'), 'w', encoding='utf-8') as f:
        for proxy in proxies:
            from utils import proxy_to_share_url
            url = proxy_to_share_url(proxy)
            if url:
                f.write(url + '\n')
    
    # Sorted by latency
    sorted_proxies = sorted(proxies, key=lambda x: x.get('latency', 999999))
    with open(os.path.join(output_dir, 'sorted_by_latency.txt'), 'w', encoding='utf-8') as f:
        for proxy in sorted_proxies:
            from utils import proxy_to_share_url
            url = proxy_to_share_url(proxy)
            lat = proxy.get('latency', 0)
            if url:
                f.write(f"{url} # {lat:.0f}ms\n")
    
    # Metadata
    latencies = [p.get('latency', 0) for p in proxies if p.get('latency', 0) > 0]
    metadata = {
        'total_working': len(proxies),
        'by_protocol': {ptype: len(plist) for ptype, plist in protocols.items()},
        'latency_stats': {
            'average_ms': round(sum(latencies) / len(latencies), 2) if latencies else 0,
            'min_ms': round(min(latencies), 2) if latencies else 0,
            'max_ms': round(max(latencies), 2) if latencies else 0,
            'median_ms': round(sorted(latencies)[len(latencies)//2], 2) if latencies else 0
        },
        'test_method': 'balanced_validated',
        'validation': 'HTTP + HTTPS + Content/IP',
        'test_date': datetime.now().isoformat(),
        'timestamp': int(time.time())
    }
    
    with open(os.path.join(output_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)


def find_clash() -> Optional[str]:
    paths = ['/usr/local/bin/clash', '/usr/bin/clash', './clash',
             '/home/runner/.local/bin/clash', 'clash.exe']
    
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
    seen = set()
    unique = []
    
    for proxy in proxies:
        h = calculate_proxy_hash(proxy)
        if h not in seen:
            seen.add(h)
            unique.append(proxy)
    
    if len(proxies) != len(unique):
        print(f"Removed {len(proxies) - len(unique)} duplicates")
    
    return unique


def main():
    print("="*70)
    print("Perfect Balanced Proxy Tester")
    print("Fast Speed + Accurate Validation")
    print("="*70 + "\n")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    temp_dir = os.path.join(base_dir, 'temp_configs')
    output_dir = os.path.join(base_dir, 'working_configs')
    
    # Load
    proxies_file = os.path.join(temp_dir, 'parsed_proxies.json')
    if not os.path.exists(proxies_file):
        print(f"Error: {proxies_file} not found")
        sys.exit(1)
    
    with open(proxies_file, 'r', encoding='utf-8') as f:
        proxies = json.load(f)
    
    print(f"Loaded: {len(proxies)} proxies")
    
    proxies = remove_duplicates(proxies)
    print(f"Unique: {len(proxies)} proxies\n")
    
    if not proxies:
        sys.exit(1)
    
    # Find Clash
    clash_bin = find_clash()
    if not clash_bin:
        print("Error: Clash not found")
        sys.exit(1)
    
    print(f"Clash: {clash_bin}")
    
    # Test
    start_time = time.time()
    working = test_all_balanced(proxies, clash_bin, temp_dir)
    elapsed = time.time() - start_time
    
    # Results
    print(f"\n{'='*70}")
    print(f"FINAL RESULTS")
    print(f"{'='*70}")
    print(f"Total Tested:    {len(proxies)}")
    print(f"Working Proxies: {len(working)}")
    print(f"Success Rate:    {len(working)/len(proxies)*100:.1f}%")
    print(f"Time Elapsed:    {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"Test Speed:      {len(proxies)/elapsed:.1f} proxies/sec")
    
    if working:
        print(f"\nBy Protocol:")
        protocols = {}
        for p in working:
            ptype = p.get('type', '?')
            protocols[ptype] = protocols.get(ptype, 0) + 1
        
        for ptype, count in sorted(protocols.items()):
            print(f"  {ptype.upper():<10} {count}")
        
        latencies = [p.get('latency', 0) for p in working if p.get('latency', 0) > 0]
        if latencies:
            print(f"\nLatency:")
            print(f"  Avg: {sum(latencies)/len(latencies):.0f}ms")
            print(f"  Min: {min(latencies):.0f}ms")
            print(f"  Max: {max(latencies):.0f}ms")
    
    print(f"{'='*70}\n")
    
    # Save
    if working:
        save_results(working, output_dir)
        print(f"✓ Saved {len(working)} verified proxies to {output_dir}/")
    else:
        print("⚠ No working proxies")
        sys.exit(1)


if __name__ == '__main__':
    main()
