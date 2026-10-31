"""agentcart command line. Anything that loosens security requires a real TTY + the passphrase,
so an AI agent driving a non-interactive shell cannot do it."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import os
import sys
from dataclasses import asdict
from decimal import Decimal, InvalidOperation

from . import __version__, audit, paths, policy, vault
from .errors import AgentCartError


def _require_tty(action: str) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise AgentCartError(f"'{action}' must be run by you in an interactive terminal, not by an agent")


def _passphrase(prompt: str = "Vault passphrase: ") -> str:
    return getpass.getpass(prompt)


def cmd_init(_: argparse.Namespace) -> None:
    _require_tty("init")
    print("Creating your on-device vault. The passphrase never leaves this machine and cannot be "
          "recovered.")
    p1 = _passphrase("New passphrase (12+ chars): ")
    if _passphrase("Repeat passphrase: ") != p1:
        raise AgentCartError("passphrases do not match")
    vault.create(p1)
    unlocked = vault.unlock(p1)
    policy.save(policy.Policy(), unlocked)
    audit.append(unlocked.data["genesis"], "vault_created", {"version": __version__})
    print(f"Vault created at {paths.home()}\nAuto-pay is OFF. Flow 1 (approve on phone) works now.")


def _money(v: str) -> str:
    try:
        d = Decimal(v)
    except InvalidOperation as e:
        raise AgentCartError(f"'{v}' is not an amount") from e
    return str(d.quantize(Decimal("0.01")))


def cmd_policy_show(_: argparse.Namespace) -> None:
    print(json.dumps(policy.summary(policy.load()), indent=2, ensure_ascii=False))


def cmd_policy_set(a: argparse.Namespace) -> None:
    _require_tty("policy set")
    pol = policy.load()
    before = asdict(pol)
    if a.autopay:
        pol.autopay_enabled = a.autopay == "on"
    if a.per_order is not None:
        pol.per_order_cap_inr = _money(a.per_order)
    if a.monthly is not None:
        pol.monthly_cap_inr = _money(a.monthly)
    if a.orders_per_day is not None:
        pol.max_orders_per_day = a.orders_per_day
    if a.price_tolerance is not None:
        pol.max_price_increase_pct = _money(a.price_tolerance)
    for s in a.allow or []:
        s = s.lower().removeprefix("www.")
        if s not in pol.allowed_sites:
            pol.allowed_sites.append(s)
    for s in a.disallow or []:
        pol.allowed_sites = [x for x in pol.allowed_sites if x != s.lower().removeprefix("www.")]
    pol.validate()
    changes = {k: (before[k], v) for k, v in asdict(pol).items() if before[k] != v}
    if not changes:
        print("Nothing to change.")
        return
    for k, (old, new) in changes.items():
        print(f"  {k}: {old} -> {new}")
    if pol.autopay_enabled and not before["autopay_enabled"]:
        print("\nWARNING: auto-pay lets the agent spend from your store balance without asking you.\n"
              "Only pre-fund small amounts. Caps are enforced in code, not by the AI.")
    unlocked = vault.unlock(_passphrase())
    pol.version += 1
    policy.save(pol, unlocked)
    audit.append(unlocked.data["genesis"], "policy_changed",
                 {k: v[1] for k, v in changes.items()})
    print("Policy signed and saved.")


def cmd_kill(_: argparse.Namespace) -> None:
    """Emergency off switch. Does not need a TTY: anyone (even an agent) may make things SAFER."""
    pol_file = paths.policy_file()
    if pol_file.exists():
        pol_file.unlink()  # missing policy == restrictive default (auto-pay off)
    print("Auto-pay disabled. Run 'agentcart policy set' to configure it again.")


def cmd_log(a: argparse.Namespace) -> None:
    pol = policy.load()
    if not pol.genesis:
        unlocked = vault.unlock(_passphrase())
        genesis = unlocked.data["genesis"]
    else:
        genesis = pol.genesis
    entries = audit.read_verified(genesis)
    for e in entries[-a.tail:]:
        print(f"{e['seq']:>5} {e['ts']} {e['event']:<24} {json.dumps(e['data'], ensure_ascii=False)}")
    print(f"\nchain OK ({len(entries)} entries)")


def cmd_login(a: argparse.Namespace) -> None:
    _require_tty("login")
    from .adapters import by_name
    from .browser import BrowserManager

    adapter = by_name(a.site)

    async def go() -> None:
        bm = BrowserManager()
        s = await bm.open(adapter.name, adapter.domains, headless=False)
        await s.page.goto(await adapter.login_url())
        print("Log in (and save your delivery address) in the browser window, then press Enter here.")
        await asyncio.get_running_loop().run_in_executor(None, input)
        await s.context.close()
        await bm.close()

    asyncio.run(go())
    print(f"Session saved in your local profile for {adapter.name}. Your password was not stored by "
          "agentcart.")


def cmd_passwd(_: argparse.Namespace) -> None:
    _require_tty("passwd")
    vault.change_passphrase(_passphrase("Current passphrase: "), _passphrase("New passphrase: "))
    print("Passphrase changed.")


def cmd_doctor(_: argparse.Namespace) -> None:
    print(f"agentcart {__version__}\nhome: {paths.home()}")
    print(f"vault: {'present' if vault.exists() else 'not created (run agentcart init)'}")
    try:
        print("policy:", json.dumps(policy.summary(policy.load()), ensure_ascii=False))
    except AgentCartError as e:
        print("policy: ERROR", e)
    if os.name == "posix":
        mode = oct(paths.home().stat().st_mode & 0o777)
        print(f"home permissions: {mode} {'OK' if mode == '0o700' else 'TOO OPEN'}")

    async def browser_check() -> str:
        from playwright.async_api import async_playwright

        from .browser import _executable

        async with async_playwright() as pw:
            b = await pw.chromium.launch(headless=True, executable_path=_executable())
            v = b.version
            await b.close()
            return v

    try:
        print("browser: chromium", asyncio.run(browser_check()), "OK")
    except Exception as e:
        print("browser: NOT READY -", str(e).splitlines()[0][:200])
        print("         fix: uvx playwright install chromium")


def cmd_serve(_: argparse.Namespace) -> None:
    from .server import run

    run()


def cmd_demo_shop(a: argparse.Namespace) -> None:
    from .demo_shop import serve

    srv, _ = serve(a.port)
    print(f"Demo shop on http://127.0.0.1:{a.port}/product/atomic-habits  (Ctrl+C to stop)\n"
          "Start the MCP server with AGENTCART_DEV_LOOPBACK=1 to use it.")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentcart", description="Security-first agentic checkout")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the MCP server (stdio)").set_defaults(fn=cmd_serve)
    sub.add_parser("init", help="create the on-device vault").set_defaults(fn=cmd_init)
    sub.add_parser("passwd", help="change vault passphrase").set_defaults(fn=cmd_passwd)
    sub.add_parser("doctor", help="check installation").set_defaults(fn=cmd_doctor)
    sub.add_parser("kill", help="immediately disable auto-pay").set_defaults(fn=cmd_kill)
    lg = sub.add_parser("log", help="show + verify the audit log")
    lg.add_argument("--tail", type=int, default=50)
    lg.set_defaults(fn=cmd_log)
    li = sub.add_parser("login", help="log in to a store in a local browser profile")
    li.add_argument("site")
    li.set_defaults(fn=cmd_login)
    ds = sub.add_parser("demo-shop", help="run the local demo store")
    ds.add_argument("--port", type=int, default=8765)
    ds.set_defaults(fn=cmd_demo_shop)
    po = sub.add_parser("policy", help="view or change the auto-pay policy")
    psub = po.add_subparsers(dest="pcmd", required=True)
    psub.add_parser("show").set_defaults(fn=cmd_policy_show)
    ps = psub.add_parser("set")
    ps.add_argument("--autopay", choices=["on", "off"])
    ps.add_argument("--per-order", help="max INR per order")
    ps.add_argument("--monthly", help="max INR per calendar month")
    ps.add_argument("--orders-per-day", type=int)
    ps.add_argument("--price-tolerance", help="allowed %% above listed price (0-10)")
    ps.add_argument("--allow", action="append", metavar="DOMAIN")
    ps.add_argument("--disallow", action="append", metavar="DOMAIN")
    ps.set_defaults(fn=cmd_policy_set)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except AgentCartError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
