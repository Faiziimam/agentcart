# Threat model

## Assets
1. Money: the UPI account (Flow 1) and the pre-funded store balance (Flow 2).
2. Store sessions: cookies in the local browser profiles.
3. The vault passphrase and the policy signing key.
4. Your privacy: order history and delivery address.

## Trust boundaries
| Actor | Trust |
|---|---|
| You, at an interactive terminal | trusted |
| Your phone's UPI app | trusted, and it is the final approval in Flow 1 |
| The AI model / MCP client | **untrusted for money decisions**: it can be prompt-injected |
| Web pages, including the store itself | untrusted input |
| Other processes running as your OS user | out of scope (see residual risks) |

## Threats and controls

| # | Threat | Control |
|---|---|---|
| T1 | A prompt injection in a product page makes the agent buy things | Flow 1 needs your UPI PIN on your phone. Flow 2 is off by default, and its caps, allowlist and price check are enforced in code. Page text is labelled untrusted and stripped of invisible characters. |
| T2 | The agent tries to raise its own limits | No MCP tool writes the policy. `policy set` needs a TTY plus the passphrase. The policy is Ed25519-signed and verified on every auto-pay. |
| T3 | SSRF: the agent fetches `169.254.169.254`, the LAN or localhost | `netsafe`: https only, all resolved IPs must be global, the connection is pinned to the validated IP, each redirect is re-checked. |
| T4 | DNS rebinding between the check and the connect | IP pinning with TLS SNI verification against the hostname. |
| T5 | A malicious page redirects the browser to a phishing "payment" page | Top-level navigation is allowlisted to the adapter's domains plus a fixed list of payment gateways. The UPI intent's amount is checked against the cart total. |
| T6 | The price or quantity changes between quote and payment | The adapter re-reads the amount on the payment page. Auto-pay compares it with the listed price within your tolerance (default 0%). |
| T7 | Double payment through retries or races | A per-checkout lock and state machine; a second pay call is refused. |
| T8 | Someone edits the audit log to reset the monthly spend | The hash chain is seeded by the vault's random genesis. A broken chain disables auto-pay. Attempts are logged before paying. |
| T9 | Brute-forcing a stolen vault offline | scrypt N=2^17, r=8, p=1, a 12+ character passphrase, and weak KDF parameters in the file are rejected. |
| T10 | Collecting PINs, CVVs or OTPs | The vault refuses these key names and Luhn-valid card numbers. The server instructions tell the model never to ask for them. |
| T11 | Supply-chain compromise | Few dependencies, CI runs `pip-audit` and CodeQL, Dependabot, PyPI trusted publishing with build attestations. |

## Residual risks (stated plainly)
- **Same-user malware or a shell-capable agent.** Code running as your OS user can read browser
  cookies, delete `policy.json`, or replace the vault together with the policy. Mitigations: keep
  Flow 2 balances small. In Claude Code, deny agent access to the agentcart home folder:
  ```json
  { "permissions": { "deny": ["Read(~/.config/agentcart/**)", "Edit(~/.config/agentcart/**)",
                               "Bash(agentcart policy set:*)", "Bash(agentcart init:*)"] } }
  ```
- **A compromised store.** If the merchant site itself is malicious, it can show wrong totals.
  Flow 1's final check is you reading the payee name and amount in your UPI app.
- **Truncating the audit log tail.** Deleting the *last* entries is not detectable without an
  external anchor. The store balance remains the hard limit.
