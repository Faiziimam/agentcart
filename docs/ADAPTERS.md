# Writing a store adapter

An adapter teaches agentcart how one store's checkout works. Start from the reference
implementation in `src/agentcart/adapters/demo.py`.

```python
class MyStoreAdapter(SiteAdapter):
    name = "mystore"                    # used by 'agentcart login mystore'
    domains = ("mystore.in",)           # top-level navigation is locked to these + payment gateways
    supports_autopay = False            # True only if you implement pay_with_balance

    async def prepare_checkout(self, page, product_url, quantity) -> Quote: ...
    async def select_upi(self, page, quote) -> UpiIntent | None: ...       # optional
    async def pay_with_balance(self, page, quote) -> OrderResult: ...      # optional, Flow 2
    async def order_status(self, page) -> OrderResult: ...
```

Then register it in `adapters/__init__.py`.

## Rules (PRs are reviewed against these)
1. **`prepare_checkout` must never pay.** It stops on the payment-method page.
2. **Read every amount from the page, then re-check it.** Raise `CheckoutError` if the cart
   quantity, the cart total and the payment-page amount do not agree.
3. **Pass all scraped text through `clean_text` / `parse_money`.**
4. **Prefer stable selectors** (`data-testid`, ARIA roles, labels) over CSS classes.
5. **Never fill in passwords, OTPs or PINs.** The user logs in with `agentcart login`; if the
   session has expired, raise `CheckoutError("please run: agentcart login mystore")`.
6. **`pay_with_balance` may only use a pre-funded store balance or gift card**, never a saved card
   or UPI.
7. **Check the store's terms of service** allow automated checkout for personal use, and say so in
   your PR.
8. **Include a recorded HTML fixture test** (save the pages, redact personal data) so CI can run
   without network access.

## Good first adapters
Indian bookstores and marketplaces that publish schema.org `Product`/`Book` data. Open an issue
titled `adapter: <store>` before you start, so two people don't build the same one.
