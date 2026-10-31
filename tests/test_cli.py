import pytest
from conftest import PASS

from agentcart import cli, policy


def test_security_commands_require_tty(capsys):
    # pytest's stdin is not a TTY, exactly like an agent's shell.
    for argv in (["init"], ["policy", "set", "--autopay", "on"], ["passwd"], ["login", "demo"]):
        assert cli.main(argv) == 1
        assert "interactive terminal" in capsys.readouterr().err


def test_policy_set_and_kill(unlocked, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_require_tty", lambda action: None)
    monkeypatch.setattr(cli, "_passphrase", lambda prompt="": PASS)
    assert cli.main(["policy", "set", "--autopay", "on", "--allow", "www.Shop.in",
                     "--per-order", "500", "--monthly", "2000", "--orders-per-day", "3"]) == 0
    pol = policy.load()
    assert pol.autopay_enabled and pol.allowed_sites == ["shop.in"] and pol.version == 1
    assert cli.main(["log"]) == 0
    assert "policy_changed" in capsys.readouterr().out
    assert cli.main(["kill"]) == 0
    assert policy.load().autopay_enabled is False


def test_policy_set_wrong_passphrase(unlocked, monkeypatch):
    monkeypatch.setattr(cli, "_require_tty", lambda action: None)
    monkeypatch.setattr(cli, "_passphrase", lambda prompt="": "not the passphrase")
    assert cli.main(["policy", "set", "--autopay", "on"]) == 1
    assert policy.load().autopay_enabled is False


@pytest.mark.parametrize("args", [["--per-order", "abc"], ["--per-order", "999999"],
                                  ["--orders-per-day", "99"], ["--allow", "a.com/x"]])
def test_policy_set_rejects_bad_values(unlocked, monkeypatch, args):
    monkeypatch.setattr(cli, "_require_tty", lambda action: None)
    monkeypatch.setattr(cli, "_passphrase", lambda prompt="": PASS)
    assert cli.main(["policy", "set", *args]) == 1
