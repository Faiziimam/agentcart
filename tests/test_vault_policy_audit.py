import json
from decimal import Decimal

import pytest
from conftest import PASS

from agentcart import audit, paths, policy, vault
from agentcart.errors import PolicyViolation, TamperError, VaultError


def test_vault_roundtrip_and_wrong_pass(unlocked):
    assert vault.unlock(PASS).data["genesis"] == unlocked.data["genesis"]
    with pytest.raises(VaultError):
        vault.unlock("wrong passphrase!!")


def test_vault_file_is_private(unlocked):
    assert oct(paths.vault_file().stat().st_mode & 0o777) == "0o600"
    assert oct(paths.home().stat().st_mode & 0o777) == "0o700"


def test_vault_tamper_detected(unlocked):
    doc = json.loads(paths.vault_file().read_text())
    doc["public_key"] = vault.b64e(b"\x00" * 32)
    paths.vault_file().write_text(json.dumps(doc))
    with pytest.raises((VaultError, TamperError)):
        vault.unlock(PASS)


def test_weak_kdf_rejected(unlocked):
    doc = json.loads(paths.vault_file().read_text())
    doc["kdf"]["n"] = 2
    paths.vault_file().write_text(json.dumps(doc))
    with pytest.raises(TamperError):
        vault.unlock(PASS)


def test_short_passphrase(tmp_path):
    with pytest.raises(VaultError):
        vault.create("short")


@pytest.mark.parametrize("key,value", [
    ("upi_pin", "123456"), ("UPI PIN", "1234"), ("cvv", "123"), ("otp", "000000"),
    ("note", "4111 1111 1111 1111"),
])
def test_refuses_forbidden_secrets(unlocked, key, value):
    with pytest.raises(VaultError):
        vault.put_secret(PASS, key, value)


def test_allows_postal_pincode(unlocked):
    vault.put_secret(PASS, "pincode", "400053")


def test_policy_default_and_tamper(unlocked):
    pol = policy.load()
    assert pol.autopay_enabled is False
    doc = json.loads(paths.policy_file().read_text())
    doc["policy"]["autopay_enabled"] = True
    doc["policy"]["per_order_cap_inr"] = "9999"
    paths.policy_file().write_text(json.dumps(doc))
    with pytest.raises(TamperError):
        policy.load()


def test_missing_policy_is_restrictive(unlocked):
    paths.policy_file().unlink()
    assert policy.load().autopay_enabled is False


def _enabled(unlocked, **kw):
    p = policy.Policy(autopay_enabled=True, allowed_sites=["shop.in"], per_order_cap_inr="500",
                      monthly_cap_inr="1000", max_orders_per_day=2, **kw)
    policy.save(p, unlocked)
    return policy.load()


def test_evaluate_rules(unlocked):
    d = policy.evaluate_autopay(policy.load(), "shop.in", Decimal("100"), "INR", Decimal(0))
    assert not d.allowed and any("disabled" in r for r in d.reasons)
    pol = _enabled(unlocked)
    assert policy.evaluate_autopay(pol, "www.shop.in", Decimal("448"), "INR", Decimal("448")).allowed
    bad = [
        ("evil.in", Decimal("10"), "INR", Decimal(0), "allowlist"),
        ("shop.in", Decimal("501"), "INR", Decimal(0), "per-order"),
        ("shop.in", Decimal("10"), "USD", Decimal(0), "INR"),
        ("shop.in", Decimal("0"), "INR", Decimal(0), "verified"),
        ("shop.in", Decimal("450"), "INR", Decimal("400"), "higher than the quoted"),
    ]
    for host, amt, cur, quoted, why in bad:
        d = policy.evaluate_autopay(pol, host, amt, cur, quoted)
        assert not d.allowed and any(why in r for r in d.reasons), (host, d.reasons)


def test_monthly_and_daily_caps(unlocked):
    pol = _enabled(unlocked)
    g = pol.genesis
    audit.append(g, "autopay_attempt", {"amount": "500"})
    audit.append(g, "autopay_attempt", {"amount": "400"})
    d = policy.evaluate_autopay(pol, "shop.in", Decimal("200"), "INR", Decimal(0))
    assert not d.allowed
    assert any("monthly" in r for r in d.reasons) and any("daily" in r for r in d.reasons)


def test_hard_caps_cannot_be_configured(unlocked):
    with pytest.raises(PolicyViolation):
        policy.save(policy.Policy(per_order_cap_inr="1000000"), unlocked)


def test_audit_chain_tamper(unlocked):
    g = unlocked.data["genesis"]
    for i in range(3):
        audit.append(g, "autopay_attempt", {"amount": str(i)})
    assert len(audit.read_verified(g)) == 3
    lines = paths.audit_file().read_text().splitlines()
    edited = json.loads(lines[1])
    edited["data"]["amount"] = "0"
    paths.audit_file().write_text("\n".join([lines[0], json.dumps(edited), lines[2]]) + "\n")
    with pytest.raises(TamperError):
        audit.read_verified(g)
    paths.audit_file().write_text("\n".join([lines[0], lines[2]]) + "\n")  # deletion
    with pytest.raises(TamperError):
        audit.read_verified(g)
    with pytest.raises(TamperError):  # tampered log => auto-pay fails closed
        policy.evaluate_autopay(policy.load(), "x", Decimal(1), "INR", Decimal(0))
