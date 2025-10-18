"""
Enhanced helper utilities for Clash config testing
With improved validation and error handling
"""
import base64
import json
import re
import urllib.parse
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import hashlib


@dataclass
class ProxyInfo:
    """Data class for proxy information"""
    type: str
    name: str
    server: str
    port: int
    config: Dict
    hash: str


def calculate_proxy_hash(proxy: Dict) -> str:
    """Calculate unique hash for proxy to detect duplicates"""
    key_fields = f"{proxy.get('type')}:{proxy.get('server')}:{proxy.get('port')}"
    return hashlib.md5(key_fields.encode()).hexdigest()[:8]


def is_valid_domain(domain: str) -> bool:
    """Validate domain name or IP address"""
    # Check for IP address
    ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
    if re.match(ip_pattern, domain):
        # Validate IP range
        parts = domain.split('.')
        return all(0 <= int(part) <= 255 for part in parts)
    
    # Check for domain name
    domain_pattern = r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$'
    return bool(re.match(domain_pattern, domain)) and len(domain) <= 253


def validate_proxy_config(proxy: Dict) -> Tuple[bool, str]:
    """Enhanced proxy configuration validation"""
    required_fields = ['type', 'name', 'server', 'port']
    
    # Check required fields
    for field in required_fields:
        if field not in proxy or not proxy[field]:
            return False, f"Missing required field: {field}"
    
    # Validate proxy type
    valid_types = ['vmess', 'vless', 'ss', 'ssr', 'trojan']
    if proxy['type'] not in valid_types:
        return False, f"Invalid proxy type: {proxy['type']}"
    
    # Validate server
    server = str(proxy['server']).strip()
    if not server or len(server) > 253:
        return False, f"Invalid server: {server}"
    
    if not is_valid_domain(server):
        return False, f"Invalid server format: {server}"
    
    # Validate port
    try:
        port = int(proxy['port'])
        if not (1 <= port <= 65535):
            return False, f"Invalid port range: {port}"
    except (ValueError, TypeError):
        return False, f"Invalid port format: {proxy['port']}"
    
    # Validate protocol-specific fields
    ptype = proxy['type']
    
    if ptype == 'vmess':
        if 'uuid' not in proxy or not proxy['uuid']:
            return False, "VMess missing UUID"
        # Validate UUID format
        uuid_pattern = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
        if not re.match(uuid_pattern, proxy['uuid'].lower()):
            return False, "Invalid VMess UUID format"
    
    elif ptype == 'vless':
        if 'uuid' not in proxy or not proxy['uuid']:
            return False, "VLESS missing UUID"
        uuid_pattern = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
        if not re.match(uuid_pattern, proxy['uuid'].lower()):
            return False, "Invalid VLESS UUID format"
    
    elif ptype in ['ss', 'ssr']:
        if 'cipher' not in proxy or not proxy['cipher']:
            return False, f"{ptype.upper()} missing cipher"
        if 'password' not in proxy or not proxy['password']:
            return False, f"{ptype.upper()} missing password"
    
    elif ptype == 'trojan':
        if 'password' not in proxy or not proxy['password']:
            return False, "Trojan missing password"
    
    return True, "OK"


def decode_base64(data: str) -> str:
    """Decode base64 with improved error handling"""
    try:
        data = data.strip()
        
        # Handle URL-safe base64
        data = data.replace('-', '+').replace('_', '/')
        
        # Add padding
        missing_padding = len(data) % 4
        if missing_padding:
            data += '=' * (4 - missing_padding)
        
        decoded = base64.b64decode(data).decode('utf-8', errors='ignore')
        return decoded
    except Exception:
        return data


def is_base64(s: str) -> bool:
    """Check if string is base64 encoded"""
    try:
        s = s.strip().replace('-', '+').replace('_', '/')
        if not re.match(r'^[A-Za-z0-9+/]*={0,2}$', s):
            return False
        base64.b64decode(s)
        return len(s) > 20  # Avoid false positives
    except:
        return False


