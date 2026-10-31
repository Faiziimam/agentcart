# Security policy

agentcart handles money flows, so security reports get priority over everything else.

## Reporting a vulnerability
**Do not open a public issue.** Use GitHub's private vulnerability reporting
(*Security → Report a vulnerability*) on this repository. Include steps to reproduce and the impact.

- We acknowledge reports within 72 hours.
- Fixes for critical issues (unauthorized payment, policy bypass, SSRF, secret exposure) target
  release within 7 days.
- With your permission, we credit you in the advisory.

## In scope
- Any way an AI model, web page or MCP client can cause a payment the policy does not allow.
- Bypassing the TTY + passphrase requirement for policy changes.
- SSRF or navigation-guard bypasses.
- Vault or audit-log weaknesses.
- Leaking cookies, addresses or order data off the device.

## Out of scope
Attacks that need code execution as the same OS user (see
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md#residual-risks-stated-plainly)), and social engineering
of the user.

## Design rules contributors must keep
1. No tool exposed to the model may loosen security (policy, caps, allowlist, vault).
2. Never store or ask for a UPI PIN, MPIN, CVV, OTP or full card number.
3. Every money decision is deterministic code, never model output.
4. Fail closed on any integrity or parsing error.
5. No network listeners, and no telemetry.
