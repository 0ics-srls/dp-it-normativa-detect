"""Entrypoint of dp-it-normativa-detect: Proc-protocol v1.1 stdin loop with spec / check / run (+ execute alias) / terminate."""
from __future__ import annotations

import logging
import sys
from typing import Any, TextIO

import procclass
import v11
from dp_it_normativa_detect import detect

log = logging.getLogger("dp_it_normativa_detect")
EXIT_OK = 0
EXIT_ERROR = 1
ACTIONS = (*v11.ACTIONS, "execute")  # execute = run, mandatory for ProcMon manifests


def handle(action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Runs one v1.1 action and returns result.data; raises DetectError."""
    if action == "spec":
        return v11.spec_result(detect.CONFIG_SCHEMA, detect.OUTPUT_SCHEMA)
    kind = detect.validate_kind(params.get("kind"))
    acts = detect.validate_acts(kind, params.get("acts"))
    if action == "check":
        return {"ok": True}
    state = detect.validate_state(params.get("state"))
    token = detect.validate_refresh_token(params.get("refreshToken"))
    return detect.build_run_result(detect.build_found(kind, acts, token), state)


def main(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Reads requests until terminate/EOF; returns EXIT_ERROR if any request failed."""
    exit_code = EXIT_OK
    for line in stdin:
        if not line.strip():
            continue
        try:
            request = procclass.parse(line)
        except procclass.ProcProtocolError as ex:
            procclass.emit_error(stdout, v11.ProcErrorCode.CONFIG_ERROR.value, str(ex))
            exit_code = EXIT_ERROR
            continue
        if request.action == "terminate":
            procclass.emit_result(stdout, {"cleaned": True})
            break
        if request.action not in ACTIONS:
            procclass.emit_error(stdout, v11.ProcErrorCode.CONFIG_ERROR.value, f"unknown action: {request.action}")
            exit_code = EXIT_ERROR
            continue
        try:
            procclass.emit_result(stdout, handle(request.action, request.params))
        except detect.DetectError as ex:
            procclass.emit_error(stdout, ex.code.value, str(ex), v11.error_data(ex.code))
            exit_code = EXIT_ERROR
        except Exception:
            log.exception("unexpected failure in action %s", request.action)
            procclass.emit_error(stdout, v11.ProcErrorCode.SYSTEM_ERROR.value, f"unexpected failure in action {request.action}")
            exit_code = EXIT_ERROR
    return exit_code


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(main())