def parse_vmess(vmess_url: str) -> Optional[Dict]:
    """Parse VMess URL with enhanced validation"""
    try:
        if not vmess_url.startswith('vmess://'):
            return None
        
        encoded = vmess_url[8:].strip()
        if not encoded:
            return None
        
        decoded = decode_base64(encoded)
        config = json.loads(decoded)
        
        server = config.get('add', '').strip()
        port = config.get('port', 443)
        uuid = config.get('id', '').strip()
        
        if not server or not port or not uuid:
            return None
        
        # Validate UUID format
        uuid_pattern = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
        if not re.match(uuid_pattern, uuid.lower()):
            return None
        
        proxy = {
            'type': 'vmess',
            'name': config.get('ps', f'VMess-{server}').strip() or f'VMess-{server}',
            'server': server,
            'port': int(port),
            'uuid': uuid,
            'alterId': int(config.get('aid', 0)),
            'cipher': config.get('scy', 'auto'),
            'network': config.get('net', 'tcp'),
        }
        
        # TLS settings
        if config.get('tls') == 'tls':
            proxy['tls'] = True
            if config.get('sni'):
                proxy['servername'] = config.get('sni').strip()
        
        # Network specific options
        net = config.get('net', 'tcp')
        if net == 'ws':
            proxy['network'] = 'ws'
            proxy['ws-opts'] = {
                'path': config.get('path', '/'),
                'headers': {'Host': config.get('host', server)}
            }
        elif net == 'grpc':
            proxy['network'] = 'grpc'
            proxy['grpc-opts'] = {
                'grpc-service-name': config.get('path', '')
            }
        elif net == 'h2':
            proxy['network'] = 'h2'
            proxy['h2-opts'] = {
                'host': [config.get('host', server)],
                'path': config.get('path', '/')
            }
        
        return proxy
    except Exception:
        return None


def parse_vless(vless_url: str) -> Optional[Dict]:
    """Parse VLESS URL with enhanced validation"""
    try:
        if not vless_url.startswith('vless://'):
            return None
        
        url = vless_url[8:].strip()
        if not url:
            return None
        
        # Parse name
        if '#' in url:
            url, name = url.rsplit('#', 1)
            name = urllib.parse.unquote(name).strip()
        else:
            name = 'VLESS'
        
        # Parse params
        if '?' in url:
            url, params_str = url.split('?', 1)
            params = urllib.parse.parse_qs(params_str)
        else:
            params = {}
        
        # Parse uuid@server:port
        if '@' not in url:
            return None
        
        uuid, server_port = url.split('@', 1)
        uuid = uuid.strip()
        
        if ':' not in server_port:
            return None
        
        server, port = server_port.rsplit(':', 1)
        server = server.strip()
        
        # Validate UUID format
        uuid_pattern = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
        if not re.match(uuid_pattern, uuid.lower()):
            return None
        
        proxy = {
            'type': 'vless',
            'name': name or f'VLESS-{server}',
            'server': server,
            'port': int(port),
            'uuid': uuid,
            'network': params.get('type', ['tcp'])[0],
        }
        
        # Flow control
        if params.get('flow'):
            proxy['flow'] = params.get('flow')[0]
        
        # Security/TLS
        security = params.get('security', [''])[0]
        if security in ['tls', 'reality']:
            proxy['tls'] = True
            if security == 'reality':
                proxy['reality-opts'] = {
                    'public-key': params.get('pbk', [''])[0],
                    'short-id': params.get('sid', [''])[0],
                }
            if params.get('sni'):
                proxy['servername'] = params.get('sni')[0]
        
        # Network options
        if proxy['network'] == 'ws':
            proxy['ws-opts'] = {
                'path': params.get('path', ['/'])[0],
                'headers': {'Host': params.get('host', [server])[0]}
            }
        elif proxy['network'] == 'grpc':
            proxy['grpc-opts'] = {
                'grpc-service-name': params.get('serviceName', [''])[0]
            }
        
        return proxy
    except Exception:
        return None


