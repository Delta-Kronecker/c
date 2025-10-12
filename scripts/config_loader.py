"""
Configuration loader for Clash Config Auto-Tester
Loads settings from config.yaml with fallback to defaults
"""
import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional


class Config:
    """Configuration manager"""
    
    DEFAULT_CONFIG = {
        'download': {
            'max_workers': 10,
            'retry_count': 3,
            'timeout': 30,
            'user_agent': 'ClashForAndroid/2.5.12',
            'remove_duplicates': True
        },
        'test': {
            'max_workers': 20,
            'timeout': 10,
            'start_port': 17890,
            'clash_startup_wait': 1.5,
            'test_urls': [
                'http://www.gstatic.com/generate_204',
                'http://connectivitycheck.gstatic.com/generate_204',
                'http://cp.cloudflare.com/generate_204'
            ],
            'measure_latency': True,
            'retest_working': False
        },
        'output': {
            'directory': 'working_configs',
            'formats': {
                'json': True,
                'text': True,
                'base64': True,
                'clash_yaml': True,
                'by_protocol': True
            },
            'save_metadata': True,
            'include_latency': True,
            'sort_by_latency': False
        },
        'clash': {
            'binary_path': '',
            'mode': 'global',
            'log_level': 'silent',
            'allow_lan': False
        },
        'filters': {
            'enabled': False,
            'protocols': [],
            'countries': [],
            'max_latency': 1000,
            'min_latency': 0,
            'include_keywords': [],
            'exclude_keywords': []
        },
        'logging': {
            'verbose': False,
            'file': '',
            'level': 'INFO'
        },
        'advanced': {
            'cleanup_temp_files': True,
            'keep_failed_logs': False,
            'max_clash_instances': 20,
            'connection_pooling': True,
            'process_cleanup_timeout': 3
        },
        'subscriptions': {
            'file': 'sub.txt',
            'auto_update': True,
            'cache_enabled': False,
            'cache_ttl': 3600
        },
        'github_actions': {
            'auto_commit': True,
            'create_artifacts': True,
            'artifact_retention': 7,
            'generate_summary': True
        }
    }
    
    def __init__(self, config_file: Optional[str] = None):
        """Initialize configuration"""
        self.config_file = config_file or 'config.yaml'
        self.config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or use defaults"""
        # Start with default config
        config = self.DEFAULT_CONFIG.copy()
        
        # Try to load from file
        config_path = Path(self.config_file)
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    user_config = yaml.safe_load(f)
                
                if user_config:
                    # Merge user config with defaults
                    config = self._deep_merge(config, user_config)
                    print(f"✓ Loaded configuration from {config_path}")
            except Exception as e:
                print(f"⚠ Error loading config file: {e}")
                print("  Using default configuration")
        else:
            print(f"ℹ Config file not found, using defaults")
        
        # Override with environment variables
        config = self._apply_env_overrides(config)
        
        return config
    
    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        """Deep merge two dictionaries"""
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def _apply_env_overrides(self, config: Dict) -> Dict:
        """Apply environment variable overrides"""
        # Test settings
        if 'TEST_WORKERS' in os.environ:
            try:
                config['test']['max_workers'] = int(os.environ['TEST_WORKERS'])
            except ValueError:
                pass
        
        if 'TEST_TIMEOUT' in os.environ:
            try:
                config['test']['timeout'] = int(os.environ['TEST_TIMEOUT'])
            except ValueError:
                pass
        
        # Download settings
        if 'DOWNLOAD_WORKERS' in os.environ:
            try:
                config['download']['max_workers'] = int(os.environ['DOWNLOAD_WORKERS'])
            except ValueError:
                pass
        
        # Output directory
        if 'OUTPUT_DIR' in os.environ:
            config['output']['directory'] = os.environ['OUTPUT_DIR']
        
        # Clash binary path
        if 'CLASH_PATH' in os.environ:
            config['clash']['binary_path'] = os.environ['CLASH_PATH']
        
        return config
    
    def get(self, section: str, key: Optional[str] = None, default: Any = None) -> Any:
        """Get configuration value"""
        try:
            if key is None:
                return self.config.get(section, default)
            return self.config.get(section, {}).get(key, default)
        except (KeyError, AttributeError):
            return default
    
    def get_download_config(self) -> Dict:
        """Get download configuration"""
        return self.config.get('download', {})
    
    def get_test_config(self) -> Dict:
        """Get test configuration"""
        return self.config.get('test', {})
    
    def get_output_config(self) -> Dict:
        """Get output configuration"""
        return self.config.get('output', {})
    
    def get_clash_config(self) -> Dict:
        """Get Clash configuration"""
        return self.config.get('clash', {})
    
    def get_filter_config(self) -> Dict:
        """Get filter configuration"""
        return self.config.get('filters', {})
    
    def is_filter_enabled(self) -> bool:
        """Check if filtering is enabled"""
        return self.config.get('filters', {}).get('enabled', False)
    
    def should_filter_protocol(self, protocol: str) -> bool:
        """Check if protocol should be filtered"""
        if not self.is_filter_enabled():
            return False
        
        allowed = self.config.get('filters', {}).get('protocols', [])
        if not allowed:
            return False
        
        return protocol not in allowed
    
    def should_filter_latency(self, latency: float) -> bool:
        """Check if proxy should be filtered by latency"""
        if not self.is_filter_enabled():
            return False
        
        filters = self.config.get('filters', {})
        min_lat = filters.get('min_latency', 0)
        max_lat = filters.get('max_latency', 1000)
        
        return latency < min_lat or latency > max_lat
    
    def apply_filters(self, proxy: Dict) -> bool:
        """Check if proxy passes all filters"""
        if not self.is_filter_enabled():
            return True
        
        filters = self.config.get('filters', {})
        
        # Protocol filter
        if filters.get('protocols'):
            if proxy.get('type') not in filters['protocols']:
                return False
        
        # Latency filter
        if proxy.get('latency'):
            if self.should_filter_latency(proxy['latency']):
                return False
        
        # Keyword filters
        name = proxy.get('name', '').lower()
        
        if filters.get('include_keywords'):
            if not any(kw.lower() in name for kw in filters['include_keywords']):
                return False
        
        if filters.get('exclude_keywords'):
            if any(kw.lower() in name for kw in filters['exclude_keywords']):
                return False
        
        return True
    
    def print_config(self):
        """Print current configuration"""
        print("\n" + "=" * 60)
        print("Current Configuration")
        print("=" * 60)
        
        print("\n📥 Download:")
        dl = self.get_download_config()
        print(f"  Workers: {dl.get('max_workers')}")
        print(f"  Retry: {dl.get('retry_count')}")
        print(f"  Timeout: {dl.get('timeout')}s")
        
        print("\n🧪 Test:")
        test = self.get_test_config()
        print(f"  Workers: {test.get('max_workers')}")
        print(f"  Timeout: {test.get('timeout')}s")
        print(f"  Start Port: {test.get('start_port')}")
        
        print("\n💾 Output:")
        out = self.get_output_config()
        print(f"  Directory: {out.get('directory')}")
        formats = out.get('formats', {})
        enabled_formats = [k for k, v in formats.items() if v]
        print(f"  Formats: {', '.join(enabled_formats)}")
        
        if self.is_filter_enabled():
            print("\n🔍 Filters: ENABLED")
            filt = self.get_filter_config()
            if filt.get('protocols'):
                print(f"  Protocols: {', '.join(filt['protocols'])}")
            if filt.get('max_latency'):
                print(f"  Max Latency: {filt['max_latency']}ms")
        else:
            print("\n🔍 Filters: DISABLED")
        
        print("\n" + "=" * 60 + "\n")


# Global config instance
_config_instance = None


def get_config(config_file: Optional[str] = None) -> Config:
    """Get global configuration instance"""
    global _config_instance
    
    if _config_instance is None:
        _config_instance = Config(config_file)
    
    return _config_instance


def reload_config(config_file: Optional[str] = None):
    """Reload configuration"""
    global _config_instance
    _config_instance = Config(config_file)
    return _config_instance


# Example usage
if __name__ == '__main__':
    config = get_config()
    config.print_config()
    
    # Test filter
    test_proxy = {
        'type': 'vmess',
        'name': 'Test Proxy',
        'latency': 150
    }
    
    print(f"Test proxy passes filters: {config.apply_filters(test_proxy)}")