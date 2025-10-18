#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Complete proxy test runner that integrates the enhanced tester
This file should REPLACE your existing test.py
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime

# Import from your existing modules
from parallel_test import (
    test_group_proxies,
    TEST_PASS_RATE,
    TEST_READY_WAIT,
    TEST_TIMEOUT,
    TEST_RETRY,
    MIN_REQUIRED_PASSES,
    TEST_VERBOSE
)
from config_loader import load_parsed_proxies, ensure_directories
from port_manager import PortManager
from clash_manager import find_clash_binary


def save_working_proxies(proxies, latencies, output_dir='working_configs'):
    """Save working proxies to files"""
    os.makedirs(output_dir, exist_ok=True)
    
    # Group by protocol
    by_protocol = {}
    for proxy in proxies:
        protocol = proxy.get('type', 'unknown')
        if protocol not in by_protocol:
            by_protocol[protocol] = []
        by_protocol[protocol].append(proxy)
    
    # Save individual protocol files
    for protocol, proxy_list in by_protocol.items():
        output_file = os.path.join(output_dir, f'{protocol}_configs.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(proxy_list, f, ensure_ascii=False, indent=2)
        print(f"  Saved {len(proxy_list)} {protocol} configs to {output_file}")
    
    # Save all working configs
    all_file = os.path.join(output_dir, 'all_working_configs.json')
    with open(all_file, 'w', encoding='utf-8') as f:
        json.dump(proxies, f, ensure_ascii=False, indent=2)
    print(f"  Saved all {len(proxies)} working configs to {all_file}")
    
    # Calculate statistics
    total_latency = sum(latencies.values())
    avg_latency = total_latency / len(latencies) if latencies else 0
    
    protocol_stats = {
        protocol: len(proxy_list)
        for protocol, proxy_list in by_protocol.items()
    }
    
    # Save metadata
    metadata = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'total_working': len(proxies),
        'average_latency': avg_latency,
        'by_protocol': protocol_stats,
        'test_config': {
            'pass_rate': TEST_PASS_RATE,
            'timeout': TEST_TIMEOUT,
            'retry': TEST_RETRY,
            'min_passes': MIN_REQUIRED_PASSES
        }
    }
    
    metadata_file = os.path.join(output_dir, 'metadata.json')
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"  Saved metadata to {metadata_file}")
    
    return metadata


