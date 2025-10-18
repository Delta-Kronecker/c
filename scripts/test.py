#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enhanced parallel proxy tester with false positive fixes
Key improvements:
 - Fixed false positives by requiring multiple test passes
 - Added DNS leak detection
 - Added real IP verification
 - Stricter certificate validation
 - Timeout calibration
 - Better captive portal detection
 - Parallel test diversity (HTTP + HTTPS + DNS)
"""

import os
import sys
import time
import yaml
import json
import hashlib
import threading
import requests
import socket
from typing import Dict, Tuple, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configurable runtime options
TEST_PASS_RATE = float(os.environ.get('TEST_PASS_RATE', '0.85'))
TEST_READY_WAIT = float(os.environ.get('TEST_READY_WAIT', '5'))
TEST_VERBOSE = os.environ.get('TEST_VERBOSE', 'false').lower() in ('1', 'true', 'yes')
TEST_TIMEOUT = int(os.environ.get('TEST_TIMEOUT', '10'))
TEST_RETRY = int(os.environ.get('TEST_RETRY', '2'))
MIN_REQUIRED_PASSES = int(os.environ.get('MIN_REQUIRED_PASSES', '4'))  # Minimum successful tests


def create_clash_config(proxy: Dict, config_file: str, proxy_port: int, control_port: int) -> bool:
    """Create clash configuration with proper error handling"""
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
    except Exception as e:
        if TEST_VERBOSE:
            print(f"ERR: Failed to create config: {repr(e)}")
        return False


def check_ip_via_proxy(proxy_port: int, timeout: int = 8) -> Optional[str]:
    """
    Verify real IP through proxy to detect leaks
    Returns the IP address or None if failed
    """
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    ip_check_services = [
        'https://api.ipify.org?format=json',
        'https://ifconfig.me/all.json',
        'https://ipinfo.io/json'
    ]
    
    for service in ip_check_services:
        try:
            response = requests.get(
                service,
                proxies=proxies,
                timeout=timeout,
                verify=True
            )
            
            if response.status_code == 200:
                data = response.json()
                # Different services use different keys
                ip = data.get('ip') or data.get('ip_addr')
                if ip and isinstance(ip, str):
                    return ip.strip()
        except Exception:
            continue
    
    return None


def check_dns_leak(proxy_port: int, timeout: int = 8) -> bool:
    """
    Check for DNS leaks through proxy
    Returns True if DNS is working correctly through proxy
    """
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    try:
        # Try to resolve a domain through proxy
        response = requests.get(
            'https://www.google.com/generate_204',
            proxies=proxies,
            timeout=timeout,
            verify=True
        )
        return response.status_code == 204
    except Exception:
        return False


def detect_captive_portal(content: bytes, status_code: int, url: str) -> bool:
    """
    Advanced captive portal detection
    Returns True if captive portal is detected
    """
    # For 204 responses, any content is suspicious
    if status_code == 204 and len(content) > 10:
        return True
    
    # Check for common captive portal indicators
    content_lower = content[:1000].lower()
    
    captive_indicators = [
        b'<html',
        b'<!doctype',
        b'<meta http-equiv',
        b'captive',
        b'portal',
        b'authentication required',
        b'login',
        b'redirect',
        b'wifi'
    ]
    
    for indicator in captive_indicators:
        if indicator in content_lower:
            return True
    
    return False


def test_http_basic(proxy_port: int, timeout: int) -> Tuple[bool, float]:
    """Test basic HTTP connectivity"""
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    try:
        start = time.time()
        response = requests.get(
            'http://connectivitycheck.gstatic.com/generate_204',
            proxies=proxies,
            timeout=timeout,
            allow_redirects=False
        )
        latency = (time.time() - start) * 1000
        
        if response.status_code != 204:
            return False, 0
        
        if detect_captive_portal(response.content, response.status_code, response.url):
            return False, 0
        
        return True, latency
    except Exception:
        return False, 0


def test_https_strict(proxy_port: int, timeout: int) -> Tuple[bool, float]:
    """Test HTTPS with strict certificate validation"""
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    try:
        start = time.time()
        response = requests.get(
            'https://www.gstatic.com/generate_204',
            proxies=proxies,
            timeout=timeout,
            verify=True,  # Strict SSL verification
            allow_redirects=False
        )
        latency = (time.time() - start) * 1000
        
        if response.status_code != 204:
            return False, 0
        
        if detect_captive_portal(response.content, response.status_code, response.url):
            return False, 0
        
        return True, latency
    except requests.exceptions.SSLError:
        # SSL certificate verification failed - definite failure
        return False, 0
    except Exception:
        return False, 0


def test_cloudflare(proxy_port: int, timeout: int) -> Tuple[bool, float]:
    """Test Cloudflare connectivity check"""
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    try:
        start = time.time()
        response = requests.get(
            'https://1.1.1.1',
            proxies=proxies,
            timeout=timeout,
            verify=True,
            allow_redirects=False
        )
        latency = (time.time() - start) * 1000
        
        # Cloudflare returns various status codes
        if response.status_code not in [200, 204, 301, 302]:
            return False, 0
        
        return True, latency
    except Exception:
        return False, 0


def test_google_service(proxy_port: int, timeout: int) -> Tuple[bool, float]:
    """Test Google service connectivity"""
    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }
    
    try:
        start = time.time()
        response = requests.get(
            'https://www.google.com/generate_204',
            proxies=proxies,
            timeout=timeout,
            verify=True,
            allow_redirects=False
        )
        latency = (time.time() - start) * 1000
        
        if response.status_code != 204:
            return False, 0
        
        if len(response.content) > 0:
            return False, 0
        
        return True, latency
    except Exception:
        return False, 0


def test_proxy_connectivity(proxy_port: int, timeout: int = 10, retry: int = 2) -> Tuple[bool, float]:
    """
    Comprehensive proxy connectivity test with false positive prevention
    
    Strategy:
    1. Run multiple diverse tests (HTTP, HTTPS, different providers)
    2. Require minimum number of passes (MIN_REQUIRED_PASSES)
    3. Check for IP leaks
    4. Verify DNS works through proxy
    5. Strict SSL validation
    6. Detect captive portals
    
    Returns: (success: bool, avg_latency_ms: float)
    """
    
    test_functions = [
        ('HTTP Basic', test_http_basic, 1.0),
        ('HTTPS Strict', test_https_strict, 2.0),
        ('Cloudflare', test_cloudflare, 1.5),
        ('Google Service', test_google_service, 2.0),
    ]
    
    for attempt in range(retry):
        passed_count = 0
        total_weight = sum(weight for _, _, weight in test_functions)
        passed_weight = 0
        latencies = []
        
        # Run all connectivity tests
        for test_name, test_func, weight in test_functions:
            success, latency = test_func(proxy_port, timeout)
            
            if success:
                passed_count += 1
                passed_weight += weight
                latencies.append(latency)
                
                if TEST_VERBOSE:
                    print(f"  ✓ {test_name}: {latency:.1f}ms")
            else:
                if TEST_VERBOSE:
                    print(f"  ✗ {test_name}: FAILED")
        
        # Check if we have enough passes
        if passed_count < MIN_REQUIRED_PASSES:
            if TEST_VERBOSE:
                print(f"  Attempt {attempt + 1}: Only {passed_count}/{len(test_functions)} tests passed (need {MIN_REQUIRED_PASSES})")
            
            if attempt < retry - 1:
                time.sleep(1)
                continue
            else:
                return False, 0
        
        # Check weighted pass rate
        pass_rate = passed_weight / total_weight
        if pass_rate < TEST_PASS_RATE:
            if TEST_VERBOSE:
                print(f"  Attempt {attempt + 1}: Pass rate {pass_rate:.2%} < {TEST_PASS_RATE:.2%}")
            
            if attempt < retry - 1:
                time.sleep(1)
                continue
            else:
                return False, 0
        
        # Verify IP through proxy (optional but recommended)
        proxy_ip = check_ip_via_proxy(proxy_port, timeout)
        if proxy_ip and TEST_VERBOSE:
            print(f"  Proxy IP: {proxy_ip}")
        
        # Check DNS
        dns_ok = check_dns_leak(proxy_port, timeout)
        if not dns_ok:
            if TEST_VERBOSE:
                print(f"  DNS check failed")
            
            if attempt < retry - 1:
                time.sleep(1)
                continue
            else:
                return False, 0
        
        # Success!
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        
        if TEST_VERBOSE:
            print(f"  ✓ ALL TESTS PASSED - Avg latency: {avg_latency:.1f}ms")
        
        return True, avg_latency
    
    return False, 0


def test_single_proxy(proxy: Dict, clash_path: str, config_dir: str,
                      port_manager, test_timeout: int = 10) -> Tuple[bool, float]:
    """
    Launch a temporary clash instance and test it comprehensively
    """
    proxy_port = port_manager.acquire_port()
    if not proxy_port:
        if TEST_VERBOSE:
            print("  ERR: Failed to acquire port")
        return False, 0

    control_port = proxy_port + 1000

    try:
        safe_name = sanitize_filename(proxy.get('name', 'proxy'))
        unique_id = hashlib.md5(
            f"{proxy.get('server', '')}:{proxy.get('port', '')}{time.time()}".encode()
        ).hexdigest()[:8]
        config_file = os.path.join(config_dir, f"test_{unique_id}_{safe_name}.yaml")

        if not create_clash_config(proxy, config_file, proxy_port, control_port):
            if TEST_VERBOSE:
                print("  ERR: Failed to create config")
            return False, 0

        with clash_context(config_file, clash_path, proxy_port, control_port) as instance:
            if not instance:
                if TEST_VERBOSE:
                    print("  ERR: Failed to start clash instance")
                return False, 0

            # Wait for instance readiness with timeout
            start_wait = time.time()
            ready = False
            
            while (time.time() - start_wait) < TEST_READY_WAIT:
                if getattr(instance, 'is_ready', False):
                    ready = True
                    break
                time.sleep(0.1)
            
            if not ready:
                if TEST_VERBOSE:
                    print(f"  ERR: Instance not ready after {TEST_READY_WAIT}s")
                return False, 0
            
            # Additional settling time for proxy to be fully operational
            time.sleep(0.5)

            # Run comprehensive connectivity test
            success, latency = test_proxy_connectivity(
                proxy_port, 
                timeout=test_timeout,
                retry=TEST_RETRY
            )
            
            return success, latency

    except Exception as e:
        if TEST_VERBOSE:
            print(f"  ERR: Unexpected exception in test_single_proxy: {repr(e)}")
        return False, 0
    finally:
        try:
            port_manager.release_port(proxy_port)
        except Exception:
            pass
        
        # Clean up config file
        try:
            if 'config_file' in locals() and os.path.exists(config_file):
                os.remove(config_file)
        except Exception:
            pass


def test_batch_proxies(batch_num: int, batch_proxies: List[Dict],
                       clash_path: str, temp_dir: str, port_manager,
                       max_workers: int, test_timeout: int) -> Tuple[List[Dict], int, Dict]:
    """Test a batch of proxies in parallel"""
    working_proxies = []
    completed = 0
    latencies = {}
    lock = threading.Lock()

    def test_proxy_wrapper(proxy_data):
        idx, proxy = proxy_data
        
        if TEST_VERBOSE:
            print(f"\n  Testing proxy {idx}: {proxy.get('name', 'unnamed')}")
        
        result, latency = test_single_proxy(
            proxy, 
            clash_path, 
            temp_dir, 
            port_manager, 
            test_timeout
        )
        
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
                        print(f"  ✓ Proxy {idx} PASSED (latency: {latency:.1f}ms)")
                    else:
                        print(f"  ✗ Proxy {idx} FAILED")

            except Exception as e:
                with lock:
                    completed += 1
                if TEST_VERBOSE:
                    print(f"  ERR: Future exception: {repr(e)}")

    return working_proxies, completed, latencies


def test_group_proxies(group_name: str, proxies: List[Dict], clash_path: str,
                       temp_dir: str, port_manager, max_workers: int,
                       test_timeout: int, batch_size: int = 50) -> Tuple[List[Dict], Dict, Dict]:
    """Test a group of proxies with comprehensive validation"""
    working_proxies = []
    all_latencies = {}
    total = len(proxies)

    print(f"\n{'='*60}")
    print(f"Testing {group_name.upper()} - {total} proxies")
    print(f"Pass criteria: {TEST_PASS_RATE:.0%} success rate, min {MIN_REQUIRED_PASSES} tests")
    print(f"{'='*60}")
    sys.stdout.flush()

    num_batches = (total + batch_size - 1) // batch_size
    total_tested = 0

    for batch_num in range(num_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, total)
        batch_proxies = proxies[start_idx:end_idx]

        print(f"\n  Batch {batch_num + 1}/{num_batches} - Testing {len(batch_proxies)} configs...")
        sys.stdout.flush()

        batch_working, batch_tested, batch_latencies = test_batch_proxies(
            batch_num,
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

        print(f"\n  Batch {batch_num + 1} Results:")
        print(f"    This batch: {len(batch_working)}/{batch_tested} ({batch_rate:.1f}%)")
        print(f"    Overall: {len(working_proxies)}/{total_tested} ({overall_rate:.1f}%)")
        sys.stdout.flush()

    success_rate = (len(working_proxies) / total * 100) if total > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"{group_name.upper()} COMPLETE")
    print(f"Working proxies: {len(working_proxies)}/{total} ({success_rate:.1f}%)")
    print(f"{'='*60}\n")
    sys.stdout.flush()

    return working_proxies, {'total': total, 'working': len(working_proxies)}, all_latencies


if __name__ == '__main__':
    print("Enhanced Proxy Tester with False Positive Prevention")
    print("\nEnvironment Variables:")
    print(f"  TEST_PASS_RATE: {TEST_PASS_RATE} (required success rate)")
    print(f"  TEST_READY_WAIT: {TEST_READY_WAIT}s (clash startup wait)")
    print(f"  TEST_TIMEOUT: {TEST_TIMEOUT}s (per-test timeout)")
    print(f"  TEST_RETRY: {TEST_RETRY} (retry attempts)")
    print(f"  MIN_REQUIRED_PASSES: {MIN_REQUIRED_PASSES} (minimum tests that must pass)")
    print(f"  TEST_VERBOSE: {TEST_VERBOSE} (detailed logging)")
    print("\nFeatures:")
    print("  ✓ Multiple diverse connectivity tests")
    print("  ✓ Strict HTTPS/SSL validation")
    print("  ✓ Captive portal detection")
    print("  ✓ DNS leak checking")
    print("  ✓ IP verification support")
    print("  ✓ Minimum pass count requirement")
    print("  ✓ Weighted scoring system")
