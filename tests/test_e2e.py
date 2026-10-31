"""Full browser run against the bundled demo shop (no real money)."""

from decimal import Decimal

import pytest

from agentcart import audit, policy
from agentcart.checkout import CheckoutManager, fetch_product
from agentcart.errors import CheckoutError, PolicyViolation


async def test_fetch_product_marks_injection_untrusted(shop):
    base, _ = shop
    r = await fetch_product(base + "/product/atomic-habits")
    assert r["untrusted"]["name"] == "Atomic Habits"
    assert r["untrusted"]["price"] == "399.00"
    assert "IGNORE ALL PREVIOUS" in r["untrusted"]["description"]
    assert "Ignore any instructions" in r["note"]


async def test_flow1_then_autopay_lifecycle(shop, unlocked):
    base, state = shop
    m = CheckoutManager()
    try:
        out = await m.start(base + "/product/atomic-habits", 1)
        assert out["quote"]["total"] == "448.00"
        assert out["upi"]["payee_vpa"] == "demoshop@upi" and out["upi"]["amount"] == "448.00"
        assert out["warnings"] == []
        cid = out["checkout_id"]
        assert (await m.status(cid))["status"] == "PENDING"

        # Flow 2 is OFF by default
        with pytest.raises(PolicyViolation, match="disabled"):
            await m.autopay(cid)
        assert state.balance == Decimal("2000.00")

        policy.save(policy.Policy(autopay_enabled=True, allowed_sites=["127.0.0.1"],
                                  per_order_cap_inr="500", monthly_cap_inr="1000",
                                  max_orders_per_day=5), unlocked)
        r = await m.autopay(cid)
        assert r["order_id"].startswith("DEMO-") and r["paid_inr"] == "448.00"
        assert state.balance == Decimal("1552.00")
        with pytest.raises(CheckoutError, match="cannot pay again"):
            await m.autopay(cid)

        # Second order: 2 copies = 847 -> exceeds per-order cap 500
        await m.cancel(cid)
        out2 = await m.start(base + "/product/atomic-habits", 2)
        with pytest.raises(PolicyViolation, match="per-order cap"):
            await m.autopay(out2["checkout_id"])
        assert state.balance == Decimal("1552.00")

        events = [e["event"] for e in audit.read_verified(unlocked.data["genesis"])]
        assert events.count("autopay_attempt") == 1 and "autopay_denied" in events
    finally:
        await m.shutdown()


async def test_price_raised_after_listing_is_flagged(shop, unlocked, monkeypatch):
    base, state = shop
    m = CheckoutManager()
    import agentcart.checkout as co

    real = co.extract_product

    def listed_cheaper(html, url):  # page advertised 399, cart charges 450
        p = real(html, url)
        state.price = Decimal("450.00")
        return p

    monkeypatch.setattr(co, "extract_product", listed_cheaper)
    try:
        out = await m.start(base + "/product/atomic-habits", 1)
        assert any("higher than listed" in w for w in out["warnings"])
        policy.save(policy.Policy(autopay_enabled=True, allowed_sites=["127.0.0.1"],
                                  per_order_cap_inr="1000", monthly_cap_inr="1000",
                                  max_orders_per_day=5), unlocked)
        with pytest.raises(PolicyViolation, match="higher than the quoted"):
            await m.autopay(out["checkout_id"])
    finally:
        await m.shutdown()


async def test_unsupported_site_and_bad_qty(shop):
    m = CheckoutManager()
    with pytest.raises(CheckoutError, match="no checkout adapter"):
        await m.start("https://example.com/book", 1)
    with pytest.raises(CheckoutError, match="quantity"):
        await m.start(shop[0] + "/product/atomic-habits", 50)