def main():
    """Main test execution"""
    print("="*70)
    print("🚀 ENHANCED PROXY TESTER - FALSE POSITIVE PREVENTION")
    print("="*70)
    
    print("\n📋 Configuration:")
    print(f"  • Pass Rate Required: {TEST_PASS_RATE:.0%}")
    print(f"  • Minimum Test Passes: {MIN_REQUIRED_PASSES}")
    print(f"  • Test Timeout: {TEST_TIMEOUT}s")
    print(f"  • Retry Attempts: {TEST_RETRY}")
    print(f"  • Clash Ready Wait: {TEST_READY_WAIT}s")
    print(f"  • Verbose Logging: {TEST_VERBOSE}")
    print(f"  • Max Workers: {os.environ.get('TEST_WORKERS', '50')}")
    
    print("\n🔒 Security Features:")
    print("  ✓ Multiple diverse connectivity tests")
    print("  ✓ Strict HTTPS/SSL certificate validation")
    print("  ✓ Advanced captive portal detection")
    print("  ✓ DNS leak checking")
    print("  ✓ IP verification support")
    print("  ✓ Minimum pass count requirement")
    print("  ✓ Weighted scoring system")
    
    # Ensure directories exist
    print("\n📁 Setting up directories...")
    temp_dir, working_dir = ensure_directories()
    
    # Find clash binary
    print("🔍 Finding Clash binary...")
    clash_path = find_clash_binary()
    if not clash_path:
        print("❌ ERROR: Clash binary not found!")
        sys.exit(1)
    print(f"  Found: {clash_path}")
    
    # Load proxies
    print("\n📥 Loading proxy configurations...")
    parsed_file = 'temp_configs/parsed_proxies.json'
    
    if not os.path.exists(parsed_file):
        print(f"❌ ERROR: {parsed_file} not found!")
        print("   Please run download_subscriptions.py first")
        sys.exit(1)
    
    proxies = load_parsed_proxies(parsed_file)
    if not proxies:
        print("❌ ERROR: No proxies loaded!")
        sys.exit(1)
    
    print(f"  Loaded {len(proxies)} proxy configurations")
    
    # Count by protocol
    by_protocol = {}
    for proxy in proxies:
        protocol = proxy.get('type', 'unknown')
        by_protocol[protocol] = by_protocol.get(protocol, 0) + 1
    
    print("\n📊 Proxy Distribution:")
    for protocol, count in sorted(by_protocol.items()):
        print(f"  • {protocol}: {count}")
    
    # Initialize port manager
    print("\n🔧 Initializing port manager...")
    start_port = int(os.environ.get('PORT_START', '10000'))
    end_port = int(os.environ.get('PORT_END', '20000'))
    port_manager = PortManager(start_port=start_port, end_port=end_port)
    print(f"  Port range: {start_port}-{end_port}")
    
    # Test parameters
    max_workers = int(os.environ.get('TEST_WORKERS', '50'))
    batch_size = int(os.environ.get('BATCH_SIZE', '50'))
    
    print(f"\n⚙️  Test Parameters:")
    print(f"  • Max Parallel Workers: {max_workers}")
    print(f"  • Batch Size: {batch_size}")
    
    # Start testing
    print("\n" + "="*70)
    print("🧪 STARTING COMPREHENSIVE PROXY TESTING")
    print("="*70)
    
    start_time = time.time()
    
    try:
        working_proxies, stats, latencies = test_group_proxies(
            group_name='ALL PROXIES',
            proxies=proxies,
            clash_path=clash_path,
            temp_dir=temp_dir,
            port_manager=port_manager,
            max_workers=max_workers,
            test_timeout=TEST_TIMEOUT,
            batch_size=batch_size
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  Testing interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ ERROR during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    elapsed_time = time.time() - start_time
    
    # Results
    print("\n" + "="*70)
    print("📊 TEST RESULTS SUMMARY")
    print("="*70)
    
    total_tested = stats['total']
    total_working = stats['working']
    success_rate = (total_working / total_tested * 100) if total_tested > 0 else 0
    
    print(f"\n✅ Working Proxies: {total_working}/{total_tested} ({success_rate:.1f}%)")
    
    if latencies:
        avg_latency = sum(latencies.values()) / len(latencies)
        min_latency = min(latencies.values())
        max_latency = max(latencies.values())
        
        print(f"\n⏱️  Latency Statistics:")
        print(f"  • Average: {avg_latency:.1f}ms")
        print(f"  • Minimum: {min_latency:.1f}ms")
        print(f"  • Maximum: {max_latency:.1f}ms")
    
    print(f"\n⏳ Total Time: {elapsed_time:.1f}s")
    print(f"📈 Average per Proxy: {elapsed_time/total_tested:.2f}s")
    
    # Save results
    if working_proxies:
        print("\n💾 Saving working configurations...")
        metadata = save_working_proxies(working_proxies, latencies, working_dir)
        
        print(f"\n📊 Saved Results by Protocol:")
        for protocol, count in metadata['by_protocol'].items():
            print(f"  • {protocol}: {count} working proxies")
        
        print(f"\n✅ All results saved to: {working_dir}/")
    else:
        print("\n⚠️  No working proxies found!")
        # Still create metadata with zero counts
        metadata = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'total_working': 0,
            'average_latency': 0,
            'by_protocol': {},
            'test_config': {
                'pass_rate': TEST_PASS_RATE,
                'timeout': TEST_TIMEOUT,
                'retry': TEST_RETRY,
                'min_passes': MIN_REQUIRED_PASSES
            }
        }
        os.makedirs(working_dir, exist_ok=True)
        metadata_file = os.path.join(working_dir, 'metadata.json')
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
    
    print("\n" + "="*70)
    print("🎉 TESTING COMPLETED SUCCESSFULLY")
    print("="*70 + "\n")
    
    # Exit with appropriate code
    if total_working == 0:
        print("⚠️  Warning: No working proxies found")
        sys.exit(0)  # Don't fail the build, just warn
    
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
