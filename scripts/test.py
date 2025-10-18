"""
Final Optimized Proxy Tester
- Smart timeout management
- Retry failed proxies with longer timeout
- Better Clash initialization
- Optimized for real-world conditions
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
    """Optimized port manager with cooldown"""
    def __init__(self):
        self.start_port = 20000
        self.end_port = 25000
        self.used_ports = set()
        self.cooldown = {}  # port -> release_time
        self.lock = threading.Lock()
    
    def acquire(self) -> Optional[int]:
        with self.lock:
            current_time = time.time()
            
            # Clean old cooldowns
            self.cooldown = {p: t for p, t in self.cooldown.items() 
                           if current_time - t < 3}
            
            for port in range(self.start_port, self.end_port):
                if port in self.used_ports:
                    continue
                if port in self.cooldown:
                    continue
                    
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
            self.cooldown[port] = time.time()


def smart_proxy_test(proxy_port: int, timeout: int = 12, quick_mode: bool = False) -> Tuple[bool, float, str]:
    """
    Smart testing with adaptive approach
    """
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    latencies = []
    
    # Quick connectivity test first
    basic_tests = [
        'http://www.gstatic.com/generate_204',
        'http://connectivitycheck.gstatic.com/generate_204',
        'http://cp.cloudflare.com'
    ]
    
    connected = False
    for url in basic_tests:
        try:
            start = time.time()
            resp = requests.get(url, proxies=proxies, timeout=timeout, verify=False)
            lat = (time.time() - start) * 1000
            
            if resp.status_code in [200, 204]:
                latencies.append(lat)
                connected = True
                break
        except:
            continue
    
    if not connected:
        return False, 0, "No connectivity"
    
    # If in quick mode and connected, accept it
    if quick_mode:
        return True, latencies[0] if latencies else 999, ""
    
    # For non-quick mode, do additional validation
    # Try HTTPS
    https_ok = False
    https_tests = [
        ('https://www.gstatic.com/generate_204', 204, None),
        ('https://1.1.1.1', [200, 301, 302, 400], None),
        ('https://www.cloudflare.com/cdn-cgi/trace', 200, [b'ip=']),
    ]
    
    for url, status, keywords in https_tests:
        try:
            start = time.time()
            resp = requests.get(url, proxies=proxies, timeout=timeout, 
                              verify=False, allow_redirects=True)
            lat = (time.time() - start) * 1000
            
            # Check status
            if isinstance(status, list):
                status_ok = resp.status_code in status
            else:
                status_ok = resp.status_code == status
            
            if not status_ok:
                continue
            
            # Check keywords if provided
            if keywords:
                content = resp.content.lower()
                if not any(kw.lower() in content for kw in keywords):
                    continue
            
            latencies.append(lat)
            https_ok = True
            break
        except:
            continue
    
    if https_ok or len(latencies) >= 2:
        avg_lat = sum(latencies) / len(latencies) if latencies else 999
        return True, avg_lat, ""
    
    return False, 0, "Validation failed"


def start_clash_smart(config_path: str, clash_bin: str, proxy_port: int,
                     control_port: int, max_wait: int = 15) -> Optional[subprocess.Popen]:
    """Smart Clash startup with better error handling"""
    try:
        proc = subprocess.Popen(
            [clash_bin, '-f', config_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        # Progressive wait
        time.sleep(1)
        
        if proc.poll() is not None:
            return None
        
        # Quick health checks
        for attempt in range(max_wait):
            try:
                resp = requests.get(f'http://127.0.0.1:{control_port}/version', timeout=1)
                if resp.status_code == 200:
                    # Extra check - verify proxy port is listening
                    time.sleep(0.3)
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex(('127.0.0.1', proxy_port))
                    sock.close()
                    
                    if result == 0:
                        return proc
            except:
                pass
            
            if attempt < 3:
                time.sleep(0.5)
            else:
                time.sleep(1)
        
        proc.kill()
        return None
        
    except:
        return None


def test_single_proxy_smart(proxy: Dict, clash_bin: str, temp_dir: str,
                           port_mgr: SmartPortManager, timeout: int,
                           quick_mode: bool = False) -> Tuple[bool, float, str]:
    """Test single proxy with smart approach"""
    port = port_mgr.acquire()
    if not port:
        return False, 0, "No ports"
    
    ctrl_port = port + 2000
    proc = None
    config_file = None
    
    try:
        # Create config
        name = re.sub(r'[<>:"/\\|?*]', '_', proxy.get('name', 'proxy'))[:40]
        uid = hashlib.md5(
            f"{proxy.get('server')}:{proxy.get('port')}{time.time()}".encode()
        ).hexdigest()[:6]
        config_file = os.path.join(temp_dir, f"c_{uid}.yaml")
        
        clash_proxy = proxy_to_clash_format(proxy)
        
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
            yaml.dump(config, f, allow_unicode=True)
        
        # Start Clash
        proc = start_clash_smart(config_file, clash_bin, port, ctrl_port, max_wait=timeout)
        if not proc:
            return False, 0, "Clash start failed"
        
        # Test
        success, latency, error = smart_proxy_test(port, timeout, quick_mode)
        
        return success, latency, error
        
    except Exception as e:
        return False, 0, f"Error: {type(e).__name__}"
    
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except:
                try:
                    proc.kill()
                except:
                    pass
        
        port_mgr.release(port)
        
        if config_file:
            try:
                os.remove(config_file)
            except:
                pass


def test_batch_smart(batch_proxies: List[Dict], clash_bin: str, temp_dir: str,
                    workers: int, timeout: int, quick_mode: bool = False) -> Tuple[List[Dict], Dict]:
    """Test batch with smart retry logic"""
    port_mgr = SmartPortManager()
    working = []
    failed = []
    errors = {}
    lock = threading.Lock()
    
    def test_wrapper(idx_proxy):
        idx, proxy = idx_proxy
        result, latency, error = test_single_proxy_smart(
            proxy, clash_bin, temp_dir, port_mgr, timeout, quick_mode
        )
        return idx, proxy, result, latency, error
    
    # First pass
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
                        if error == "No connectivity":
                            failed.append(proxy)
                        errors[error] = errors.get(error, 0) + 1
            except Exception as e:
                with lock:
                    errors[f"Exc: {type(e).__name__}"] = errors.get(f"Exc: {type(e).__name__}", 0) + 1
    
    # Retry failed with longer timeout (only if not in quick mode)
    if failed and not quick_mode and len(failed) <= 10:
        time.sleep(1)
        retry_timeout = timeout + 5
        
        with ThreadPoolExecutor(max_workers=min(workers, 5)) as executor:
            futures = {executor.submit(test_wrapper, (i, p)): i 
                       for i, p in enumerate(failed, 1)}
            
            for future in as_completed(futures):
                try:
                    idx, proxy, result, latency, error = future.result()
                    
                    with lock:
                        if result:
                            proxy['latency'] = latency
                            working.append(proxy)
                except:
                    pass
    
    return working, errors


def test_protocol_smart(ptype: str, proxies: List[Dict], clash_bin: str,
                       temp_dir: str, workers: int, timeout: int, 
                       batch_size: int) -> List[Dict]:
    """Test protocol group with smart batching"""
    print(f"\n{'='*70}")
    print(f"Testing {ptype.upper()} - {len(proxies)} proxies")
    print(f"{'='*70}")
    
    # Use quick mode for large batches
    quick_mode = len(proxies) > 500
    
    all_working = []
    num_batches = (len(proxies) + batch_size - 1) // batch_size
    total_tested = 0
    
    for batch_idx in range(num_batches):
        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, len(proxies))
        batch_proxies = proxies[start_idx:end_idx]
        
        print(f"  Batch {batch_idx + 1}/{num_batches}: Testing {len(batch_proxies)}...", 
              end=' ', flush=True)
        
        batch_working, batch_errors = test_batch_smart(
            batch_proxies, clash_bin, temp_dir, workers, timeout, quick_mode
        )
        
        all_working.extend(batch_working)
        total_tested += len(batch_proxies)
        
        batch_rate = (len(batch_working) / len(batch_proxies) * 100)
        overall_rate = (len(all_working) / total_tested * 100)
        
        print(f"{len(batch_working)}/{len(batch_proxies)} ({batch_rate:.1f}%) | "
              f"Total: {len(all_working)}/{total_tested} ({overall_rate:.1f}%)")
        
        # Show progress every 10 batches for large groups
        if (batch_idx + 1) % 10 == 0 and num_batches > 10:
            print(f"    Progress: {batch_idx + 1}/{num_batches} batches completed")
        
        time.sleep(0.3)
    
    success_rate = (len(all_working) / len(proxies) * 100) if proxies else 0
    print(f"\n  {ptype.upper()} Complete: {len(all_working)}/{len(proxies)} ({success_rate:.1f}%)")
    
    return all_working


def test_all_smart(proxies: List[Dict], clash_bin: str, temp_dir: str) -> List[Dict]:
    """Smart testing for all proxies"""
    
    # Adaptive settings based on total count
    total = len(proxies)
    
    if total > 3000:
        workers = min(int(os.environ.get('TEST_WORKERS', 60)), 80)
        batch_size = 50
        timeout = 10
    elif total > 1000:
        workers = min(int(os.environ.get('TEST_WORKERS', 50)), 60)
        batch_size = 40
        timeout = 12
    else:
        workers = min(int(os.environ.get('TEST_WORKERS', 40)), 50)
        batch_size = 30
        timeout = 15
    
    # Group by protocol
    groups = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        groups.setdefault(ptype, []).append(proxy)
    
    print(f"\n{'='*70}")
    print(f"SMART TESTING MODE")
    print(f"{'='*70}")
    print(f"Total: {total} proxies")
    print(f"Workers: {workers} | Timeout: {timeout}s | Batch: {batch_size}")
    print(f"Strategy: Adaptive testing with smart retry")
    print(f"\nProtocols:")
    for ptype, plist in sorted(groups.items()):
        print(f"  {ptype.upper()}: {len(plist)}")
    print(f"{'='*70}")
    
    all_working = []
    
    for ptype, plist in sorted(groups.items()):
        working = test_protocol_smart(
            ptype, plist, clash_bin, temp_dir, workers, timeout, batch_size
        )
        all_working.extend(working)
        time.sleep(0.5)
    
    return all_working


def save_results(proxies: List[Dict], output_dir: str):
    """Save results"""
    os.makedirs(output_dir, exist_ok=True)
    
    with open(os.path.join(output_dir, 'working_proxies.json'), 'w', encoding='utf-8') as f:
        json.dump(proxies, f, indent=2, ensure_ascii=False)
    
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
    
    with open(os.path.join(output_dir, 'all_working.txt'), 'w', encoding='utf-8') as f:
        for proxy in proxies:
            from utils import proxy_to_share_url
            url = proxy_to_share_url(proxy)
            if url:
                f.write(url + '\n')
    
    latencies = [p.get('latency', 0) for p in proxies if p.get('latency', 0) > 0]
    metadata = {
        'total_working': len(proxies),
        'by_protocol': {ptype: len(plist) for ptype, plist in protocols.items()},
        'latency_stats': {
            'average_ms': round(sum(latencies) / len(latencies), 2) if latencies else 0,
            'min_ms': round(min(latencies), 2) if latencies else 0,
            'max_ms': round(max(latencies), 2) if latencies else 0
        },
        'test_method': 'smart_adaptive',
        'last_updated': datetime.now().isoformat(),
        'timestamp': int(time.time())
    }
    
    with open(os.path.join(output_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)


def find_clash_binary() -> Optional[str]:
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
    
    dup = len(proxies) - len(unique)
    if dup > 0:
        print(f"Removed {dup} duplicates")
    
    return unique


def main():
    print("="*70)
    print("Smart Adaptive Proxy Tester")
    print("="*70 + "\n")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    temp_dir = os.path.join(base_dir, 'temp_configs')
    output_dir = os.path.join(base_dir, 'working_configs')
    
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
    
    clash_bin = find_clash_binary()
    if not clash_bin:
        print("Error: Clash not found")
        sys.exit(1)
    
    print(f"Clash: {clash_bin}")
    
    start_time = time.time()
    working = test_all_smart(proxies, clash_bin, temp_dir)
    elapsed = time.time() - start_time
    
    print(f"\n{'='*70}")
    print(f"RESULTS")
    print(f"{'='*70}")
    print(f"Tested:      {len(proxies)}")
    print(f"Working:     {len(working)}")
    print(f"Success:     {len(working)/len(proxies)*100:.1f}%")
    print(f"Time:        {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"Speed:       {len(proxies)/elapsed:.1f} proxies/sec")
    
    if working:
        print(f"\nBy Protocol:")
        protocols = {}
        for p in working:
            ptype = p.get('type', '?')
            protocols[ptype] = protocols.get(ptype, 0) + 1
        
        for ptype, count in sorted(protocols.items()):
            print(f"  {ptype.upper():<10} {count}")
    
    print(f"{'='*70}\n")
    
    if working:
        save_results(working, output_dir)
        print(f"✓ Saved {len(working)} proxies to {output_dir}/")
    else:
        print("⚠ No working proxies")
        sys.exit(1)


if __name__ == '__main__':
    main()
