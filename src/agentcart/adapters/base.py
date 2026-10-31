"""Site adapter contract. One adapter per store; see docs/ADAPTERS.md."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Page

from ..errors import CheckoutError


@dataclass
class LineItem:
    name: str
    quantity: int
    unit_price: Decimal


@dataclass
class Quote:
    items: list[LineItem]
    subtotal: Decimal
    shipping: Decimal
    total: Decimal
    currency: str
    deliver_to: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("subtotal", "shipping", "total"):
            d[k] = str(d[k])
        for it in d["items"]:
            it["unit_price"] = str(it["unit_price"])
        return d


@dataclass
class UpiIntent:
    payee_vpa: str
    payee_name: str | None
    amount: Decimal | None
    currency: str | None
    raw: str


def parse_upi_intent(uri: str) -> UpiIntent:
    parts = urlsplit(uri)
    if parts.scheme != "upi" or parts.netloc.lower() != "pay":
        raise CheckoutError("payment link is not a upi://pay intent")
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    if "pa" not in q or "@" not in q["pa"]:
        raise CheckoutError("UPI intent has no valid payee address")
    amt = Decimal(q["am"]).quantize(Decimal("0.01")) if q.get("am") else None
    return UpiIntent(q["pa"], q.get("pn"), amt, q.get("cu"), uri)


@dataclass
class OrderResult:
    order_id: str | None
    status: str
    total: Decimal | None


class SiteAdapter(ABC):
    #: short id used for profiles and CLI ('agentcart login <name>')
    name: str
    #: domains this adapter handles (bare, no www.)
    domains: tuple[str, ...]
    #: True only if the adapter implements pay_with_balance against a pre-funded store balance
    supports_autopay: bool = False

    @abstractmethod
    async def prepare_checkout(self, page: Page, product_url: str, quantity: int) -> Quote:
        """Add the product to cart and stop on the payment-method page. Never pays."""

    async def select_upi(self, page: Page, quote: Quote) -> UpiIntent | None:
        """Choose UPI as the payment method and return the upi:// intent if the page exposes one.

        Returning None is fine: the user pays in the visible browser window instead.
        """
        return None

    async def pay_with_balance(self, page: Page, quote: Quote) -> OrderResult:
        raise CheckoutError(f"{self.name} does not support auto-pay")

    async def order_status(self, page: Page) -> OrderResult:
        raise CheckoutError(f"{self.name} cannot report order status yet")

    async def login_url(self) -> str:
        return f"https://{self.domains[0]}/"
