import pytest

from agentcart.errors import UnsafeURLError
from agentcart.netsafe import host_matches, resolve_and_validate


@pytest.mark.parametrize("url", [
    "http://example.com/",                     # plain http
    "file:///etc/passwd",
    "ftp://example.com/x",
    "javascript:alert(1)",
    "https://user:pw@example.com/",            # embedded creds
    "https://127.0.0.1/",                      # loopback
    "https://localhost/",
    "https://10.0.0.5/",
    "https://192.168.1.1/",
    "https://169.254.169.254/latest/meta-data",  # cloud metadata
    "https://[::1]/",
    "https://[::ffff:127.0.0.1]/",             # ipv4-mapped loopback
    "https://0.0.0.0/",
    "https://100.64.0.1/",                     # CGNAT
    "https://" + "a" * 3000 + ".com/",
])
async def test_rejects_unsafe(url):
    with pytest.raises(UnsafeURLError):
        await resolve_and_validate(url)


async def test_loopback_http_only_in_dev(monkeypatch):
    monkeypatch.setenv("AGENTCART_DEV_LOOPBACK", "1")
    t = await resolve_and_validate("http://127.0.0.1:8765/x")
    assert t.loopback_dev
    with pytest.raises(UnsafeURLError):
        await resolve_and_validate("http://10.0.0.1/")


async def test_public_literal_ok():
    t = await resolve_and_validate("https://1.1.1.1/")
    assert t.ip == "1.1.1.1"


@pytest.mark.parametrize("host,allowed,ok", [
    ("example.com", "example.com", True),
    ("www.example.com", "example.com", True),
    ("shop.example.com", "example.com", True),
    ("evilexample.com", "example.com", False),
    ("example.com.evil.io", "example.com", False),
])
def test_host_matches(host, allowed, ok):
    assert host_matches(host, allowed) is ok
