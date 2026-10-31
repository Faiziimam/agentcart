"""Hardened Playwright browser sessions.

* one persistent profile per site, stored on-device (0700) -- you log in once yourself with
  'agentcart login <site>'; agentcart never sees or stores your password
* headed (visible) by default so you can always watch what the agent does
* main-frame navigations outside the adapter's own domains (plus known payment gateways) are blocked
* downloads, geolocation, camera, mic, notifications and clipboard are denied
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, Playwright, Route, async_playwright

from . import paths
from .netsafe import dev_loopback_enabled, host_matches

# Hosted payment pages Indian merchants commonly redirect to. Navigation to these is allowed.
PAYMENT_GATEWAYS = (
    "razorpay.com", "payu.in", "payumoney.com", "cashfree.com", "ccavenue.com", "paytm.in",
    "paytm.com", "phonepe.com", "juspay.in", "billdesk.com", "stripe.com", "easebuzz.in",
)


def _executable() -> str | None:
    explicit = os.environ.get("AGENTCART_CHROMIUM")
    if explicit:
        return explicit
    return None


@dataclass
class Session:
    context: BrowserContext
    page: Page


class BrowserManager:
    def __init__(self) -> None:
        self._pw: Playwright | None = None

    async def _playwright(self) -> Playwright:
        if self._pw is None:
            self._pw = await async_playwright().start()
        return self._pw

    async def open(self, site: str, domains: tuple[str, ...], headless: bool | None = None) -> Session:
        pw = await self._playwright()
        if headless is None:
            headless = os.environ.get("AGENTCART_HEADLESS") == "1"
        profile = paths.ensure_private_dir(paths.profiles_dir() / site)
        kwargs: dict[str, Any] = {
            "headless": headless,
            "accept_downloads": False,
            "permissions": [],
            "service_workers": "block",
            "locale": "en-IN",
            "timezone_id": "Asia/Kolkata",
        }
        exe = _executable()
        if exe:
            kwargs["executable_path"] = exe
        ctx = await pw.chromium.launch_persistent_context(str(profile), **kwargs)
        allowed = tuple(domains) + PAYMENT_GATEWAYS

        async def guard(route: Route) -> None:
            req = route.request
            if req.is_navigation_request():
                main = req.frame.parent_frame is None
                # Top-level page: only the store + payment gateways. Iframes (captcha, 3-D Secure,
                # gateway widgets) may be any https origin, never plain http or private hosts.
                ok = (_navigation_allowed(req.url, allowed) if main
                      else _navigation_allowed(req.url, None))
                if not ok:
                    await route.abort("blockedbyclient")
                    return
            await route.continue_()

        await ctx.route("**/*", guard)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.set_default_timeout(20_000)
        return Session(ctx, page)

    async def close(self) -> None:
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None


def _navigation_allowed(url: str, allowed: tuple[str, ...] | None) -> bool:
    """allowed=None means 'any public https origin' (used for iframes)."""
    parts = urlsplit(url)
    if parts.scheme in ("about", "data") and url.startswith(("about:blank", "data:text/html")):
        return url == "about:blank"
    host = (parts.hostname or "").lower()
    if dev_loopback_enabled():
        try:
            if ipaddress.ip_address(host).is_loopback:
                return parts.scheme in ("http", "https")
        except ValueError:
            if host == "localhost":
                return True
    if parts.scheme != "https" or _is_private_literal(host) or host == "localhost":
        return False
    return allowed is None or any(host_matches(host, d) for d in allowed)


def _is_private_literal(host: str) -> bool:
    try:
        return not ipaddress.ip_address(host.strip("[]")).is_global
    except ValueError:
        return False
