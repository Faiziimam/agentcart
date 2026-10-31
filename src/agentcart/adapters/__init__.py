"""Adapter registry. Adding a store = adding one module here (see docs/ADAPTERS.md)."""

from __future__ import annotations

from ..errors import CheckoutError
from ..netsafe import dev_loopback_enabled, host_matches, registrable_host
from .base import SiteAdapter
from .demo import DemoShopAdapter


def all_adapters() -> list[SiteAdapter]:
    adapters: list[SiteAdapter] = []
    if dev_loopback_enabled():
        adapters.append(DemoShopAdapter())
    return adapters


def for_url(url: str) -> SiteAdapter:
    host = registrable_host(url)
    for a in all_adapters():
        if any(host_matches(host, d) for d in a.domains):
            return a
    supported = ", ".join(d for a in all_adapters() for d in a.domains) or "none yet"
    raise CheckoutError(
        f"no checkout adapter for '{host}' yet (supported: {supported}). "
        "fetch_product still works on any site; contributions welcome, see docs/ADAPTERS.md")


def by_name(name: str) -> SiteAdapter:
    for a in all_adapters():
        if a.name == name:
            return a
    raise CheckoutError(f"unknown site '{name}'")
