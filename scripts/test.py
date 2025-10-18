"""
Optimized Proxy Testing - Fast but Accurate
Designed for CI/CD environments
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

# Import utilities
from utils import proxy_to_clash_format, calculate_proxy_hash


class FastPortManager:
    """Lightweight port manager"""
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
                        # Quick check
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


def quick_test_proxy(proxy_port: int, timeout: int = 12) -> Tuple[bool, float, str]:
    """
    Optimized 3-stage test:
    1. IP verification (CRITICAL)
    2. Basic HTTP
    3. HTTPS content
    """
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    latencies = []
    
    # Stage 1: IP verification - MUST PASS
    try:
        start = time.time()
        resp = requests.get('http://ip-api.com/json/?fields=query', 
                          proxies=proxies, timeout=timeout, verify=False)
        lat = (time.time() - start) * 1000
        
        if resp.status_code != 200:
            return False, 0, "IP check: connection failed"
        
        proxy_ip = resp.json().get('query', '')
        if not proxy_ip:
            return False, 0, "IP check: no IP returned"
        
        # Quick direct IP check
        direct_resp = requests.get('http://ip-api.com/json/?fields=query', timeout=3)
        direct_ip = direct_resp.json().get('query', '')
        
        if proxy_ip == direct_ip:
            return False, 0, f"IP leak detected ({proxy_ip})"
        
        latencies.append(lat)
        
    except requests.exceptions.ProxyError:
        return False, 0, "Proxy connection failed"
    except requests.exceptions.Timeout:
        return False, 0, "Timeout"
    except Exception as e:
        return False, 0, f"Error: {type(e).__name__}"
    
    # Stage 2: Basic HTTP
    try:
        start = time.time()
        resp = requests.get('http://www.gstatic.com/generate_204',
                          proxies=proxies, timeout=timeout, verify=False)
        lat = (time.time() - start) * 1000
        
        if resp.status_code == 204:
            latencies.append(lat)
        else:
            return False, 0, "HTTP test failed"
    except:
        return False, 0, "HTTP error"
    
    # Stage 3: HTTPS + Content
    try:
        start = time.time()
        resp = requests.get('https://www.google.com/humans.txt',
                          proxies=proxies, timeout=timeout, verify=False)
        lat = (time.time() - start) * 1000
        
        if resp.status_code == 200 and len(resp.content) > 20:
            content = resp.content.lower()
            if b'google' in content or b'human' in content:
                latencies.append(lat)
            else:
                return False, 0, "Content validation failed"
        else:
            return False, 0, "HTTPS test failed"
    except:
        return False, 0, "HTTPS error"
    
    avg_latency = sum(latencies) / len(latencies) if latencies else 999999
    return True, avg_latency, ""


def start_clash(config_path: str, clash_bin: str, proxy_port: int, 
                control_port: int, timeout: int = 12) -> Optional[subprocess.Popen]:
    """Start Clash and wait for ready"""
    try:
        proc = subprocess.Popen(
            [clash_bin, '-f', config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        time.sleep(1.5)
        
        if proc.poll() is not None:
            return None
        
        # Quick health check
        for _ in range(timeout):
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
                      port_mgr: FastPortManager, timeout: int) -> Tuple[bool, float, str]:
    """Test single proxy"""
    port = port_mgr.acquire()
    if not port:
        return False, 0, "No ports"
    
    ctrl_port = port + 1000
    proc = None
    
    try:
        # Create config
        name = re.sub(r'[<>:"/\\|?*]', '_', proxy.get('name', 'proxy'))[:50]
        uid = hashlib.md5(f"{proxy.get('server')}:{proxy.get('port')}{time.time()}".encode()).hexdigest()[:8]
        cfg_file = os.path.join(temp_dir, f"t_{uid}.yaml")
        
        clash_proxy = proxy_to_clash_format(proxy)
        config = {
            'port': port,
            'socks-port': port + 1,
            'allow-lan': False,
            'mode': 'rule',
            'log-level': 'silent',
            'external-controller': f'127.0.0.1:{ctrl_port}',
            'dns': {'enable': False},
            'proxies': [clash_proxy],
            'proxy-groups': [{
                'name': 'PROXY',
                'type': 'select',
                'proxies': [clash_proxy['name']]
            }],
            'rules': ['MATCH,PROXY,no-resolve']
        }
        
        with open(cfg_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True)
        
        # Start Clash
        proc = start_clash(cfg_file, clash_bin, port, ctrl_port, timeout)
        
        if not proc:
            return False, 0, "Clash start failed"
        
        # Test proxy
        success, latency, error = quick_test_proxy(port, timeout)
        
        return success, latency, error
        
    except Exception as e:
        return False, 0, f"Exception: {type(e).__name__}"
    finally:
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
        
        # Cleanup config
        try:
            cfg_file = os.path.join(temp_dir, f"t_{uid}.yaml")
            if os.path.exists(cfg_file):
                os.remove(cfg_file)
        except:
            pass


def test_batch(batch_id: int, proxies: List[Dict], clash_bin: str, 
               temp_dir: str, workers: int, timeout: int) -> Tuple[List[Dict], Dict]:
    """Test a batch of proxies"""
    port_mgr = FastPortManager()
    working = []
    errors = {}
    lock = threading.Lock()
    
    def test_wrapper(idx_proxy):
        idx, proxy = idx_proxy
        result, latency, error = test_single_proxy(proxy, clash_bin, temp_dir, port_mgr, timeout)
        return idx, proxy, result, latency, error
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(test_wrapper, (i, p)): i 
                   for i, p in enumerate(proxies, 1)}
        
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
                    errors[f"Exc: {type(e).__name__}"] = errors.get(f"Exc: {type(e).__name__}", 0) + 1
    
    return working, errors


def test_all(proxies: List[Dict], clash_bin: str, temp_dir: str) -> List[Dict]:
    """Test all proxies"""
    
    # Get settings from env
    workers = min(int(os.environ.get('TEST_WORKERS', 40)), 50)
    timeout = int(os.environ.get('TEST_TIMEOUT', 12))
    batch_size = min(int(os.environ.get('BATCH_SIZE', 30)), 50)
    
    # Group by protocol
    groups = {}
    for proxy in proxies:
        ptype = proxy.get('type', 'unknown')
        groups.setdefault(ptype, []).append(proxy)
    
    print(f"\n{'='*70}")
    print(f"OPTIMIZED TESTING MODE")
    print(f"{'='*70}")
    print(f"Total: {len(proxies)} | Workers: {workers} | Timeout: {timeout}s | Batch: {batch_size}")
    print(f"Validation: IP Check + HTTP + HTTPS + Content")
    for ptype, plist in sorted(groups.items()):
        print(f"  {ptype.upper()}: {len(plist)}")
    print(f"{'='*70}")
    
    all_working = []
    
    for ptype, plist in sorted(groups.items()):
        print(f"\n{'='*70}")
        print(f"Testing {ptype.upper()} - {len(plist)} proxies")
        print(f"{'='*70}")
        
        total_tested = 0
        protocol_working = []
        
        # Process in batches
        num_batches = (len(plist) + batch_size - 1) // batch_size
        
        for batch_idx in range(num_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, len(plist))
            batch_proxies = plist[start_idx:end_idx]
            
            print(f"\n  Batch {batch_idx + 1}/{num_batches}: Testing {len(batch_proxies)}...", end=' ', flush=True)
            
            batch_working, batch_errors = test_batch(
                batch_idx + 1, batch_proxies, clash_bin, temp_dir, workers, timeout
            )
            
            protocol_working.extend(batch_working)
            total_tested += len(batch_proxies)
            
            batch_rate = (len(batch_working) / len(batch_proxies) * 100) if batch_proxies else 0
            overall_rate = (len(protocol_working) / total_tested * 100) if total_tested > 0 else 0
            
            print(f"{len(batch_working)}/{len(batch_proxies)} ({batch_rate:.1f}%)")
            
            if batch_errors:
                top_err = sorted(batch_errors.items(), key=lambda x: x[1], reverse=True)[:2]
                print(f"    Errors: {', '.join([f'{e}({c})' for e, c in top_err])}")
            
            print(f"    Overall: {len(protocol_working)}/{total_tested} ({overall_rate:.1f}%)", flush=True)
        
        all_working.extend(protocol_working)
        
        print(f"\n  {ptype.upper()} Complete: {len(protocol_working)}/{len(plist)} ({len(protocol_working)/len(plist)*100:.1f}%)")
        
        time.sleep(1)
    
    return all_working


def save_results(proxies: List[Dict], output_dir: str):
    """Save working proxies"""
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
        with open(os.path.join(by_proto_dir, f'{ptype}.txt'), 'w') as f:
            for proxy in plist:
                from utils import proxy_to_share_url
                f.write(proxy_to_share_url(proxy) + '\n')
    
    # All proxies
    with open(os.path.join(output_dir, 'all_working.txt'), 'w') as f:
        for proxy in proxies:
            from utils import proxy_to_share_url
            f.write(proxy_to_share_url(proxy) + '\n')
    
    # Metadata
    latencies = [p.get('latency', 0) for p in proxies if p.get('latency', 0) > 0]
    metadata = {
        'total_working': len(proxies),
        'by_protocol': {ptype: len(plist) for ptype, plist in protocols.items()},
        'latency': {
            'average': sum(latencies) / len(latencies) if latencies else 0,
            'min': min(latencies) if latencies else 0,
            'max': max(latencies) if latencies else 0
        },
        'validation': 'IP-Check + HTTP + HTTPS + Content',
        'last_updated': datetime.now().isoformat()
    }
    
    with open(os.path.join(output_dir, 'metadata.json'), 'w') as f:
        json.dump(metadata, f, indent=2)


def find_clash() -> Optional[str]:
    """Find Clash binary"""
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
    """Remove duplicate proxies"""
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
    print("Fast & Accurate Proxy Tester")
    print("="*70 + "\n")
    
    # Paths
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
    
    print(f"Loaded {len(proxies)} proxies")
    
    # Remove duplicates
    proxies = remove_duplicates(proxies)
    print(f"Unique proxies: {len(proxies)}")
    
    if not proxies:
        print("No proxies to test")
        sys.exit(1)
    
    # Find Clash
    clash_bin = find_clash()
    if not clash_bin:
        print("Error: Clash binary not found")
        sys.exit(1)
    
    print(f"Clash: {clash_bin}")
    
    # Test all proxies
    start_time = time.time()
    working = test_all(proxies, clash_bin, temp_dir)
    elapsed = time.time() - start_time
    
    # Results
    print(f"\n{'='*70}")
    print(f"RESULTS")
    print(f"{'='*70}")
    print(f"Tested: {len(proxies)}")
    print(f"Working: {len(working)}")
    print(f"Success Rate: {len(working)/len(proxies)*100:.1f}%")
    print(f"Time: {elapsed:.1f}s")
    print(f"Speed: {len(proxies)/elapsed:.1f} proxies/sec")
    print(f"{'='*70}\n")
    
    if working:
        save_results(working, output_dir)
        print(f"✓ Saved {len(working)} verified working proxies")
        print(f"  Location: {output_dir}/")
    else:
        print("⚠ No working proxies found")
        sys.exit(1)


if __name__ == '__main__':
    main()
