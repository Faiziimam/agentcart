"""SSRF-hardened HTTP fetching for untrusted, model-supplied URLs.

Rules (all enforced in code, never by the model):
* https only (plain http is allowed solely for loopback when AGENTCART_DEV_LOOPBACK=1, for the demo shop/tests)
* hostnames must resolve exclusively to public unicast addresses
* the connection is pinned to the validated IP (defeats DNS rebinding), TLS is still verified
  against the hostname via SNI
* every redirect hop is re-validated, at most 5 hops
* response body capped (default 3 MiB), short timeouts, HTML content types only
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from .errors import UnsafeURLError

MAX_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(15.0, connect=8.0)
USER_AGENT = "Mozilla/5.0 (compatible; agentcart/0.1; +https://github.com/Faiziimam/agentcart)"
ALLOWED_TYPES = ("text/html", "application/xhtml+xml")


def dev_loopback_enabled() -> bool:
    return os.environ.get("AGENTCART_DEV_LOOPBACK") == "1"


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    scheme: str
    host: str
    port: int
    ip: str
    loopback_dev: bool


def validate_url_syntax(url: str) -> tuple[str, str, int]:
    if not isinstance(url, str) or len(url) > 2048:
        raise UnsafeURLError("URL missing or too long")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no host")
    if scheme not in ("https", "http"):
        raise UnsafeURLError(f"scheme '{scheme}' is not allowed; use https")
    port = parts.port or (443 if scheme == "https" else 80)
    return scheme, host, port


async def resolve_and_validate(url: str) -> ValidatedTarget:
    scheme, host, port = validate_url_syntax(url)
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        addrs = [literal]
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise UnsafeURLError(f"cannot resolve host '{host}'") from e
        addrs = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not addrs:
        raise UnsafeURLError(f"cannot resolve host '{host}'")

    if all(a.is_loopback for a in addrs) and dev_loopback_enabled():
        return ValidatedTarget(url, scheme, host, port, str(addrs[0]), loopback_dev=True)
    # Every resolved address must be public: a mixed answer is a rebinding/split-horizon red flag.
    for a in addrs:
        if not _is_public(a):
            raise UnsafeURLError(f"host '{host}' resolves to a non-public address; refusing")
    if scheme != "https":
        raise UnsafeURLError("only https URLs are allowed")
    return ValidatedTarget(url, scheme, host, port, str(addrs[0]), loopback_dev=False)


def _proxy_configured() -> bool:
    return any(os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"))


async def _request(client: httpx.AsyncClient, target: ValidatedTarget) -> httpx.Response:
    parts = urlsplit(target.url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    if _proxy_configured() or target.loopback_dev:
        # Through an explicit proxy the proxy resolves DNS; we already validated the name.
        req = client.build_request("GET", target.url)
    else:
        ip = f"[{target.ip}]" if ":" in target.ip else target.ip
        pinned = f"{target.scheme}://{ip}:{target.port}{path}"
        req = client.build_request(
            "GET", pinned, headers={"Host": target.host}, extensions={"sni_hostname": target.host}
        )
    return await client.send(req, stream=True)


@dataclass(frozen=True)
class FetchResult:
    final_url: str
    status: int
    content_type: str
    text: str


async def fetch_html(url: str, max_bytes: int = MAX_BYTES) -> FetchResult:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=False, headers=headers, trust_env=True
    ) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            target = await resolve_and_validate(current)
            resp = await _request(client, target)
            try:
                if resp.is_redirect:
                    loc = resp.headers.get("location")
                    if not loc:
                        raise UnsafeURLError("redirect without location")
                    current = urljoin(current, loc)
                    continue
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype not in ALLOWED_TYPES:
                    raise UnsafeURLError(f"unexpected content type '{ctype or 'none'}'")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise UnsafeURLError("page too large")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise UnsafeURLError("page too large")
                text = buf.decode(resp.encoding or "utf-8", errors="replace")
                return FetchResult(current, resp.status_code, ctype, text)
            finally:
                await resp.aclose()
        raise UnsafeURLError("too many redirects")


def registrable_host(url: str) -> str:
    """Lower-cased host without a leading 'www.'; used for allowlists and adapter routing."""
    _, host, _ = validate_url_syntax(url)
    return host[4:] if host.startswith("www.") else host


def host_matches(host: str, allowed: str) -> bool:
    host, allowed = host.lower().rstrip("."), allowed.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host == allowed or host.endswith("." + allowed)
