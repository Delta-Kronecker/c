"""
Enhanced subscription downloader with retry, validation, and better error handling
"""
import os
import sys
import json
import time
import requests
from pathlib import Path
from typing import List, Set, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils import (
    decode_base64, is_base64, parse_proxy_url,
    validate_proxy_config, calculate_proxy_hash
)


class SubscriptionDownloader:
    """Enhanced subscription downloader"""
    
    def __init__(self, max_workers: int = 10, retry_count: int = 3, timeout: int = 30):
        self.max_workers = max_workers
        self.retry_count = retry_count
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'ClashForAndroid/2.5.12'
        })
    
    def read_subscription_urls(self, file_path: str) -> List[str]:
        """Read and validate subscription URLs"""
        urls = []
        
        if not os.path.exists(file_path):
            print(f"Error: Subscription file not found: {file_path}")
            return urls
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Basic URL validation
                    if line.startswith(('http://', 'https://')):
                        urls.append(line)
                    else:
                        print(f"Warning: Invalid URL at line {line_num}: {line}")
            
            print(f"✓ Loaded {len(urls)} subscription URLs")
            return urls
            
        except Exception as e:
            print(f"Error reading subscription file: {e}")
            return []
    
    def download_with_retry(self, url: str) -> str:
        """Download subscription content with retry mechanism"""
        last_error = None
        
        for attempt in range(self.retry_count):
            try:
                if attempt > 0:
                    wait_time = 2 ** attempt  # Exponential backoff
                    print(f"  Retry {attempt + 1}/{self.retry_count} after {wait_time}s...")
                    time.sleep(wait_time)
                
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                
                # Check if response is not empty
                if not response.text or len(response.text) < 10:
                    raise ValueError("Empty or invalid response")
                
                return response.text
                
            except requests.exceptions.Timeout:
                last_error = "Timeout"
            except requests.exceptions.ConnectionError:
                last_error = "Connection error"
            except requests.exceptions.HTTPError as e:
                last_error = f"HTTP {e.response.status_code}"
            except Exception as e:
                last_error = str(e)
        
        print(f"  ✗ Failed after {self.retry_count} attempts: {last_error}")
        return ""
    
    def parse_subscription_content(self, content: str) -> List[str]:
        """Parse subscription content and extract proxy URLs"""
        proxy_urls = []
        
        if not content:
            return proxy_urls
        
        lines = content.strip().split('\n')
        
        # Check if content is base64 encoded
        if len(lines) == 1 and is_base64(content.strip()):
            try:
                decoded = decode_base64(content.strip())
                lines = decoded.split('\n')
            except Exception as e:
                print(f"  Warning: Failed to decode base64 content: {e}")
        
        # Extract proxy URLs
        supported_protocols = ['ss://', 'ssr://', 'vmess://', 'vless://', 'trojan://']
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Check if line is a valid proxy URL
            if any(line.startswith(prefix) for prefix in supported_protocols):
                proxy_urls.append(line)
        
        return proxy_urls
    
    def download_subscription(self, url: str, index: int, total: int) -> Dict:
        """Download and parse a single subscription"""
        result = {
            'url': url,
            'success': False,
            'proxy_urls': [],
            'error': None
        }
        
        print(f"\n[{index}/{total}] Downloading: {url[:80]}...")
        
        # Download content
        content = self.download_with_retry(url)
        
        if not content:
            result['error'] = "Download failed"
            return result
        
        # Parse proxy URLs
        proxy_urls = self.parse_subscription_content(content)
        
        if proxy_urls:
            result['success'] = True
            result['proxy_urls'] = proxy_urls
            print(f"  ✓ Found {len(proxy_urls)} proxy URLs")
        else:
            result['error'] = "No valid proxies found"
            print(f"  ⚠ No valid proxies found in subscription")
        
        return result
    
    def download_all_parallel(self, urls: List[str]) -> List[str]:
        """Download all subscriptions in parallel"""
        all_proxy_urls = []
        seen_urls = set()
        
        print(f"\nDownloading {len(urls)} subscriptions (parallel)...")
        print("=" * 60)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all download tasks
            futures = {
                executor.submit(self.download_subscription, url, i, len(urls)): url
                for i, url in enumerate(urls, 1)
            }
            
            # Process results as they complete
            for future in as_completed(futures):
                result = future.result()
                
                if result['success']:
                    # Add unique proxy URLs
                    for proxy_url in result['proxy_urls']:
                        if proxy_url not in seen_urls:
                            all_proxy_urls.append(proxy_url)
                            seen_urls.add(proxy_url)
        
        return all_proxy_urls
    
    def parse_proxies_parallel(self, proxy_urls: List[str]) -> List[Dict]:
        """Parse proxy URLs in parallel with validation"""
        parsed_proxies = []
        seen_hashes = set()
        failed_count = 0
        
        print(f"\nParsing {len(proxy_urls)} proxy URLs...")
        
        def parse_single(url: str) -> Dict:
            proxy = parse_proxy_url(url)
            if proxy:
                is_valid, msg = validate_proxy_config(proxy)
                if is_valid:
                    return proxy
            return None
        
        # Parse in parallel
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(parse_single, url) for url in proxy_urls]
            
            for i, future in enumerate(as_completed(futures), 1):
                if i % 100 == 0:
                    print(f"  Progress: {i}/{len(proxy_urls)}")
                
                proxy = future.result()
                if proxy:
                    # Check for duplicates using hash
                    proxy_hash = proxy.get('hash', '')
                    if proxy_hash not in seen_hashes:
                        parsed_proxies.append(proxy)
                        seen_hashes.add(proxy_hash)
                else:
                    failed_count += 1
        
        print(f"✓ Successfully parsed {len(parsed_proxies)} unique proxies")
        if failed_count > 0:
            print(f"  ⚠ Failed to parse {failed_count} proxies")
        
        return parsed_proxies
    
    def save_results(self, proxy_urls: List[str], parsed_proxies: List[Dict], output_dir: str):
        """Save download and parse results"""
        os.makedirs(output_dir, exist_ok=True)
        
        # Save raw proxy URLs
        raw_file = os.path.join(output_dir, 'raw_proxies.txt')
        with open(raw_file, 'w', encoding='utf-8') as f:
            for url in proxy_urls:
                f.write(url + '\n')
        print(f"✓ Saved raw URLs to: {raw_file}")
        
        # Save parsed proxies
        json_file = os.path.join(output_dir, 'parsed_proxies.json')
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(parsed_proxies, f, indent=2, ensure_ascii=False)
        print(f"✓ Saved parsed proxies to: {json_file}")
        
        # Save statistics
        stats = {
            'total_raw': len(proxy_urls),
            'total_parsed': len(parsed_proxies),
            'by_type': {}
        }
        
        for proxy in parsed_proxies:
            ptype = proxy.get('type', 'unknown')
            stats['by_type'][ptype] = stats['by_type'].get(ptype, 0) + 1
        
        stats_file = os.path.join(output_dir, 'download_stats.json')
        with open(stats_file, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2)
        
        # Print statistics
        print(f"\n{'=' * 60}")
        print("Proxy Statistics:")
        print(f"  Total Raw URLs: {stats['total_raw']}")
        print(f"  Total Parsed: {stats['total_parsed']}")
        print(f"  Success Rate: {stats['total_parsed'] / stats['total_raw'] * 100:.1f}%")
        print("\nBy Protocol:")
        for ptype, count in sorted(stats['by_type'].items(), key=lambda x: -x[1]):
            print(f"  {ptype}: {count}")
        print(f"{'=' * 60}")


