"""Bounded HTTPS fetches. Never log response bodies or credentials."""
import ipaddress
import os
import socket
import time
from functools import lru_cache
from urllib.parse import urljoin, urlsplit
import httpx

class FetchError(Exception):
    def __init__(self, message, retryable=False, retry_after=1):
        super().__init__(message)
        self.retryable=retryable
        self.retry_after=retry_after

@lru_cache(maxsize=256)
def resolve_addresses(host):
    try:
        return socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return None

def validate_url(url, allowed_hosts=None):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise FetchError('Je povoleno pouze veřejné HTTPS bez přihlašovacích údajů v URL.')
    if allowed_hosts is not None and p.hostname not in allowed_hosts:
        raise FetchError('Host přílohy není v allowed_hosts: ' + p.hostname)
    if '.' not in p.hostname or p.hostname.endswith(('.localhost','.local','.internal','.test')) or p.hostname == 'localhost':
        raise FetchError('Lokální hostname není povolen.')
    try:
        literal=ipaddress.ip_address(p.hostname)
    except ValueError:
        literal=None
    if literal is not None and not literal.is_global:
        raise FetchError('Privátní IP není povolená.')
    addresses = resolve_addresses(p.hostname)
    if addresses is None:
        # In cloud sandboxes DNS is performed by the configured HTTPS proxy.
        # Only explicitly configured hosts may use that supported route.
        if allowed_hosts is not None and p.hostname in allowed_hosts and any(os.getenv(k) for k in ('HTTPS_PROXY','https_proxy','ALL_PROXY','all_proxy')):
            return url
        raise FetchError('Nelze přeložit jméno hostu: ' + p.hostname)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise FetchError('Privátní a neveřejné adresy jsou zakázané.')
    return url

class Web:
    def __init__(self, allowed_hosts):
        self.allowed_hosts = allowed_hosts
        self.client = httpx.Client(timeout=40, headers={'User-Agent': 'UzemniMonitor/1.0 (daily municipal notice monitoring)'})

    def close(self):
        self.client.close()

    def get(self, url, limit=20_000_000):
        for attempt in range(3):
            try:
                return self._get_once(url,limit)
            except FetchError as e:
                if not e.retryable or attempt==2:
                    raise
                time.sleep(max(2**attempt,e.retry_after))

    def _get_once(self, url, limit):
        for _ in range(6):
            validate_url(url, self.allowed_hosts)
            try:
                with self.client.stream('GET', url) as r:
                    if r.is_redirect:
                        url = urljoin(url, r.headers['location'])
                        continue
                    if r.status_code != 200:
                        try:
                            retry_after=min(10,max(1,int(r.headers.get('Retry-After','1'))))
                        except ValueError:
                            retry_after=2
                        raise FetchError(f'HTTP {r.status_code} od {urlsplit(url).hostname}',
                                         r.status_code in {408,429,500,502,503,504},retry_after)
                    parts, size = [], 0
                    for chunk in r.iter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise FetchError('Dokument překročil limit velikosti.')
                        parts.append(chunk)
                    return b''.join(parts)
            except httpx.HTTPError as e:
                raise FetchError('Síťová chyba: ' + type(e).__name__,retryable=True) from e
        raise FetchError('Příliš mnoho přesměrování.')
