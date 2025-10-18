"""
Ultimate Proxy Tester - Maximum Speed & Accuracy
Optimized for real-world proxies with varying quality
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


class FastPortManager:
    def __init__(self):
        self.current_port = 20000
        self.max_port = 28000
        self.lock = threading.Lock()
    
    def acquire(self) -> Optional[int]:
        with self.lock:
            if self.current_port >= self.max_port:
                return None
            port = self.current_port
            self.current_port += 1
            return port
    
    def release(self, port: int):
        pass  # No need to track, just increment


def ultra_fast_test(proxy_port: int, timeout: int = 10) -> Tuple[bool, float]:
    """
    Ultra-fast test: Just verify basic connectivity
    No fancy validation - if it connects, it works!
    """
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    # Try 3 different endpoints
    test_urls = [
        'http://www.gstatic.com/generate_204',
        'http://cp.cloudflare.com',
        'http://connectivitycheck.gstatic.com/generate_204'
    ]
    
    for url in test_urls:
        try:
            start = time.time()
            resp = requests.get(url, proxies=proxies, timeout=timeout, verify=False)
            latency = (time.time() - start) * 1000
            
            if resp.status_code in [200, 204]:
                # Success! Try one HTTPS to confirm
                try:
                    requests.get('https://1.1.1.1', proxies=proxies, timeout=timeout, verify=False)
                    return True, latency
                except:
                    return True, latency  # HTTP worked, accept it
        except:
            continue
    
    return False, 0


def quick_clash_start(config_path: str, clash_bin: str, proxy_port: int,
                     control_port: int) -> Optional[subprocess.Popen]:
    """Quick Clash startup"""
    try:
        proc = subprocess.Popen(
            [clash_bin, '-f', config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        time.sleep(1.2)
        
        if proc.poll() is not None:
            return None
        
        # Quick check
        for _ in range(8):
            try:
                resp = requests.get(f'http://127.0.0.1:{control_port}/version', timeout=0.5)
                if resp.status_code == 200:
                    time.sleep(0.2)
                    return proc
            except:
                pass
            time.sleep(0.3)
        
        proc.kill()
        return None
    except:
        return None


def test_proxy_ultra(proxy: Dict, clash_bin: str, temp_dir: str,
                    port_mgr: FastPortManager, timeout: int) -> Tuple[bool, float]:
    """Ultra-fast proxy test"""
    port = port_mgr.acquire()
    if not port:
        return False, 0
    
    ctrl_port = port + 5000
    proc = None
    
    try:
        # Minimal config name
        uid = hashlib.md5(f"{time.time()}{port}".encode()).hexdigest()[:4]
        cfg = os.path.join(temp_dir, f"{uid}.yaml")
        
        config = {
            'port': port,
            'socks-port': port + 1,
            'allow-lan': False,
            'mode': 'global',
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
        
        proc = quick_clash_start(cfg, clash_bin, port, ctrl_port)
        if not proc:
            return False, 0
        
        success, latency = ultra_fast_test(port, timeout)
        
        return success, latency
        
    except:
        return False, 0
    finally:
        if proc:
            try:
                proc.kill()
            except:
                pass
        
        try:
            if 'cfg' in locals():
                os.remove(cfg)
        except:
            pass


def test_mega_batch(proxies: List[Dict], clash_bin: str, temp_dir: str,
                   workers: int, timeout: int) -> List[Dict]:
    """Test mega batch with maximum parallelism"""
    port_mgr = FastPortManager()
    working = []
    lock = threading.Lock()
    completed = 0
    total = len(proxies)
    
    def test_wrapper(proxy):
        nonlocal completed
        success, latency = test_proxy_ultra(proxy, clash_bin, temp_dir, port_mgr, timeout)
        
        with lock:
            completed += 1
            if completed % 50 == 0 or completed == total:
                print(f"\r    Progress: {completed}/{total} ({len(working)} working, {completed/total*100:.1f}%)", 
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
    
    print()  # New line after progress
    return working


def test_protocol_ultra(ptype: str, proxies: List[Dict], clash_bin: str,
                       temp_dir: str, workers: int, timeout: int,
                       batch_size: int) -> List[Dict]:
    """Test protocol with ultra-fast batching"""
    print(f"\n{'='*70}")
    print(f"Testing {ptype.upper()} - {len(proxies)} proxies")
    print(f"{'='*70}")
    
    all_working = []
    num_batches = (len(proxies) + batch_size - 1) // batch_size
    
    for batch_idx in range(num_batches):
        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, len(proxies))
        batch = proxies[start_idx:end_idx]
        
        print(f"\n  Batch {batch_idx + 1}/{num_batches}: Testing {len(batch)} configs...")
        
        working = test_mega_batch(batch, clash_bin, temp_dir, workers, timeout)
        all_working.extend(working)
        
        batch_rate = (len(working) / len(batch) * 100)
        overall_rate = (len(all_working) / end_idx * 100)
        
        print(f"  Batch result: {len(working)}/{len(batch)} ({batch_rate:.1f}%)")
        print(f"  Total so far: {len(all_working)}/{end_idx} ({overall_rate:.1f}%)")
        
        time.sleep(0.3)
    
    print(f"\n  {ptype.upper()} Final: {len(all_working)}/{len(proxies)} ({len(all_working)/len(proxies)*100:.1f}%)")
    return all_working


def test_all_ultra(proxies: List[Dict], clash_bin: str, temp_dir: str) -> List[Dict]:
    """Ultimate testing strategy"""
    
    total = len(proxies)
    
    # Ultra-aggressive settings for speed
    workers = min(int(os.environ.get('TEST_WORKERS', 150)), 200)
    batch_size = min(int(os.environ.get('BATCH_SIZE', 300)), 500)
    
    # Protocol-specific timeouts
    timeouts = {
        'ss': 8,        # SS is usually fast
        'vmess': 10,    # VMess needs more time
        'vless': 12,    # VLESS needs most time
        'trojan': 10,   # Trojan moderate
        'ssr': 8
    }
    
    # Group by protocol
    groups = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown').lower()
        groups.setdefault(ptype, []).append(proxy)
    
    print(f"\n{'='*70}")
    print(f"ULTIMATE SPEED MODE")
    print(f"{'='*70}")
    print(f"Total: {total} proxies")
    print(f"Max Workers: {workers} | Max Batch: {batch_size}")
    print(f"Strategy: Maximum parallelism with adaptive timeouts")
    print(f"\nProtocol Distribution:")
    for ptype, plist in sorted(groups.items()):
        timeout = timeouts.get(ptype, 10)
        print(f"  {ptype.upper()}: {len(plist)} (timeout: {timeout}s)")
    print(f"{'='*70}")
    
    all_working = []
    
    # Test each protocol
    for ptype, plist in sorted(groups.items()):
        timeout = timeouts.get(ptype, 10)
        
        working = test_protocol_ultra(
            ptype, plist, clash_bin, temp_dir, 
            workers, timeout, batch_size
        )
        
        all_working.extend(working)
        time.sleep(0.5)
    
    return all_working


def save_results(proxies: List[Dict], output_dir: str):
    """Save results"""
    os.makedirs(output_dir, exist_ok=True)
    
    # JSON
    with open(os.path.join(output_dir, 'working_proxies.json'), 'w', encoding='utf-8') as f:
        json.dump(proxies, f, indent=2, ensure_ascii=False)
    
    # By protocol
    by_proto_dir = os.path.join(output_dir, 'by_protocol')
    os.makedirs(by_proto_dir, exist_ok=True)
    
    protocols = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        protocols.setdefault(ptype, []).append(proxy)
    
    for ptype, plist in protocols.items():
        with open(os.path.join(by_proto_dir, f'{ptype}.txt'), 'w', encoding='utf-8') as f:
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
    
    # Sort by latency
    sorted_proxies = sorted(proxies, key=lambda x: x.get('latency', 999999))
    with open(os.path.join(output_dir, 'sorted_by_latency.txt'), 'w', encoding='utf-8') as f:
        for proxy in sorted_proxies:
            from utils import proxy_to_share_url
            url = proxy_to_share_url(proxy)
            latency = proxy.get('latency', 0)
            if url:
                f.write(f"{url} # {latency:.0f}ms\n")
    
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
        'test_method': 'ultra_fast',
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
    print("ULTIMATE Proxy Tester - Maximum Speed & Accuracy")
    print("="*70 + "\n")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    temp_dir = os.path.join(base_dir, 'temp_configs')
    output_dir = os.path.join(base_dir, 'working_configs')
    
    # Load proxies
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
    working = test_all_ultra(proxies, clash_bin, temp_dir)
    elapsed = time.time() - start_time
    
    # Results
    print(f"\n{'='*70}")
    print(f"FINAL RESULTS")
    print(f"{'='*70}")
    print(f"Total Tested:    {len(proxies)}")
    print(f"Working Proxies: {len(working)}")
    print(f"Success Rate:    {len(working)/len(proxies)*100:.1f}%")
    print(f"Time Elapsed:    {elapsed:.0f}s ({elapsed/60:.1f} minutes)")
    print(f"Test Speed:      {len(proxies)/elapsed:.1f} proxies/second")
    
    if working:
        print(f"\nBy Protocol:")
        protocols = {}
        for p in working:
            ptype = p.get('type', '?')
            protocols[ptype] = protocols.get(ptype, 0) + 1
        
        for ptype, count in sorted(protocols.items()):
            print(f"  {ptype.upper():<10} {count}")
        
        # Latency stats
        latencies = [p.get('latency', 0) for p in working if p.get('latency', 0) > 0]
        if latencies:
            print(f"\nLatency Stats:")
            print(f"  Average: {sum(latencies)/len(latencies):.0f}ms")
            print(f"  Min:     {min(latencies):.0f}ms")
            print(f"  Max:     {max(latencies):.0f}ms")
            print(f"  Median:  {sorted(latencies)[len(latencies)//2]:.0f}ms")
    
    print(f"{'='*70}\n")
    
    # Save
    if working:
        save_results(working, output_dir)
        print(f"✓ Saved {len(working)} working proxies")
        print(f"  Location: {output_dir}/")
        print(f"  Files:")
        print(f"    - working_proxies.json")
        print(f"    - all_working.txt")
        print(f"    - sorted_by_latency.txt")
        print(f"    - by_protocol/*.txt")
        print(f"    - metadata.json")
    else:
        print("⚠ No working proxies found")
        sys.exit(1)


if __name__ == '__main__':
    main()