def main():
    """Main function"""
    print("=" * 60)
    print("Clash Config Subscription Downloader (Enhanced)")
    print("=" * 60)
    
    # Get paths
    base_dir = Path(__file__).parent.parent
    sub_file = base_dir / 'sub.txt'
    output_dir = base_dir / 'temp_configs'
    
    # Check if subscription file exists
    if not sub_file.exists():
        print(f"\nError: Subscription file not found: {sub_file}")
        print("Please create sub.txt with subscription URLs (one per line)")
        sys.exit(1)
    
    # Initialize downloader
    downloader = SubscriptionDownloader(
        max_workers=10,
        retry_count=3,
        timeout=30
    )
    
    # Read subscription URLs
    urls = downloader.read_subscription_urls(str(sub_file))
    
    if not urls:
        print("No valid subscription URLs found")
        sys.exit(1)
    
    # Download all subscriptions
    start_time = time.time()
    proxy_urls = downloader.download_all_parallel(urls)
    download_time = time.time() - start_time
    
    if not proxy_urls:
        print("\n✗ No proxy URLs found in any subscription")
        sys.exit(1)
    
    print(f"\n✓ Downloaded {len(proxy_urls)} unique proxy URLs in {download_time:.1f}s")
    
    # Parse all proxies
    start_time = time.time()
    parsed_proxies = downloader.parse_proxies_parallel(proxy_urls)
    parse_time = time.time() - start_time
    
    if not parsed_proxies:
        print("\n✗ No proxies could be parsed successfully")
        sys.exit(1)
    
    print(f"✓ Parsed in {parse_time:.1f}s")
    
    # Save results
    downloader.save_results(proxy_urls, parsed_proxies, str(output_dir))
    
    print(f"\n{'=' * 60}")
    print(f"✓ Download complete! Ready for testing.")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()