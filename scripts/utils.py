"""
Helper utilities for Clash config testing
"""
import base64
import json
import re
import urllib.parse
from typing import List, Dict, Optional


def decode_base64(data: str) -> str:
    """
    Decode base64 encoded string, handling padding issues
    """
    try:
        # Remove whitespace and newlines
        data = data.strip()

        # Add padding if needed
        missing_padding = len(data) % 4
        if missing_padding:
            data += '=' * (4 - missing_padding)

        decoded = base64.b64decode(data).decode('utf-8')
        return decoded
    except Exception as e:
        print(f"Base64 decode error: {e}")
        return data


def is_base64(s: str) -> bool:
    """
    Check if string is base64 encoded
    """
    try:
        # Remove whitespace
        s = s.strip()
        # Check if it looks like base64
        if re.match(r'^[A-Za-z0-9+/]*={0,2}$', s):
            # Try to decode
            base64.b64decode(s)
            return True
        return False
    except:
        return False


def parse_vmess(vmess_url: str) -> Optional[Dict]:
    """
    Parse VMess URL to dict
    Format: vmess://base64(json)
    """
    try:
        if not vmess_url.startswith('vmess://'):
            return None

        encoded = vmess_url[8:]  # Remove 'vmess://'
        decoded = decode_base64(encoded)
        config = json.loads(decoded)

        return {
            'type': 'vmess',
            'name': config.get('ps', 'VMess'),
            'server': config.get('add', ''),
            'port': int(config.get('port', 443)),
            'uuid': config.get('id', ''),
            'alterId': int(config.get('aid', 0)),
            'cipher': config.get('scy', 'auto'),
            'network': config.get('net', 'tcp'),
            'tls': config.get('tls', '') == 'tls',
            'ws-opts': {
                'path': config.get('path', '/'),
                'headers': {
                    'Host': config.get('host', '')
                }
            } if config.get('net') == 'ws' else None
        }
    except Exception as e:
        print(f"Error parsing VMess: {e}")
        return None


def parse_vless(vless_url: str) -> Optional[Dict]:
    """
    Parse VLESS URL to dict
    Format: vless://uuid@server:port?params#name
    """
    try:
        if not vless_url.startswith('vless://'):
            return None

        # Remove protocol
        url = vless_url[8:]

        # Split name if exists
        if '#' in url:
            url, name = url.split('#', 1)
            name = urllib.parse.unquote(name)
        else:
            name = 'VLESS'

        # Split params if exists
        if '?' in url:
            url, params_str = url.split('?', 1)
            params = urllib.parse.parse_qs(params_str)
        else:
            params = {}

        # Parse uuid@server:port
        uuid, server_port = url.split('@', 1)
        server, port = server_port.rsplit(':', 1)

        config = {
            'type': 'vless',
            'name': name,
            'server': server,
            'port': int(port),
            'uuid': uuid,
            'flow': params.get('flow', [''])[0],
            'network': params.get('type', ['tcp'])[0],
            'tls': params.get('security', [''])[0] in ['tls', 'reality'],
        }

        # Add network specific options
        if config['network'] == 'ws':
            config['ws-opts'] = {
                'path': params.get('path', ['/'])[0],
                'headers': {
                    'Host': params.get('host', [''])[0]
                }
            }

        return config
    except Exception as e:
        print(f"Error parsing VLESS: {e}")
        return None


def parse_ss(ss_url: str) -> Optional[Dict]:
    """
    Parse Shadowsocks URL to dict
    Format: ss://base64(method:password)@server:port#name
    """
    try:
        if not ss_url.startswith('ss://'):
            return None

        # Remove protocol
        url = ss_url[5:]

        # Split name if exists
        if '#' in url:
            url, name = url.split('#', 1)
            name = urllib.parse.unquote(name)
        else:
            name = 'Shadowsocks'

        # Check if credentials are base64 encoded
        if '@' in url:
            parts = url.split('@', 1)
            if len(parts) == 2:
                # Try to decode first part
                try:
                    creds = decode_base64(parts[0])
                    method, password = creds.split(':', 1)
                except:
                    # Already decoded format: method:password@server:port
                    method, password = parts[0].split(':', 1)

                server, port = parts[1].split(':', 1)
                # Remove any query params from port
                port = port.split('?')[0].split('#')[0]
        else:
            # Fully base64 encoded
            decoded = decode_base64(url)
            if '@' in decoded:
                creds, server_port = decoded.split('@', 1)
                method, password = creds.split(':', 1)
                server, port = server_port.split(':', 1)
            else:
                return None

        return {
            'type': 'ss',
            'name': name,
            'server': server,
            'port': int(port),
            'cipher': method,
            'password': password
        }
    except Exception as e:
        print(f"Error parsing Shadowsocks: {e}")
        return None


def parse_trojan(trojan_url: str) -> Optional[Dict]:
    """
    Parse Trojan URL to dict
    Format: trojan://password@server:port?params#name
    """
    try:
        if not trojan_url.startswith('trojan://'):
            return None

        # Remove protocol
        url = trojan_url[9:]

        # Split name if exists
        if '#' in url:
            url, name = url.split('#', 1)
            name = urllib.parse.unquote(name)
        else:
            name = 'Trojan'

        # Split params if exists
        if '?' in url:
            url, params_str = url.split('?', 1)
            params = urllib.parse.parse_qs(params_str)
        else:
            params = {}

        # Parse password@server:port
        password, server_port = url.split('@', 1)
        server, port = server_port.split(':', 1)
        # Remove any remaining query params
        port = port.split('?')[0]

        config = {
            'type': 'trojan',
            'name': name,
            'server': server,
            'port': int(port),
            'password': password,
            'sni': params.get('sni', [''])[0] or server,
            'skip-cert-verify': params.get('allowInsecure', ['0'])[0] == '1'
        }

        # Add network options if not tcp
        network = params.get('type', [''])[0]
        if network == 'ws':
            config['network'] = 'ws'
            config['ws-opts'] = {
                'path': params.get('path', ['/'])[0],
                'headers': {
                    'Host': params.get('host', [server])[0]
                }
            }

        return config
    except Exception as e:
        print(f"Error parsing Trojan: {e}")
        return None


def parse_proxy_url(url: str) -> Optional[Dict]:
    """
    Parse any proxy URL to dict
    """
    url = url.strip()

    if url.startswith('vmess://'):
        return parse_vmess(url)
    elif url.startswith('vless://'):
        return parse_vless(url)
    elif url.startswith('ss://'):
        return parse_ss(url)
    elif url.startswith('trojan://'):
        return parse_trojan(url)
    else:
        return None


def proxy_to_clash_format(proxy: Dict) -> Dict:
    """
    Convert parsed proxy dict to Clash format
    """
    # Remove None values
    return {k: v for k, v in proxy.items() if v is not None and v != ''}


def generate_clash_config(proxies: List[Dict], test_url: str = "http://www.gstatic.com/generate_204") -> Dict:
    """
    Generate a Clash configuration file
    """
    config = {
        'port': 7890,
        'socks-port': 7891,
        'allow-lan': False,
        'mode': 'rule',
        'log-level': 'info',
        'external-controller': '127.0.0.1:9090',
        'proxies': proxies,
        'proxy-groups': [
            {
                'name': 'PROXY',
                'type': 'select',
                'proxies': [p['name'] for p in proxies]
            }
        ],
        'rules': [
            'MATCH,PROXY'
        ]
    }

    return config