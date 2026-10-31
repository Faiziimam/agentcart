"""Spending policy for Flow 2 (auto-pay). Signed with the vault key; verified on every use.

Defaults are maximally restrictive: auto-pay OFF, no allowed sites, zero caps.
Only `agentcart policy set` (interactive TTY + passphrase) can produce a valid policy.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from . import audit, paths, vault
from .errors import PolicyViolation, TamperError
from .netsafe import host_matches

HARD_MAX_PER_ORDER = Decimal("10000")   # code-level ceiling, regardless of user config
HARD_MAX_PER_MONTH = Decimal("25000")
HARD_MAX_ORDERS_PER_DAY = 10


@dataclass
class Policy:
    autopay_enabled: bool = False
    allowed_sites: list[str] = field(default_factory=list)
    per_order_cap_inr: str = "0"
    monthly_cap_inr: str = "0"
    max_orders_per_day: int = 0
    max_price_increase_pct: str = "0"
    genesis: str = ""
    version: int = 0

    def canonical(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()

    def validate(self) -> None:
        if Decimal(self.per_order_cap_inr) < 0 or Decimal(self.monthly_cap_inr) < 0:
            raise PolicyViolation("caps cannot be negative")
        if Decimal(self.per_order_cap_inr) > HARD_MAX_PER_ORDER:
            raise PolicyViolation(f"per-order cap cannot exceed ₹{HARD_MAX_PER_ORDER}")
        if Decimal(self.monthly_cap_inr) > HARD_MAX_PER_MONTH:
            raise PolicyViolation(f"monthly cap cannot exceed ₹{HARD_MAX_PER_MONTH}")
        if not 0 <= self.max_orders_per_day <= HARD_MAX_ORDERS_PER_DAY:
            raise PolicyViolation(f"orders/day must be 0..{HARD_MAX_ORDERS_PER_DAY}")
        if not Decimal("0") <= Decimal(self.max_price_increase_pct) <= Decimal("10"):
            raise PolicyViolation("allowed price increase must be 0..10%")
        for s in self.allowed_sites:
            if not s or "/" in s or " " in s:
                raise PolicyViolation(f"invalid site '{s}' (use a bare domain like example.com)")


def save(policy: Policy, unlocked: vault.UnlockedVault) -> None:
    policy.validate()
    policy.genesis = unlocked.data["genesis"]
    doc = {"policy": asdict(policy), "signature": vault.sign(unlocked.signing_key, policy.canonical())}
    paths.write_private(paths.policy_file(), json.dumps(doc, indent=2).encode())


def load() -> Policy:
    """Load and verify. Missing policy => restrictive default. Bad signature => TamperError."""
    f = paths.policy_file()
    if not f.exists():
        return Policy()
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
        pol = Policy(**doc["policy"])
    except (ValueError, KeyError, TypeError) as e:
        raise TamperError("policy file is corrupted") from e
    vault.verify(pol.canonical(), doc.get("signature", ""))
    pol.validate()
    return pol


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: list[str]
    month_spent: Decimal
    orders_last_24h: int


def evaluate_autopay(pol: Policy, host: str, amount: Decimal, currency: str,
                     quoted: Decimal) -> Decision:
    """Pure, deterministic check. Every rule must pass; the model cannot override any of them."""
    entries = audit.read_verified(pol.genesis) if pol.genesis else []
    spent, today = audit.autopay_spend(entries)
    r: list[str] = []
    if not pol.autopay_enabled:
        r.append("auto-pay is disabled (enable it with 'agentcart policy set --autopay on')")
    if not pol.genesis:
        r.append("policy was never signed")
    if currency != "INR":
        r.append(f"auto-pay only supports INR, got {currency or 'unknown'}")
    if not any(host_matches(host, s) for s in pol.allowed_sites):
        r.append(f"site '{host}' is not in your auto-pay allowlist")
    if amount <= 0:
        r.append("order total could not be verified")
    cap = min(Decimal(pol.per_order_cap_inr), HARD_MAX_PER_ORDER)
    if amount > cap:
        r.append(f"total ₹{amount} exceeds per-order cap ₹{cap}")
    mcap = min(Decimal(pol.monthly_cap_inr), HARD_MAX_PER_MONTH)
    if spent + amount > mcap:
        r.append(f"monthly cap ₹{mcap} would be exceeded (already spent ₹{spent})")
    if today >= min(pol.max_orders_per_day, HARD_MAX_ORDERS_PER_DAY):
        r.append("daily auto-pay order limit reached")
    if quoted > 0:
        limit = quoted * (1 + Decimal(pol.max_price_increase_pct) / 100)
        if amount > limit:
            r.append(f"checkout total ₹{amount} is higher than the quoted ₹{quoted}")
    return Decision(not r, r, spent, today)


def summary(pol: Policy) -> dict[str, Any]:
    spent, today = (audit.autopay_spend(audit.read_verified(pol.genesis)) if pol.genesis
                    else (Decimal(0), 0))
    return {
        "autopay_enabled": pol.autopay_enabled,
        "allowed_sites": pol.allowed_sites,
        "per_order_cap_inr": pol.per_order_cap_inr,
        "monthly_cap_inr": pol.monthly_cap_inr,
        "spent_this_month_inr": str(spent),
        "max_orders_per_day": pol.max_orders_per_day,
        "orders_last_24h": today,
        "note": "Only the user can change this, from an interactive terminal with the vault passphrase.",
    }
