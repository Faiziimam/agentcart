"""Adapter for the bundled demo shop (loopback only). Also the reference implementation."""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import urlsplit

from playwright.async_api import Page

from ..errors import CheckoutError
from ..sanitize import clean_text, parse_money
from .base import LineItem, OrderResult, Quote, SiteAdapter, UpiIntent, parse_upi_intent


async def _money(page: Page, testid: str) -> Decimal:
    v = parse_money(await page.get_by_test_id(testid).inner_text())
    if v is None:
        raise CheckoutError(f"could not read '{testid}' amount from the page")
    return v


class DemoShopAdapter(SiteAdapter):
    name = "demo"
    domains = ("127.0.0.1", "localhost")
    supports_autopay = True

    async def prepare_checkout(self, page: Page, product_url: str, quantity: int) -> Quote:
        await page.goto(product_url)
        await page.get_by_test_id("qty").fill(str(quantity))
        await page.get_by_test_id("add-to-cart").click()
        await page.wait_for_url("**/checkout")
        qty = int(await page.get_by_test_id("item-qty").inner_text())
        if qty != quantity:
            raise CheckoutError(f"cart has quantity {qty}, expected {quantity}")
        quote = Quote(
            items=[LineItem(clean_text(await page.get_by_test_id("item-name").inner_text(), 200) or "?",
                            qty, await _money(page, "item-price"))],
            subtotal=await _money(page, "subtotal"),
            shipping=await _money(page, "shipping"),
            total=await _money(page, "total"),
            currency="INR",
            deliver_to=clean_text(await page.get_by_test_id("address").inner_text(), 200),
        )
        await page.get_by_test_id("to-payment").click()
        await page.wait_for_url("**/payment")
        due = await _money(page, "due")
        if due != quote.total:
            raise CheckoutError(f"payment page amount ₹{due} differs from cart total ₹{quote.total}")
        return quote

    async def select_upi(self, page: Page, quote: Quote) -> UpiIntent | None:
        await page.get_by_test_id("method-upi").check()
        href = await page.get_by_test_id("upi-intent").get_attribute("href")
        return parse_upi_intent(href) if href else None

    async def pay_with_balance(self, page: Page, quote: Quote) -> OrderResult:
        if await _money(page, "due") != quote.total:
            raise CheckoutError("amount changed before payment; aborted")
        await page.get_by_test_id("pay-balance").click()
        await page.wait_for_load_state()
        return await self.order_status(page)

    async def order_status(self, page: Page) -> OrderResult:
        if "/order/" not in urlsplit(page.url).path:
            if await page.get_by_test_id("error").count():
                return OrderResult(None, "FAILED: " + (await page.get_by_test_id("error").inner_text()),
                                   None)
            return OrderResult(None, "PENDING", None)
        return OrderResult(
            clean_text(await page.get_by_test_id("order-id").inner_text(), 40),
            clean_text(await page.get_by_test_id("order-status").inner_text(), 40) or "UNKNOWN",
            await _money(page, "order-total"),
        )