def parse_ss(ss_url: str) -> Optional[Dict]:
    """Parse Shadowsocks URL with enhanced validation"""
    try:
        if not ss_url.startswith('ss://'):
            return None
        
        url = ss_url[5:].strip()
        if not url:
            return None
        
        # Parse name
        if '#' in url:
            url, name = url.rsplit('#', 1)
            name = urllib.parse.unquote(name).strip()
        else:
            name = 'Shadowsocks'
        
        # Remove query params
        url = url.split('?')[0]
        
        # Parse credentials and server
        if '@' in url:
            creds_part, server_part = url.rsplit('@', 1)
            
            # Try to decode credentials
            try:
                creds = decode_base64(creds_part)
                if ':' in creds:
                    method, password = creds.split(':', 1)
                else:
                    return None
            except:
                # Already in plain format
                if ':' in creds_part:
                    method, password = creds_part.split(':', 1)
                else:
                    return None
            
            # Parse server:port
            if ':' not in server_part:
                return None
            server, port = server_part.rsplit(':', 1)
        else:
            # Fully encoded format
            decoded = decode_base64(url)
            if '@' not in decoded or ':' not in decoded:
                return None
            
            creds, server_port = decoded.rsplit('@', 1)
            method, password = creds.split(':', 1)
            server, port = server_port.rsplit(':', 1)
        
        server = server.strip()
        method = method.strip()
        password = password.strip()
        
        if not server or not method or not password:
            return None
        
        # Validate cipher method
        valid_ciphers = [
            'aes-128-gcm', 'aes-192-gcm', 'aes-256-gcm',
            'aes-128-cfb', 'aes-192-cfb', 'aes-256-cfb',
            'aes-128-ctr', 'aes-192-ctr', 'aes-256-ctr',
            'chacha20-ietf-poly1305', 'xchacha20-ietf-poly1305',
            'rc4-md5'
        ]
        
        if method not in valid_ciphers:
            return None
        
        return {
            'type': 'ss',
            'name': name or f'SS-{server}',
            'server': server,
            'port': int(port),
            'cipher': method,
            'password': password
        }
    except Exception:
        return None


def parse_trojan(trojan_url: str) -> Optional[Dict]:
    """Parse Trojan URL with enhanced validation"""
    try:
        if not trojan_url.startswith('trojan://'):
            return None
        
        url = trojan_url[9:].strip()
        if not url:
            return None
        
        # Parse name
        if '#' in url:
            url, name = url.rsplit('#', 1)
            name = urllib.parse.unquote(name).strip()
        else:
            name = 'Trojan'
        
        # Parse params
        if '?' in url:
            url, params_str = url.split('?', 1)
            params = urllib.parse.parse_qs(params_str)
        else:
            params = {}
        
        # Parse password@server:port
        if '@' not in url:
            return None
        
        password, server_port = url.rsplit('@', 1)
        password = urllib.parse.unquote(password).strip()
        
        if ':' not in server_port:
            return None
        
        server, port = server_port.rsplit(':', 1)
        server = server.strip()
        port = port.split('?')[0]  # Remove any leftover params
        
        if not password or not server:
            return None
        
        proxy = {
            'type': 'trojan',
            'name': name or f'Trojan-{server}',
            'server': server,
            'port': int(port),
            'password': password,
            'skip-cert-verify': params.get('allowInsecure', ['0'])[0] == '1'
        }
        
        # SNI
        if params.get('sni'):
            proxy['sni'] = params.get('sni')[0]
        
        # Network type
        network = params.get('type', [''])[0]
        if network == 'ws':
            proxy['network'] = 'ws'
            proxy['ws-opts'] = {
                'path': params.get('path', ['/'])[0],
                'headers': {'Host': params.get('host', [server])[0]}
            }
        elif network == 'grpc':
            proxy['network'] = 'grpc'
            proxy['grpc-opts'] = {
                'grpc-service-name': params.get('serviceName', [''])[0]
            }
        
        return proxy
    except Exception:
        return None


