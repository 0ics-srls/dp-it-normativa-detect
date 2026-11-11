"""Detect of a configured set of Normattiva / Gazzetta Ufficiale acts: validates the config, emits catalog items, never uses the network.

Item key = ``<actId>|v<version>`` where actId is ``normattiva:<DENOMINAZIONE>:<anno>:<numero>`` or ``gu:<codiceRedazionale>``.
The version is ``1``; a non-blank ``refreshToken`` makes it ``1-<hash>`` so changing the token re-emits every act
(the existence of an act is checked by grab).
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any
from urllib.parse import urlencode

from v11 import ProcErrorCode

KINDS = ("normattiva", "gu")
MAX_ACTS = 50
SEEN_LIMIT = 200
MIN_YEAR, MAX_YEAR = 1861, 2100
CODICE_PATTERN = "^[0-9A-Z]{6,12}$"
DATE_PATTERN = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
BASE_VERSION = "1"

# Normattiva "denominazione atto" values (normattiva_elenco_tipi, 2026-11-11); the export API expects them verbatim.
DENOMINAZIONI = frozenset({
    "COSTITUZIONE", "DECRETO", "DECRETO DEL CAPO DEL GOVERNO", "DECRETO DEL CAPO DEL GOVERNO, PRIMO MINISTRO SEGRETARIO DI STATO",
    "DECRETO DEL CAPO PROVVISORIO DELLO STATO", "DECRETO DEL DUCE", "DECRETO DEL DUCE DEL FASCISMO, CAPO DEL GOVERNO",
    "DECRETO DEL PRESIDENTE DEL CONSIGLIO DEI MINISTRI", "DECRETO DEL PRESIDENTE DELLA REPUBBLICA", "DECRETO-LEGGE",
    "DECRETO-LEGGE LUOGOTENENZIALE", "DECRETO LEGISLATIVO", "DECRETO LEGISLATIVO DEL CAPO PROVVISORIO DELLO STATO",
    "DECRETO LEGISLATIVO LUOGOTENENZIALE", "DECRETO LEGISLATIVO PRESIDENZIALE", "DECRETO LUOGOTENENZIALE", "DECRETO MINISTERIALE",
    "DECRETO PRESIDENZIALE", "DECRETO REALE", "DELIBERAZIONE", "DETERMINAZIONE DEL COMMISSARIO PER LE FINANZE",
    "DETERMINAZIONE DEL COMMISSARIO PER LA PRODUZIONE BELLICA", "DETERMINAZIONE INTERCOMMISSARIALE", "LEGGE", "LEGGE COSTITUZIONALE",
    "ORDINANZA", "REGIO DECRETO", "REGIO DECRETO-LEGGE", "REGIO DECRETO LEGISLATIVO", "REGOLAMENTO",
})

_CODICE = re.compile(CODICE_PATTERN)
_DATE = re.compile(DATE_PATTERN)


class DetectError(Exception):
    """A protocol error: ``code`` is one of the three v1.1 codes."""

    def __init__(self, code: ProcErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _config_error(message: str) -> DetectError:
    return DetectError(ProcErrorCode.CONFIG_ERROR, message)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_kind(kind: Any) -> str:
    if kind not in KINDS:
        raise _config_error(f"kind must be one of {list(KINDS)}, got {kind!r}")
    return str(kind)


def _shape(act: Any) -> str | None:
    if not isinstance(act, dict):
        return None
    return "gu" if "codiceRedazionale" in act else "normattiva" if "denominazione" in act else None


def resolve_kind(kind: Any, acts: Any) -> str:
    """The explicit kind, else inferred from the act shape (the Host has no Source-type template to pass it)."""
    if kind is not None:
        return validate_kind(kind)
    shapes = {_shape(act) for act in acts} if isinstance(acts, list) else set()
    if len(shapes) != 1 or None in shapes:
        msg = "kind is not given and cannot be inferred: acts must be all {denominazione, anno, numero} or all {dataGU, codiceRedazionale}"
        raise _config_error(msg)
    return str(shapes.pop())


def validate_acts(kind: str, acts: Any) -> list[dict[str, Any]]:
    """Returns the acts or raises config_error; runs before anything else."""
    if not isinstance(acts, list) or not acts:
        raise _config_error("acts must be a non-empty array")
    if len(acts) > MAX_ACTS:
        raise _config_error(f"at most {MAX_ACTS} acts are allowed, got {len(acts)}")
    check = _check_normattiva if kind == "normattiva" else _check_gu
    for index, act in enumerate(acts):
        if not isinstance(act, dict):
            raise _config_error(f"acts[{index}] must be an object")
        problem = check(act)
        if problem:
            raise _config_error(f"acts[{index}]: {problem}")
    return acts


def _check_normattiva(act: dict[str, Any]) -> str | None:
    if set(act) != {"denominazione", "anno", "numero"}:
        return "expected exactly denominazione, anno, numero"
    if act["denominazione"] not in DENOMINAZIONI:
        return f"unknown denominazione {act['denominazione']!r}"
    if not _is_int(act["anno"]) or not MIN_YEAR <= act["anno"] <= MAX_YEAR:
        return f"anno must be an integer {MIN_YEAR}..{MAX_YEAR}"
    if not _is_int(act["numero"]) or act["numero"] < 1:
        return "numero must be an integer >= 1"
    return None


def _check_gu(act: dict[str, Any]) -> str | None:
    if set(act) != {"dataGU", "codiceRedazionale"}:
        return "expected exactly dataGU, codiceRedazionale"
    data = act["dataGU"]
    try:
        if not isinstance(data, str) or not _DATE.match(data):
            raise ValueError(data)  # noqa: TRY301
        date.fromisoformat(data)
    except ValueError:
        return "dataGU must be a date YYYY-MM-DD"
    codice = act["codiceRedazionale"]
    if not isinstance(codice, str) or not _CODICE.match(codice):
        return f"codiceRedazionale must match {CODICE_PATTERN}"
    return None


def validate_state(state: Any) -> dict[str, Any] | None:
    if state is None:
        return None
    seen = state.get("seen") if isinstance(state, dict) else None
    if not isinstance(state, dict) or (seen is not None and not (isinstance(seen, list) and all(isinstance(s, str) for s in seen))):
        raise _config_error("state must be an object {seen: [string]}")
    return state


def validate_refresh_token(token: Any) -> str | None:
    if token is None or token == "":
        return None
    if not isinstance(token, str):
        raise _config_error("refreshToken must be a string")
    return token


def version_of(refresh_token: str | None) -> str:
    if not refresh_token:
        return BASE_VERSION
    return f"{BASE_VERSION}-{hashlib.sha256(refresh_token.encode('utf-8')).hexdigest()[:8]}"


def build_found(kind: str, acts: list[dict[str, Any]], refresh_token: str | None) -> list[dict[str, Any]]:
    """One catalog item per distinct act, in config order."""
    version = version_of(refresh_token)
    found: dict[str, dict[str, Any]] = {}
    for act in acts:
        item = _normattiva_item(act, version, refresh_token) if kind == "normattiva" else _gu_item(act, version, refresh_token)
        found.setdefault(item["key"], item)
    return list(found.values())


def _normattiva_item(act: dict[str, Any], version: str, refresh_token: str | None) -> dict[str, Any]:
    act_id = f"normattiva:{act['denominazione']}:{act['anno']}:{act['numero']}"
    query = {"denominazione": act["denominazione"], "anno": act["anno"], "numero": act["numero"]}
    if refresh_token:
        query["refresh"] = version
    return {
        "key": f"{act_id}|v{version}",
        "url": "https://www.normattiva.it/ricerca/avanzata?" + urlencode(query),
        "meta": {"kind": "normattiva", "actId": act_id, "version": version, "denominazione": act["denominazione"],
                 "anno": act["anno"], "numero": act["numero"]},
    }


def _gu_item(act: dict[str, Any], version: str, refresh_token: str | None) -> dict[str, Any]:
    act_id = f"gu:{act['codiceRedazionale']}"
    year, month, day = act["dataGU"].split("-")
    url = f"https://www.gazzettaufficiale.it/eli/id/{year}/{month}/{day}/{act['codiceRedazionale']}/sg"
    if refresh_token:
        url += f"?refresh={version}"
    return {
        "key": f"{act_id}|v{version}",
        "url": url,
        "meta": {"kind": "gu", "actId": act_id, "version": version, "dataGU": act["dataGU"], "codiceRedazionale": act["codiceRedazionale"]},
    }


def build_run_result(found: list[dict[str, Any]], state_in: dict[str, Any] | None) -> dict[str, Any]:
    """Items = acts whose key is not in state.seen; state keeps the newest SEEN_LIMIT keys."""
    seen = list((state_in or {}).get("seen") or [])
    known = set(seen)
    new = [act for act in found if act["key"] not in known]
    seen_out = (seen + [act["key"] for act in new])[-SEEN_LIMIT:]
    return {
        "items": new,
        "state": {"seen": seen_out},
        "metrics": {"acts": len(found), "newActs": len(new)},
        "checks": [{"name": "acts_found", "passed": len(found) > 0, "severity": "error"}],
    }


ACT_SCHEMAS: list[dict[str, Any]] = [
    {"type": "object", "required": ["denominazione", "anno", "numero"], "additionalProperties": False,
     "properties": {"denominazione": {"type": "string", "enum": sorted(DENOMINAZIONI)},
                    "anno": {"type": "integer", "minimum": MIN_YEAR, "maximum": MAX_YEAR},
                    "numero": {"type": "integer", "minimum": 1}}},
    {"type": "object", "required": ["dataGU", "codiceRedazionale"], "additionalProperties": False,
     "properties": {"dataGU": {"type": "string", "pattern": DATE_PATTERN},
                    "codiceRedazionale": {"type": "string", "pattern": CODICE_PATTERN}}},
]

CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["acts"],
    "properties": {
        "kind": {"enum": list(KINDS), "description": "optional (inferred from the acts): normattiva or gu"},
        "acts": {"type": "array", "minItems": 1, "maxItems": MAX_ACTS, "items": {"anyOf": ACT_SCHEMAS}},
        "refreshToken": {"type": ["string", "null"], "description": "change it to fetch every act again"},
        "state": {"type": ["object", "null"], "properties": {"seen": {"type": "array", "items": {"type": "string"}}}},
    },
    "additionalProperties": True,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["items", "state", "metrics", "checks"],
    "properties": {
        "items": {"type": "array", "items": {"type": "object", "required": ["key", "url", "meta"], "properties": {
            "key": {"type": "string", "minLength": 1}, "url": {"type": "string", "minLength": 1},
            "meta": {"type": "object", "required": ["kind", "actId", "version"], "properties": {
                "kind": {"enum": list(KINDS)}, "actId": {"type": "string"}, "version": {"type": "string"}}}}}},
        "state": {"type": "object", "required": ["seen"], "properties": {"seen": {"type": "array", "items": {"type": "string"}}}},
        "metrics": {"type": "object", "properties": {"acts": {"type": "integer"}, "newActs": {"type": "integer"}}},
        "checks": {"type": "array"},
    },
}
