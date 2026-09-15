"""Public-network probes. Resolve once and connect to the validated IP (DNS rebinding safe)."""
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urljoin
import urllib3


def resolve_public(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("仅支持不含账号密码的 HTTP/HTTPS 公网地址")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("禁止监测内网、回环、链路本地或保留地址")
    return parsed, addresses[0][4][0], port


def probe(url):
    try:
        for _ in range(6):
            parsed, address, port = resolve_public(url)
            kwargs = {"timeout": urllib3.Timeout(connect=5, read=10), "retries": False}
            if parsed.scheme == "https":
                pool = urllib3.HTTPSConnectionPool(address, port, server_hostname=parsed.hostname,
                    assert_hostname=parsed.hostname, cert_reqs="CERT_REQUIRED", **kwargs)
            else:
                pool = urllib3.HTTPConnectionPool(address, port, **kwargs)
            with pool:
                response = pool.urlopen("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                    headers={"Host": parsed.netloc, "User-Agent": "BudMon/2.0"},
                    redirect=False, preload_content=False)
                code, location = response.status, response.headers.get("Location")
                response.close()
            if code in {301,302,303,307,308} and location:
                url = urljoin(url, location)
                continue
            return 200 <= code < 400, code, None if code < 400 else f"HTTP {code}"
        return False, None, "重定向次数过多"
    except Exception as exc:
        return False, None, str(exc)[:500]
