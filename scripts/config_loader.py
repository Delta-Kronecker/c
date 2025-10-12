#!/usr/bin/env python3
"""
Main runner script for Clash Config Auto-Tester
Integrates download, test, and output modules with configuration
"""
import sys
import time
import argparse
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent / 'scripts'))

from config_loader import get_config
from download_subscriptions import SubscriptionDownloader
from test_configs import ClashTester, ResultSaver, find_clash_binary, load_proxies


class ClashConfigRunner:
    """Main runner for the testing pipeline"""
    
    def __init__(self, config_file=None):
        """Initialize runner with configuration"""
        self.config = get_config(config_file)
        self.stats = {
            'download_time': 0,
            'parse_time': 0,
            'test_time': 0,
            'total_time': 0,
            'proxies_downloaded': 0,
            'proxies_parsed': 0,
            'proxies_tested': 0,
            'proxies_working': 0
        }
    
    def print_banner(self):
        """Print application banner"""
        print("\n" + "=" * 70)
        print("   ⚡ Clash Config Auto-Tester - Enhanced Edition ⚡")
        print("=" * 70)
        print("   High-performance parallel proxy testing system")
        print("   Supports: VMess, VLESS, Shadowsocks, SSR, Trojan")
        print("=" * 70 + "\n")
    
    def validate_environment(self):
        """Validate environment and dependencies"""
        print("🔍 Validating environment...")
        
        # Check subscription file
        sub_file = self.config.get('subscriptions', 'file', 'sub.txt')
        if not Path(sub_file).exists():
            print(f"✗ Subscription file not found: {sub_file}")
            print(f"  Please create {sub_file} with your subscription URLs")
            return False
        
        # Check Clash binary
        clash_path = self.config.get('clash', 'binary_path')
        if not clash_path:
            clash_path = find_clash_binary()
        
        if not clash_path:
            print("✗ Clash binary not found")
            print("  Please install Clash or Clash Meta")
            return False
        
        print(f"✓ Clash binary: {clash_path}")
        print(f"✓ Subscription file: {sub_file}")
        
        return True
    
    def download_phase(self):
        """Download subscriptions"""
        print("\n" + "━" * 70)
        print("📥 Phase 1: Downloading Subscriptions")
        print("━" * 70 + "\n")
        
        dl_config = self.config.get_download_config()
        
        downloader = SubscriptionDownloader(
            max_workers=dl_config.get('max_workers', 10),
            retry_count=dl_config.get('retry_count', 3),
            timeout=dl_config.get('timeout', 30)
        )
        
        # Read subscription URLs
        sub_file = self.config.get('subscriptions', 'file', 'sub.txt')
        urls = downloader.read_subscription_urls(sub_file)
        
        if not urls:
            print("✗ No subscription URLs found")
            return None
        
        # Download
        start_time = time.time()
        proxy_urls = downloader.download_all_parallel(urls)
        self.stats['download_time'] = time.time() - start_time
        self.stats['proxies_downloaded'] = len(proxy_urls)
        
        if not proxy_urls:
            print("✗ No proxies downloaded")
            return None
        
        print(f"\n✓ Downloaded {len(proxy_urls)} proxies in {self.stats['download_time']:.1f}s")
        
        # Parse
        start_time = time.time()
        parsed_proxies = downloader.parse_proxies_parallel(proxy_urls)
        self.stats['parse_time'] = time.time() - start_time
        self.stats['proxies_parsed'] = len(parsed_proxies)
        
        if not parsed_proxies:
            print("✗ No proxies could be parsed")
            return None
        
        print(f"✓ Parsed {len(parsed_proxies)} proxies in {self.stats['parse_time']:.1f}s")
        
        # Apply filters if enabled
        if self.config.is_filter_enabled():
            print("\n🔍 Applying filters...")
            original_count = len(parsed_proxies)
            parsed_proxies = [p for p in parsed_proxies if self.config.apply_filters(p)]
            filtered_count = original_count - len(parsed_proxies)
            print(f"  Filtered out {filtered_count} proxies")
            print(f"  Remaining: {len(parsed_proxies)} proxies")
        
        # Save temporary results
        base_dir = Path.cwd()
        temp_dir = base_dir / 'temp_configs'
        downloader.save_results(proxy_urls, parsed_proxies, str(temp_dir))
        
        return parsed_proxies
    
    def test_phase(self, proxies):
        """Test proxy configurations"""
        print("\n" + "━" * 70)
        print("🧪 Phase 2: Testing Proxy Configurations")
        print("━" * 70 + "\n")
        
        test_config = self.config.get_test_config()
        clash_config = self.config.get_clash_config()
        
        # Find Clash binary
        clash_path = clash_config.get('binary_path') or find_clash_binary()
        
        if not clash_path:
            print("✗ Clash binary not found")
            return None
        
        # Initialize tester
        tester = ClashTester(
            clash_path=clash_path,
            max_workers=test_config.get('max_workers', 20),
            test_timeout=test_config.get('timeout', 10)
        )
        
        # Update test URLs if configured
        test_urls = test_config.get('test_urls')
        if test_urls:
            tester.test_urls = test_urls
        
        # Test proxies
        start_time = time.time()
        self.stats['proxies_tested'] = len(proxies)
        
        working_proxies = tester.test_proxies_parallel(proxies)
        
        self.stats['test_time'] = time.time() - start_time
        self.stats['proxies_working'] = len(working_proxies)
        
        # Apply latency filter if enabled
        if self.config.is_filter_enabled() and working_proxies:
            print("\n🔍 Applying latency filters...")
            original_count = len(working_proxies)
            working_proxies = [
                p for p in working_proxies 
                if not self.config.should_filter_latency(p.get('latency', 0))
            ]
            filtered_count = original_count - len(working_proxies)
            if filtered_count > 0:
                print(f"  Filtered out {filtered_count} proxies by latency")
                self.stats['proxies_working'] = len(working_proxies)
        
        # Sort by latency if configured
        if self.config.get('output', 'sort_by_latency', False) and working_proxies:
            print("\n📊 Sorting by latency...")
            working_proxies.sort(key=lambda x: x.get('latency', 999999))
        
        return working_proxies
    
    def save_phase(self, proxies):
        """Save results"""
        print("\n" + "━" * 70)
        print("💾 Phase 3: Saving Results")
        print("━" * 70 + "\n")
        
        output_config = self.config.get_output_config()
        output_dir = output_config.get('directory', 'working_configs')
        
        saver = ResultSaver(output_dir)
        saver.save_all_formats(proxies)
        
        return True
    
    def print_summary(self):
        """Print execution summary"""
        print("\n" + "=" * 70)
        print("📊 Execution Summary")
        print("=" * 70)
        
        print(f"\n⏱️  Timing:")
        print(f"  Download:  {self.stats['download_time']:.1f}s")
        print(f"  Parse:     {self.stats['parse_time']:.1f}s")
        print(f"  Test:      {self.stats['test_time']:.1f}s")
        print(f"  Total:     {self.stats['total_time']:.1f}s")
        
        print(f"\n📈 Results:")
        print(f"  Downloaded: {self.stats['proxies_downloaded']}")
        print(f"  Parsed:     {self.stats['proxies_parsed']}")
        print(f"  Tested:     {self.stats['proxies_tested']}")
        print(f"  Working:    {self.stats['proxies_working']}")
        
        if self.stats['proxies_tested'] > 0:
            success_rate = (self.stats['proxies_working'] / self.stats['proxies_tested']) * 100
            print(f"  Success:    {success_rate:.1f}%")
        
        if self.stats['test_time'] > 0:
            test_speed = self.stats['proxies_tested'] / self.stats['test_time']
            print(f"\n⚡ Speed:     {test_speed:.1f} proxies/sec")
        
        print("\n" + "=" * 70)
        
        if self.stats['proxies_working'] > 0:
            print("\n✅ Testing completed successfully!")
            output_dir = self.config.get('output', 'directory', 'working_configs')
            print(f"📦 Results saved to: {output_dir}/")
        else:
            print("\n⚠️  No working proxies found")
        
        print()
    
    def run(self):
        """Run the complete testing pipeline"""
        self.print_banner()
        
        # Print configuration
        if self.config.get('logging', 'verbose', False):
            self.config.print_config()
        
        # Validate environment
        if not self.validate_environment():
            return False
        
        start_time = time.time()
        
        try:
            # Phase 1: Download
            proxies = self.download_phase()
            if not proxies:
                return False
            
            # Phase 2: Test
            working_proxies = self.test_phase(proxies)
            if not working_proxies:
                print("\n⚠️  No working proxies found")
                return False
            
            # Phase 3: Save
            self.save_phase(working_proxies)
            
            # Calculate total time
            self.stats['total_time'] = time.time() - start_time
            
            # Print summary
            self.print_summary()
            
            return True
            
        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user")
            return False
        except Exception as e:
            print(f"\n\n✗ Error: {e}")
            if self.config.get('logging', 'verbose', False):
                import traceback
                traceback.print_exc()
            return False


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Clash Config Auto-Tester - Enhanced Edition',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '-c', '--config',
        help='Configuration file path (default: config.yaml)',
        default=None
    )
    
    parser.add_argument(
        '-v', '--verbose',
        help='Enable verbose output',
        action='store_true'
    )
    
    parser.add_argument(
        '--workers',
        help='Number of test workers',
        type=int,
        default=None
    )
    
    parser.add_argument(
        '--timeout',
        help='Test timeout in seconds',
        type=int,
        default=None
    )
    
    args = parser.parse_args()
    
    # Override config with command line arguments
    if args.workers:
        import os
        os.environ['TEST_WORKERS'] = str(args.workers)
    
    if args.timeout:
        import os
        os.environ['TEST_TIMEOUT'] = str(args.timeout)
    
    if args.verbose:
        import os
        os.environ['VERBOSE'] = '1'
    
    # Run the pipeline
    runner = ClashConfigRunner(args.config)
    success = runner.run()
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()