def parse_ssr(ssr_url: str) -> Optional[Dict]:
    """Parse ShadowsocksR URL with enhanced validation"""
    try:
        if not ssr_url.startswith('ssr://'):
            return None
        
        encoded = ssr_url[6:].strip()
        if not encoded:
            return None
        
        decoded = decode_base64(encoded)
        
        # Parse SSR format: server:port:protocol:method:obfs:password_base64/?params
        parts = decoded.split('/?')
        main_part = parts[0]
        params = urllib.parse.parse_qs(parts[1]) if len(parts) > 1 else {}
        
        components = main_part.split(':')
        if len(components) < 6:
            return None
        
        server, port, protocol, method, obfs, password_b64 = components[:6]
        server = server.strip()
        protocol = protocol.strip()
        method = method.strip()
        obfs = obfs.strip()
        
        password = decode_base64(password_b64).strip()
        
        if not server or not protocol or not method or not obfs or not password:
            return None
        
        name = params.get('remarks', [''])[0]
        if name:
            name = decode_base64(name).strip()
        name = name or f'SSR-{server}'
        
        return {
            'type': 'ssr',
            'name': name,
            'server': server,
            'port': int(port),
            'cipher': method,
            'password': password,
            'protocol': protocol,
            'obfs': obfs,
            'protocol-param': decode_base64(params.get('protoparam', [''])[0]) if params.get('protoparam') else '',
            'obfs-param': decode_base64(params.get('obfsparam', [''])[0]) if params.get('obfsparam') else '',
        }
    except Exception:
        return None


def parse_proxy_url(url: str) -> Optional[Dict]:
    """Parse any supported proxy URL with validation"""
    url = url.strip()
    
    if not url or len(url) < 10:
        return None
    
    parsers = {
        'vmess://': parse_vmess,
        'vless://': parse_vless,
        'ss://': parse_ss,
        'trojan://': parse_trojan,
        'ssr://': parse_ssr,
    }
    
    for prefix, parser in parsers.items():
        if url.startswith(prefix):
            proxy = parser(url)
            if proxy:
                # Validate configuration
                is_valid, msg = validate_proxy_config(proxy)
                if is_valid:
                    proxy['hash'] = calculate_proxy_hash(proxy)
                    return proxy
            return None
    
    return None


def proxy_to_clash_format(proxy: Dict) -> Dict:
    """Convert proxy to Clash format with cleanup"""
    clash_proxy = {}
    
    # Copy all non-None values
    for k, v in proxy.items():
        if k == 'hash':  # Skip internal fields
            continue
        if v is not None and v != '' and v != {} and v != []:
            clash_proxy[k] = v
    
    return clash_proxy


def proxy_to_share_url(proxy: Dict) -> str:
    """Reconstruct proper share URL from proxy config"""
    ptype = proxy.get('type', '')
    
    try:
        if ptype == 'vmess':
            return reconstruct_vmess_url(proxy)
        elif ptype == 'vless':
            return reconstruct_vless_url(proxy)
        elif ptype == 'ss':
            return reconstruct_ss_url(proxy)
        elif ptype == 'trojan':
            return reconstruct_trojan_url(proxy)
        elif ptype == 'ssr':
            return reconstruct_ssr_url(proxy)
    except Exception:
        pass
    
    # Fallback
    return f"{ptype}://{proxy.get('server')}:{proxy.get('port')}"


def reconstruct_vmess_url(proxy: Dict) -> str:
    """Reconstruct VMess share URL"""
    config = {
        'v': '2',
        'ps': proxy['name'],
        'add': proxy['server'],
        'port': str(proxy['port']),
        'id': proxy['uuid'],
        'aid': str(proxy.get('alterId', 0)),
        'net': proxy.get('network', 'tcp'),
        'type': 'none',
        'host': '',
        'path': '',
        'tls': 'tls' if proxy.get('tls') else '',
        'sni': proxy.get('servername', ''),
        'scy': proxy.get('cipher', 'auto'),
    }
    
    # Add network specific options
    if proxy.get('ws-opts'):
        config['path'] = proxy['ws-opts'].get('path', '/')
        config['host'] = proxy['ws-opts'].get('headers', {}).get('Host', '')
    elif proxy.get('grpc-opts'):
        config['path'] = proxy['grpc-opts'].get('grpc-service-name', '')
    
    json_str = json.dumps(config, separators=(',', ':'))
    encoded = base64.b64encode(json_str.encode()).decode()
    return f"vmess://{encoded}"


