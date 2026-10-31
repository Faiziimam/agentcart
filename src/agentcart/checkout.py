"""Checkout orchestration shared by the MCP server and the CLI. All security decisions live here."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import segno

from . import audit, paths, policy, vault
from .adapters import for_url
from .adapters.base import OrderResult, Quote, SiteAdapter, UpiIntent
from .browser import BrowserManager, Session
from .errors import AgentCartError, CheckoutError, PolicyViolation
from .extract import Product, extract_product
from .netsafe import fetch_html, registrable_host, validate_url_syntax
from .sanitize import UNTRUSTED_NOTE

MAX_SESSIONS = 3
SESSION_TTL_S = 20 * 60
MAX_QTY = 10


async def fetch_product(url: str) -> dict[str, Any]:
    res = await fetch_html(url)
    product = extract_product(res.text, res.final_url)
    return {"source_url": res.final_url, "note": UNTRUSTED_NOTE, "untrusted": product.to_public(),
            "found": bool(product.name or product.price)}


@dataclass
class Checkout:
    id: str
    adapter: SiteAdapter
    session: Session
    host: str
    product_url: str
    quote: Quote
    listed_price: Decimal | None
    upi: UpiIntent | None
    created: float = field(default_factory=time.monotonic)
    state: str = "awaiting_payment"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _audit(event: str, data: dict[str, Any]) -> None:
    """Best-effort log when no vault exists yet (Flow 1 works without a vault); strict otherwise."""
    if not vault.exists():
        return
    pol = policy.load()
    if pol.genesis:
        audit.append(pol.genesis, event, data)


class CheckoutManager:
    def __init__(self) -> None:
        self.browser = BrowserManager()
        self.checkouts: dict[str, Checkout] = {}

    async def _reap(self) -> None:
        now = time.monotonic()
        for cid, c in list(self.checkouts.items()):
            if now - c.created > SESSION_TTL_S and c.state != "paying":
                await self._close(cid)

    async def _close(self, cid: str) -> None:
        c = self.checkouts.pop(cid, None)
        if c:
            with contextlib.suppress(Exception):  # closing a dead browser is fine
                await c.session.context.close()

    def _get(self, cid: str) -> Checkout:
        c = self.checkouts.get(cid)
        if not c:
            raise CheckoutError("unknown or expired checkout_id; start a new checkout")
        return c

    async def start(self, url: str, quantity: int) -> dict[str, Any]:
        validate_url_syntax(url)
        if not 1 <= quantity <= MAX_QTY:
            raise CheckoutError(f"quantity must be between 1 and {MAX_QTY}")
        await self._reap()
        if len(self.checkouts) >= MAX_SESSIONS:
            raise CheckoutError("too many open checkouts; cancel one first")
        adapter = for_url(url)
        listed: Product | None = None
        try:
            res = await fetch_html(url)
            listed = extract_product(res.text, res.final_url)
        except AgentCartError:
            listed = None
        if any(c.adapter.name == adapter.name for c in self.checkouts.values()):
            raise CheckoutError(f"a checkout on {adapter.name} is already open; finish or cancel it")
        session = await self.browser.open(adapter.name, adapter.domains)
        try:
            quote = await adapter.prepare_checkout(session.page, url, quantity)
            upi = await adapter.select_upi(session.page, quote)
        except Exception:
            await session.context.close()
            raise
        warnings: list[str] = []
        listed_price = listed.price if listed else None
        if listed_price is not None and quote.items and quote.items[0].unit_price > listed_price:
            warnings.append(f"cart price ₹{quote.items[0].unit_price} is higher than listed ₹{listed_price}")
        if upi:
            if upi.amount != quote.total:
                warnings.append(f"UPI request amount {upi.amount} does not match total {quote.total} — "
                                "do NOT pay")
            if (upi.currency or "INR") != "INR":
                warnings.append("UPI request is not in INR — do NOT pay")
        cid = "co_" + secrets.token_urlsafe(9)
        self.checkouts[cid] = Checkout(cid, adapter, session, registrable_host(url), url, quote,
                                       listed_price, upi)
        _audit("checkout_started", {"id": cid, "site": adapter.name, "total": str(quote.total)})
        out: dict[str, Any] = {
            "checkout_id": cid,
            "site": adapter.name,
            "quote": quote.to_public(),
            "warnings": warnings,
            "how_to_pay": (
                "Flow 1 (default): the browser window is open on the payment page. Scan the UPI QR "
                "there (or the QR below) with your UPI app, CHECK the payee name and amount, and "
                "enter your UPI PIN on your phone. agentcart never sees your PIN."),
            "autopay_available": adapter.supports_autopay,
        }
        if upi:
            qr_path = paths.ensure_private_dir(paths.home() / "qr") / f"{cid}.png"
            qr = segno.make(upi.raw, error="m")
            qr.save(str(qr_path), scale=8)
            out["upi"] = {"payee_vpa": upi.payee_vpa, "payee_name": upi.payee_name,
                          "amount": str(upi.amount) if upi.amount is not None else None,
                          "qr_png": str(qr_path)}
        return out

    async def autopay(self, cid: str) -> dict[str, Any]:
        """Flow 2. Every rule is enforced here; any failure falls back to Flow 1."""
        c = self._get(cid)
        async with c.lock:
            if c.state != "awaiting_payment":
                raise CheckoutError(f"checkout is '{c.state}', cannot pay again")
            if not c.adapter.supports_autopay:
                raise PolicyViolation(f"{c.adapter.name} does not support auto-pay; pay with UPI instead")
            pol = policy.load()  # verifies signature; TamperError fails closed
            listed_total = (c.listed_price * sum(i.quantity for i in c.quote.items) + c.quote.shipping
                            if c.listed_price is not None else Decimal(0))
            d = policy.evaluate_autopay(pol, c.host, c.quote.total, c.quote.currency, listed_total)
            if not d.allowed:
                if pol.genesis:
                    audit.append(pol.genesis, "autopay_denied",
                                 {"id": cid, "amount": str(c.quote.total), "reasons": d.reasons})
                raise PolicyViolation("auto-pay blocked: " + "; ".join(d.reasons) +
                                      ". You can still pay manually with UPI (Flow 1).")
            # Log BEFORE paying so a crash mid-payment still counts against the caps.
            audit.append(pol.genesis, "autopay_attempt", {"id": cid, "site": c.adapter.name,
                                                          "amount": str(c.quote.total)})
            c.state = "paying"
            try:
                result: OrderResult = await c.adapter.pay_with_balance(c.session.page, c.quote)
            except Exception as e:
                c.state = "unknown"
                audit.append(pol.genesis, "autopay_error", {"id": cid, "error": type(e).__name__})
                raise CheckoutError("payment step failed; check the browser window and your store "
                                    "account before retrying") from e
            c.state = "paid" if result.order_id else "unknown"
            audit.append(pol.genesis, "autopay_result", {"id": cid, "order_id": result.order_id,
                                                         "status": result.status})
            return {"checkout_id": cid, "order_id": result.order_id, "status": result.status,
                    "paid_inr": str(result.total) if result.total is not None else None}

    async def status(self, cid: str) -> dict[str, Any]:
        c = self._get(cid)
        r = await c.adapter.order_status(c.session.page)
        if r.order_id and c.state == "awaiting_payment":
            c.state = "paid"
            _audit("manual_payment_confirmed", {"id": cid, "order_id": r.order_id})
        return {"checkout_id": cid, "state": c.state, "order_id": r.order_id, "status": r.status}

    async def cancel(self, cid: str) -> dict[str, Any]:
        c = self._get(cid)
        if c.state == "paying":
            raise CheckoutError("payment in progress; cannot cancel now")
        await self._close(cid)
        _audit("checkout_cancelled", {"id": cid})
        return {"checkout_id": cid, "state": "cancelled"}

    async def shutdown(self) -> None:
        for cid in list(self.checkouts):
            await self._close(cid)
        await self.browser.close()
