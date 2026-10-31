# Changelog

## 0.1.0 (unreleased)
- MCP server (stdio) with get_product, start_checkout, autopay_checkout, checkout_status, cancel_checkout,
  list_supported_sites, get_spending_policy
- Flow 1: prepare checkout, UPI QR, approve on phone
- Flow 2: opt-in auto-pay from store balance with a signed policy and hard caps
- Encrypted on-device vault, hash-chained audit log, SSRF-hardened fetching, navigation guard
- Demo shop + reference adapter