def reconstruct_vless_url(proxy: Dict) -> str:
    """Reconstruct VLESS share URL"""
    params = {
        'type': proxy.get('network', 'tcp'),
        'security': 'tls' if proxy.get('tls') else 'none',
    }
    
    if proxy.get('flow'):
        params['flow'] = proxy['flow']
    
    if proxy.get('servername'):
        params['sni'] = proxy['servername']
    
    if proxy.get('ws-opts'):
        params['path'] = proxy['ws-opts'].get('path', '/')
        params['host'] = proxy['ws-opts'].get('headers', {}).get('Host', '')
    elif proxy.get('grpc-opts'):
        params['serviceName'] = proxy['grpc-opts'].get('grpc-service-name', '')
    
    param_str = urllib.parse.urlencode(params)
    name = urllib.parse.quote(proxy['name'])
    
    return f"vless://{proxy['uuid']}@{proxy['server']}:{proxy['port']}?{param_str}#{name}"


def reconstruct_ss_url(proxy: Dict) -> str:
    """Reconstruct Shadowsocks share URL"""
    creds = f"{proxy['cipher']}:{proxy['password']}"
    encoded_creds = base64.b64encode(creds.encode()).decode()
    name = urllib.parse.quote(proxy['name'])
    
    return f"ss://{encoded_creds}@{proxy['server']}:{proxy['port']}#{name}"


def reconstruct_trojan_url(proxy: Dict) -> str:
    """Reconstruct Trojan share URL"""
    params = {}
    
    if proxy.get('sni'):
        params['sni'] = proxy['sni']
    
    if proxy.get('skip-cert-verify'):
        params['allowInsecure'] = '1'
    
    if proxy.get('network') == 'ws':
        params['type'] = 'ws'
        if proxy.get('ws-opts'):
            params['path'] = proxy['ws-opts'].get('path', '/')
            params['host'] = proxy['ws-opts'].get('headers', {}).get('Host', '')
    
    param_str = '?' + urllib.parse.urlencode(params) if params else ''
    name = urllib.parse.quote(proxy['name'])
    password = urllib.parse.quote(proxy['password'])
    
    return f"trojan://{password}@{proxy['server']}:{proxy['port']}{param_str}#{name}"


def reconstruct_ssr_url(proxy: Dict) -> str:
    """Reconstruct ShadowsocksR share URL"""
    password_b64 = base64.b64encode(proxy['password'].encode()).decode()
    
    main = f"{proxy['server']}:{proxy['port']}:{proxy['protocol']}:{proxy['cipher']}:{proxy['obfs']}:{password_b64}"
    
    params = {
        'remarks': base64.b64encode(proxy['name'].encode()).decode(),
    }
    
    if proxy.get('protocol-param'):
        params['protoparam'] = base64.b64encode(proxy['protocol-param'].encode()).decode()
    if proxy.get('obfs-param'):
        params['obfsparam'] = base64.b64encode(proxy['obfs-param'].encode()).decode()
    
    param_str = urllib.parse.urlencode(params)
    full = f"{main}/?{param_str}"
    encoded = base64.b64encode(full.encode()).decode()
    
    return f"ssr://{encoded}"


def generate_clash_config(proxies: List[Dict], port: int = 7890) -> Dict:
    """Generate Clash configuration"""
    config = {
        'mixed-port': port,
        'allow-lan': False,
        'mode': 'global',
        'log-level': 'silent',
        'external-controller': f'127.0.0.1:{port + 100}',
        'proxies': [proxy_to_clash_format(p) for p in proxies],
        'proxy-groups': [{
            'name': 'PROXY',
            'type': 'select',
            'proxies': [p['name'] for p in proxies]
        }],
        'rules': ['MATCH,PROXY']
    }
    
    return config
