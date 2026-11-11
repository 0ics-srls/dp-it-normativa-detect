"""Puts the repo root (main.py, procclass.py, v11.py) and src/ on sys.path, forbids any network access and drives main in-process."""
from __future__ import annotations

import io
import json
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
for entry in (ROOT, ROOT / "src", ROOT / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import main  # noqa: E402 - needs the sys.path set up above

MANIFEST = ROOT / "manifest.json"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: hermetic unit test")
    config.addinivalue_line("markers", "integration: needs a network or Docker")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Detect never touches the network: any socket connection fails the test."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        msg = "detect must not use the network"
        raise AssertionError(msg)

    monkeypatch.setattr(socket.socket, "connect", refuse)


@dataclass(frozen=True)
class Run:
    """Outcome of one in-process invocation: exit code, raw stdout and the parsed JSON lines."""

    returncode: int
    stdout: str
    lines: list[dict[str, Any]]

    @property
    def result(self) -> dict[str, Any]:
        results = [m for m in self.lines if m.get("type") == "result"]
        assert results, f"no result line in {self.stdout!r}"
        data: dict[str, Any] = results[-1]["data"]
        return data

    @property
    def error(self) -> dict[str, Any]:
        errors = [m for m in self.lines if m.get("type") == "error"]
        assert errors, f"no error line in {self.stdout!r}"
        return errors[-1]


@pytest.fixture
def invoke() -> Callable[..., Run]:
    def _invoke(action: str, **params: Any) -> Run:
        request = {"_action": action, "_meta": {"executionId": "e1"}, **params}
        return invoke_raw(json.dumps(request) + "\n")

    return _invoke


def invoke_raw(text: str) -> Run:
    out = io.StringIO()
    code = main.main(io.StringIO(text), out)
    lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return Run(code, out.getvalue(), lines)
