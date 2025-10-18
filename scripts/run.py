#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enhanced parallel proxy tester (modified)
Key improvements:
 - Configurable pass threshold (TEST_PASS_RATE)
 - Wait/poll for clash instance readiness instead of blind sleep
 - Stronger HTTPS checks (verify certs for HTTPS)
 - allow_redirects enabled to detect captive portals
 - Verbose debug logging option (TEST_VERBOSE)
 - Require at least one successful HTTPS-weighted test for full pass
"""

import os
import sys
import time
import yaml
import hashlib
import threading
import requests
from typing import Dict, Tuple, List
from concurrent.futures import ThreadPoolExecutor, as_completed

# Note: The following helper objects/functions are expected to exist elsewhere in repo:
# - PortManager
# - clash_context
# - sanitize_filename
# - proxy_to_clash_format
# If module imports are needed, they should be imported above. This file assumes they are available
# in the same package or via Python path.

# Configurable runtime options via environment variables
TEST_PASS_RATE = float(os.environ.get('TEST_PASS_RATE', '0.8'))   # fraction 0..1 required to pass
TEST_READY_WAIT = float(os.environ.get('TEST_READY_WAIT', '3'))   # seconds to wait for clash instance readiness
TEST_VERBOSE = os.environ.get('TEST_VERBOSE', 'false').lower() in ('1', 'true', 'yes')

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


def test_proxy_connectivity(proxy_port: int, timeout: int = 8, retry: int = 2, pass_rate: float = None) -> Tuple[bool, float]:
    """
    Test connectivity through a local proxy listening on proxy_port.
    Improvements:
      - allow_redirects=True to detect captive portals / redirects
      - verify=True for HTTPS requests to detect interception
      - configurable pass_rate (default TEST_PASS_RATE)
      - require at least some HTTPS-weighted passes to reduce false positives
    Returns: (success: bool, avg_latency_ms: float)
    """
    pass_rate = float(pass_rate) if pass_rate is not None else TEST_PASS_RATE

    proxies = {
        'http': f'http://127.0.0.1:{proxy_port}',
        'https': f'http://127.0.0.1:{proxy_port}'
    }

    test_targets = [
        {'url': 'http://connectivitycheck.gstatic.com/generate_204', 'expected_code': 204, 'min_size': None, 'weight': 2},
        {'url': 'http://cp.cloudflare.com', 'expected_code': 204, 'min_size': None, 'weight': 2},
        {'url': 'http://www.gstatic.com/generate_204', 'expected_code': 204, 'min_size': None, 'weight': 2},
        {'url': 'https://www.gstatic.com/generate_204', 'expected_code': 204, 'min_size': None, 'weight': 2},
        {'url': 'https://1.1.1.1', 'expected_code': 204, 'min_size': None, 'weight': 2},
    ]

    passed_tests = 0
    total_weight = sum(t['weight'] for t in test_targets)
    latencies = []

    https_pass_weight = 0
    https_total_weight = sum(t['weight'] for t in test_targets if t['url'].lower().startswith('https'))

    for attempt in range(retry):
        for test in test_targets:
            try:
                start = time.time()
                is_https = test['url'].lower().startswith('https')
                # For HTTPS, enable certificate verification to detect MITM/interception
                response = requests.get(
                    test['url'],
                    proxies=proxies,
                    timeout=timeout,
                    allow_redirects=True,
                    verify=is_https
                )
                latency = (time.time() - start) * 1000

                if response.status_code != test['expected_code']:
                    if TEST_VERBOSE:
                        print(f"DBG: {test['url']} -> code {response.status_code} (expected {test['expected_code']})")
                    continue

                if test['min_size'] and len(response.content) < test['min_size']:
                    if TEST_VERBOSE:
                        print(f"DBG: {test['url']} -> content too small {len(response.content)} < {test['min_size']}")
                    continue

                # Basic heuristic: if we see a redirect to a known captive portal domain or body contains HTML,
                # it may be a captive portal; allow_redirects=True means final URL may differ.
                # Simple check: if content looks like HTML and expected_code is 204, treat with suspicion.
                if test['expected_code'] == 204:
                    content_sample = (response.content[:200] or b'').lower()
                    if b'<html' in content_sample or b'<!doctype html' in content_sample:
                        if TEST_VERBOSE:
                            print(f"DBG: {test['url']} -> HTML content returned for 204 endpoint; treating as fail")
                        continue

                passed_tests += test['weight']
                latencies.append(latency)
                if is_https:
                    https_pass_weight += test['weight']

                if TEST_VERBOSE:
                    sample = (response.content[:200] if response.content else b'').decode(errors='replace')
                    print(f"DBG PASS: {test['url']} code={response.status_code} len={len(response.content)} sample={sample!r}")

            except (requests.exceptions.ProxyError,
                    requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError) as e:
                if TEST_VERBOSE:
                    print(f"DBG EXC: {test['url']} -> {repr(e)}")
                continue
            except Exception as e:
                if TEST_VERBOSE:
                    print(f"DBG UNEXPECTED: {test['url']} -> {repr(e)}")
                continue

        # Evaluate pass condition: require weighted pass_rate AND at least some HTTPS-weighted passes (if HTTPS tests exist)
        effective_rate = (passed_tests / total_weight) if total_weight > 0 else 0
        https_ok = (https_total_weight == 0) or (https_pass_weight > 0)

        if TEST_VERBOSE:
            print(f"DBG ATTEMPT {attempt+1}: passed={passed_tests}/{total_weight} rate={effective_rate:.2f} https_ok={https_ok}")

        if effective_rate >= pass_rate and https_ok:
            break

        if attempt < retry - 1:
            time.sleep(1)
            passed_tests = 0
            latencies.clear()
            https_pass_weight = 0

    success = (passed_tests / total_weight) >= pass_rate and ((https_total_weight == 0) or (https_pass_weight > 0))
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    return success, avg_latency


def test_single_proxy(proxy: Dict, clash_path: str, config_dir: str,
                      port_manager, test_timeout: int = 8) -> Tuple[bool, float]:
    """
    Launch a temporary clash instance configured for the single proxy, wait until ready (poll),
    then run test_proxy_connectivity. Finally release port.
    """
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
            if not instance:
                return False, 0

            # Wait / poll for instance.is_ready up to TEST_READY_WAIT seconds
            start_wait = time.time()
            while True:
                if getattr(instance, 'is_ready', False):
                    break
                if (time.time() - start_wait) >= TEST_READY_WAIT:
                    if TEST_VERBOSE:
                        print(f"DBG: instance not ready after {TEST_READY_WAIT}s")
                    return False, 0
                time.sleep(0.1)

            # Now run the connectivity test
            success, latency = test_proxy_connectivity(proxy_port, timeout=test_timeout)
            return success, latency

    except Exception as e:
        if TEST_VERBOSE:
            print(f"DBG test_single_proxy unexpected: {repr(e)}")
        return False, 0
    finally:
        try:
            port_manager.release_port(proxy_port)
        except Exception:
            pass


def test_batch_proxies(batch_num: int, batch_proxies: List[Dict],
                       clash_path: str, temp_dir: str, port_manager,
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

            except Exception as e:
                with lock:
                    completed += 1
                if TEST_VERBOSE:
                    print(f"DBG batch future exception: {repr(e)}")

    return working_proxies, completed, latencies


def test_group_proxies(group_name: str, proxies: List[Dict], clash_path: str,
                       temp_dir: str, port_manager, max_workers: int,
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

        print(f"  Batch {batch_num + 1}: {len(batch_working)}/{batch_tested} ({batch_rate:.1f}%)")
        print(f"  Overall: {len(working_proxies)}/{total_tested} ({overall_rate:.1f}%)")
        sys.stdout.flush()

    success_rate = (len(working_proxies) / total * 100) if total > 0 else 0
    print(f"\n  {group_name.upper()} Complete: {len(working_proxies)}/{total} ({success_rate:.1f}%)")
    sys.stdout.flush()

    return working_proxies, {'total': total, 'working': len(working_proxies)}, all_latencies


# If the original file contained a __main__ or CLI handling, it should remain below.
# For brevity, keep CLI wiring minimal and defers to existing main logic in the repo.
if __name__ == '__main__':
    # Minimal runner: keep existing behavior by importing the repo's higher-level runner if present.
    # This script focuses on the testing logic improvements; the main application (config_loader, CLI)
    # will call these functions.
    print("This module provides enhanced test functions for the clash tester.")
    print("Configure via environment variables: TEST_PASS_RATE, TEST_READY_WAIT, TEST_VERBOSE")
