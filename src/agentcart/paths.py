"""On-device storage locations. Nothing agentcart stores ever leaves this machine."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def home() -> Path:
    override = os.environ.get("AGENTCART_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "agentcart"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "agentcart"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "agentcart"
    return ensure_private_dir(base)


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
    return path


def write_private(path: Path, data: bytes) -> None:
    """Atomically write a file readable only by the current user."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def vault_file() -> Path:
    return home() / "vault.json"


def policy_file() -> Path:
    return home() / "policy.json"


def audit_file() -> Path:
    return home() / "audit.log"


def profiles_dir() -> Path:
    return ensure_private_dir(home() / "browser-profiles")
