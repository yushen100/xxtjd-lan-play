"""服务器和 Windows 启动入口共用同一 CIDR 配置。"""
import ipaddress
import json
from pathlib import Path

class NetworkPolicy:
    def __init__(self, config_path=None):
        path = config_path or Path(__file__).resolve().parent.parent / '1011_网络规则_v1.json'
        self.networks = tuple(ipaddress.IPv4Network(cidr) for cidr in json.loads(Path(path).read_text(encoding='utf-8-sig'))['allowedIpv4Cidrs'])

    def is_allowed(self, address):
        try:
            ip = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            return False
        return any(ip in network for network in self.networks)
