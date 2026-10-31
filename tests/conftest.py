import socket
import threading

import pytest

from agentcart import vault
from agentcart.demo_shop import serve

PASS = "correct horse battery staple"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCART_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENTCART_HEADLESS", "1")
    monkeypatch.delenv("AGENTCART_DEV_LOOPBACK", raising=False)
    monkeypatch.setattr(vault, "DEFAULT_KDF", {"n": 2**14, "r": 8, "p": 1})
    return tmp_path / "home"


@pytest.fixture
def unlocked():
    vault.create(PASS)
    from agentcart import policy

    v = vault.unlock(PASS)
    policy.save(policy.Policy(), v)
    return v


@pytest.fixture
def shop(monkeypatch):
    monkeypatch.setenv("AGENTCART_DEV_LOOPBACK", "1")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv, state = serve(port)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", state
    srv.shutdown()
