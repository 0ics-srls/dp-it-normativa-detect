"""Proc-protocol v1.1 adapter: the three actions (spec / check / run), the three error codes and the result shapes.

Generic on purpose (no letaky knowledge); modelled on the detect classes' adapter (letak-worker-*/src/v11.py).
Metrics are flat numbers (the 1.1 run-result schema allows only numbers as metric values).
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

PROTOCOL = "1.1"
ACTIONS = ("spec", "check", "run")


class ProcErrorCode(StrEnum):
    """The only error codes a v1.1 class may report."""

    CONFIG_ERROR = "config_error"
    TRANSIENT_ERROR = "transient_error"
    SYSTEM_ERROR = "system_error"


def spec_result(config_schema: dict[str, Any], output_schema: dict[str, Any]) -> dict[str, Any]:
    """`data` of the result line of the `spec` action."""
    return {"configSchema": config_schema, "outputSchema": output_schema, "protocol": PROTOCOL}


def run_result(
    metrics: dict[str, float],
    checks: list[dict[str, Any]],
    items: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`data` of the result line of the `run` action. `items` and `state` are left out when None."""
    data: dict[str, Any] = {"metrics": metrics, "checks": checks}
    if items is not None:
        data["items"] = items
    if state is not None:
        data["state"] = state
    return data


def error_data(code: ProcErrorCode, retry_after_sec: int | None = None) -> dict[str, Any] | None:
    """`data` of the error line: `retryAfterSec` only for a transient error."""
    if code is ProcErrorCode.TRANSIENT_ERROR and retry_after_sec is not None:
        return {"retryAfterSec": retry_after_sec}
    return None
