"""Proc-protocol v1.1 detect for a configured set of Normattiva and Gazzetta Ufficiale acts (no network)."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from conftest import MANIFEST, Run, invoke_raw

LEGGE = {"denominazione": "LEGGE", "anno": 2000, "numero": 212}
DL = {"denominazione": "DECRETO-LEGGE", "anno": 2026, "numero": 157}
GU1 = {"dataGU": "2026-09-10", "codiceRedazionale": "26G00178"}
GU2 = {"dataGU": "2000-07-31", "codiceRedazionale": "000G0265"}
Invoke = Callable[..., Run]


def keys(run: Run) -> list[str]:
    return [item["key"] for item in run.result["items"]]


def test_spec_declares_protocol_11_with_valid_schemas(invoke: Invoke) -> None:
    run = invoke("spec")

    assert run.result["protocol"] == "1.1"
    for name in ("configSchema", "outputSchema"):
        Draft202012Validator.check_schema(run.result[name])
    config = run.result["configSchema"]
    assert set(config["required"]) == {"kind", "acts"}
    assert config["properties"]["kind"]["enum"] == ["normattiva", "gu"]
    assert config["properties"]["acts"]["minItems"] == 1
    assert config["properties"]["acts"]["maxItems"] == 50
    assert "refreshToken" in config["properties"]


def test_first_run_emits_one_item_per_act_with_deterministic_keys(invoke: Invoke) -> None:
    run = invoke("run", kind="normattiva", acts=[LEGGE, DL], state=None)

    assert keys(run) == ["normattiva:LEGGE:2000:212|v1", "normattiva:DECRETO-LEGGE:2026:157|v1"]
    first = run.result["items"][0]
    assert first["meta"] == {
        "kind": "normattiva", "actId": "normattiva:LEGGE:2000:212", "version": "1", "denominazione": "LEGGE", "anno": 2000, "numero": 212,
    }
    assert first["url"] == "https://www.normattiva.it/ricerca/avanzata?denominazione=LEGGE&anno=2000&numero=212"
    assert run.result["metrics"] == {"acts": 2, "newActs": 2}
    assert run.result["checks"][0]["passed"] is True


def test_denominazione_with_spaces_is_kept_in_the_key_and_encoded_in_the_url(invoke: Invoke) -> None:
    act = {"denominazione": "DECRETO LEGISLATIVO", "anno": 2003, "numero": 196}

    item = invoke("run", kind="normattiva", acts=[act], state=None).result["items"][0]

    assert item["key"] == "normattiva:DECRETO LEGISLATIVO:2003:196|v1"
    assert item["url"].endswith("denominazione=DECRETO+LEGISLATIVO&anno=2003&numero=196")


def test_gu_acts_are_keyed_by_the_codice_redazionale(invoke: Invoke) -> None:
    run = invoke("run", kind="gu", acts=[GU1, GU2], state=None)

    assert keys(run) == ["gu:26G00178|v1", "gu:000G0265|v1"]
    first = run.result["items"][0]
    assert first["url"] == "https://www.gazzettaufficiale.it/eli/id/2026/09/10/26G00178/sg"
    assert first["meta"] == {
        "kind": "gu", "actId": "gu:26G00178", "version": "1", "dataGU": "2026-09-10", "codiceRedazionale": "26G00178",
    }


def test_output_validates_against_the_output_schema(invoke: Invoke) -> None:
    schema = invoke("spec").result["outputSchema"]

    for result in (invoke("run", kind="normattiva", acts=[LEGGE], state=None).result, invoke("run", kind="gu", acts=[GU1], state=None).result):
        Draft202012Validator(schema).validate(result)


def test_second_run_with_the_returned_state_emits_nothing(invoke: Invoke) -> None:
    first = invoke("run", kind="normattiva", acts=[LEGGE, DL], state=None).result

    second = invoke("run", kind="normattiva", acts=[LEGGE, DL], state=first["state"])

    assert second.result["items"] == []
    assert second.result["state"] == first["state"]
    assert second.result["metrics"] == {"acts": 2, "newActs": 0}


def test_a_key_already_in_state_is_skipped_and_only_new_acts_are_emitted(invoke: Invoke) -> None:
    state = {"seen": ["normattiva:LEGGE:2000:212|v1"]}

    run = invoke("run", kind="normattiva", acts=[LEGGE, DL], state=state)

    assert keys(run) == ["normattiva:DECRETO-LEGGE:2026:157|v1"]
    assert run.result["state"]["seen"] == ["normattiva:LEGGE:2000:212|v1", "normattiva:DECRETO-LEGGE:2026:157|v1"]


def test_changing_the_refresh_token_changes_the_version_and_emits_again(invoke: Invoke) -> None:
    first = invoke("run", kind="normattiva", acts=[LEGGE], state=None).result
    refreshed = invoke("run", kind="normattiva", acts=[LEGGE], state=first["state"], refreshToken="2026-11-11")
    again = invoke("run", kind="normattiva", acts=[LEGGE], state=refreshed.result["state"], refreshToken="2026-11-11")
    changed = invoke("run", kind="normattiva", acts=[LEGGE], state=refreshed.result["state"], refreshToken="2026-11-12")

    [item] = refreshed.result["items"]
    assert item["key"].startswith("normattiva:LEGGE:2000:212|v1-")
    assert item["meta"]["version"].startswith("1-")
    assert item["meta"]["actId"] == "normattiva:LEGGE:2000:212"
    assert again.result["items"] == []
    assert len(changed.result["items"]) == 1
    assert changed.result["items"][0]["key"] != item["key"]


def test_a_blank_refresh_token_is_the_same_as_none(invoke: Invoke) -> None:
    assert keys(invoke("run", kind="gu", acts=[GU1], state=None, refreshToken="")) == ["gu:26G00178|v1"]
    assert keys(invoke("run", kind="gu", acts=[GU1], state=None, refreshToken=None)) == ["gu:26G00178|v1"]


def test_duplicate_acts_in_the_config_emit_one_item(invoke: Invoke) -> None:
    assert keys(invoke("run", kind="normattiva", acts=[LEGGE, dict(LEGGE)], state=None)) == ["normattiva:LEGGE:2000:212|v1"]


def test_state_keeps_only_the_newest_200_keys(invoke: Invoke) -> None:
    acts = [{"denominazione": "LEGGE", "anno": 2000, "numero": n} for n in range(1, 51)]
    state: dict[str, Any] | None = None
    for _ in range(5):  # 5 distinct refresh tokens x 50 acts = 250 keys
        run = invoke("run", kind="normattiva", acts=acts, state=state, refreshToken=f"t{_}")
        state = run.result["state"]

    assert state is not None
    assert len(state["seen"]) == 200


@pytest.mark.parametrize(
    ("kind", "acts"),
    [
        ("normattiva", [{"denominazione": "LEGGINA", "anno": 2000, "numero": 1}]),
        ("normattiva", [{"denominazione": "legge", "anno": 2000, "numero": 1}]),
        ("normattiva", [{"denominazione": "LEGGE", "anno": 1800, "numero": 1}]),
        ("normattiva", [{"denominazione": "LEGGE", "anno": 2101, "numero": 1}]),
        ("normattiva", [{"denominazione": "LEGGE", "anno": 2000, "numero": 0}]),
        ("normattiva", [{"denominazione": "LEGGE", "anno": "2000", "numero": 1}]),
        ("normattiva", [{"denominazione": "LEGGE", "anno": True, "numero": 1}]),
        ("normattiva", [{"denominazione": "LEGGE", "anno": 2000}]),
        ("normattiva", [{**LEGGE, "extra": 1}]),
        ("normattiva", ["LEGGE 2000/212"]),
        ("normattiva", [GU1]),
        ("gu", [LEGGE]),
        ("gu", [{"dataGU": "2026-13-40", "codiceRedazionale": "26G00178"}]),
        ("gu", [{"dataGU": "10/09/2026", "codiceRedazionale": "26G00178"}]),
        ("gu", [{"dataGU": "2026-09-10", "codiceRedazionale": "x"}]),
        ("gu", [{"dataGU": "2026-09-10"}]),
        ("normattiva", []),
        ("normattiva", None),
        ("normattiva", {"denominazione": "LEGGE"}),
        ("other", [LEGGE]),
        (None, [LEGGE]),
    ],
)
def test_invalid_config_is_rejected_with_config_error_for_run_and_check(invoke: Invoke, kind: Any, acts: Any) -> None:
    for action in ("run", "check"):
        run = invoke(action, kind=kind, acts=acts, state=None)

        assert run.error["code"] == "config_error"
        assert run.returncode != 0
        assert [m["type"] for m in run.lines] == ["error"]


def test_more_than_fifty_acts_are_rejected(invoke: Invoke) -> None:
    acts = [{"denominazione": "LEGGE", "anno": 2000, "numero": n} for n in range(1, 52)]

    assert invoke("run", kind="normattiva", acts=acts, state=None).error["code"] == "config_error"


def test_malformed_state_or_refresh_token_is_a_config_error(invoke: Invoke) -> None:
    assert invoke("run", kind="gu", acts=[GU1], state="oops").error["code"] == "config_error"
    assert invoke("run", kind="gu", acts=[GU1], state={"seen": [1]}).error["code"] == "config_error"
    assert invoke("run", kind="gu", acts=[GU1], state=None, refreshToken=5).error["code"] == "config_error"


def test_check_answers_ok_without_any_request(invoke: Invoke) -> None:
    assert invoke("check", kind="normattiva", acts=[LEGGE, DL]).result == {"ok": True}
    assert invoke("check", kind="gu", acts=[GU1]).result == {"ok": True}


def test_execute_is_an_alias_of_run(invoke: Invoke) -> None:
    assert keys(invoke("execute", kind="gu", acts=[GU1], state=None)) == ["gu:26G00178|v1"]


# --- protocol loop --------------------------------------------------------------------------------------------------


def test_a_bad_request_line_emits_one_error_line_and_the_loop_continues() -> None:
    good = json.dumps({"_action": "check", "_meta": {"executionId": "e"}, "kind": "gu", "acts": [GU1]})

    run = invoke_raw("not json\n" + good + "\n")

    assert [m["type"] for m in run.lines] == ["error", "result"]
    assert run.lines[0]["code"] == "config_error"
    assert run.returncode != 0


def test_unknown_action_and_missing_meta_are_config_errors() -> None:
    assert invoke_raw(json.dumps({"_action": "bogus", "_meta": {"executionId": "e"}}) + "\n").error["code"] == "config_error"
    assert invoke_raw(json.dumps({"_action": "run"}) + "\n").error["code"] == "config_error"


def test_terminate_ends_the_loop_and_stdout_has_only_protocol_lines() -> None:
    terminate = json.dumps({"_action": "terminate", "_meta": {"executionId": "e"}})
    after = json.dumps({"_action": "spec", "_meta": {"executionId": "e"}})

    run = invoke_raw(terminate + "\n" + after + "\n")

    assert run.lines == [{"type": "result", "data": {"cleaned": True}}]
    for raw in run.stdout.splitlines():
        assert json.loads(raw)["type"] in {"progress", "result", "error"}


# --- manifest and conformance -----------------------------------------------------------------------------------------


def test_manifest_declares_the_v11_actions_the_mandatory_execute_and_no_hosts() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["name"] == "dp-it-normativa-detect"
    assert manifest["specVersion"] == "1.1"
    assert set(manifest["actions"]) == {"spec", "check", "run", "execute", "terminate"}
    for name in ("spec", "check", "run", "execute"):
        assert manifest["actions"][name]["input"]["type"] == "object"
        assert manifest["actions"][name]["output"]["type"] == "object"
    assert manifest["actions"]["run"]["input"]["required"] == ["kind", "acts"]


def test_the_package_conformance_fixture_is_valid_input_for_the_class(invoke: Invoke) -> None:
    fixture = Path(__file__).resolve().parents[2].parent.parent / "dp-it-normativa" / "tests" / "conformance" / "detect.json"
    if not fixture.exists():
        pytest.skip("package checkout not next to the class (submodule layout)")
    config = json.loads(fixture.read_text(encoding="utf-8"))

    assert invoke("check", **config["check"]).result == {"ok": True}
    assert len(invoke("run", **config["run"]).result["items"]) >= config["expect"]["minItems"] >= 1
