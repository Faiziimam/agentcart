"""Append-only, hash-chained audit log of every money-related action.

Each line is JSON: {"seq", "ts", "event", "data", "prev", "hash"} where
hash = sha256(prev || canonical_json(seq, ts, event, data)). The first entry's prev is the vault's
random genesis value. Any edit, reordering or deletion in the middle breaks the chain; on a broken
chain auto-pay fails closed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import paths
from .errors import TamperError


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock = path.with_suffix(".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | paths.O_BINARY, 0o600)
    try:
        if sys.platform == "win32":  # pragma: no cover
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            yield  # released on close
    finally:
        os.close(fd)


def read_verified(genesis: str, path: Path | None = None) -> list[dict[str, Any]]:
    path = path or paths.audit_file()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    prev = genesis
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError as err:
            raise TamperError(f"audit log line {n} is not valid JSON") from err
        body = {k: e.get(k) for k in ("seq", "ts", "event", "data")}
        expect = hashlib.sha256(prev.encode() + _canon(body)).hexdigest()
        if e.get("prev") != prev or e.get("hash") != expect or e.get("seq") != len(entries):
            raise TamperError(f"audit log chain broken at line {n}")
        entries.append(e)
        prev = e["hash"]
    return entries


def append(genesis: str, event: str, data: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    path = path or paths.audit_file()
    with _locked(path):
        entries = read_verified(genesis, path)
        prev = entries[-1]["hash"] if entries else genesis
        body = {"seq": len(entries), "ts": datetime.now(UTC).isoformat(), "event": event,
                "data": json.loads(_canon(data))}
        entry = {**body, "prev": prev, "hash": hashlib.sha256(prev.encode() + _canon(body)).hexdigest()}
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | paths.O_BINARY, 0o600)
        try:
            os.write(fd, _canon(entry) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        return entry


def autopay_spend(entries: list[dict[str, Any]], now: datetime | None = None) -> tuple[Decimal, int]:
    """Return (INR spent via auto-pay this calendar month, auto-pay orders in the last 24h)."""
    now = now or datetime.now(UTC)
    month_total, day_count = Decimal("0"), 0
    for e in entries:
        if e["event"] not in ("autopay_attempt",):
            continue
        ts = datetime.fromisoformat(e["ts"])
        amount = Decimal(str(e["data"]["amount"]))
        if (ts.year, ts.month) == (now.year, now.month):
            month_total += amount
        if (now - ts).total_seconds() < 86400:
            day_count += 1
    return month_total, day_count
