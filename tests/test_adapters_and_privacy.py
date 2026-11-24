"""Tests for Corporate zero-employer-data privacy invariant and replaceable Personal harness adapters."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from reclaimweave import AgentTaskState, ReclaimService, WeaveOSAdapter, WorldMode


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_corporate_mode_never_persists_employer_task_or_outcome(tmp_path: Path) -> None:
    svc = ReclaimService(tmp_path)
    res = svc.hand_off(
        world=WorldMode.CORPORATE,
        estimated_minutes=15,
        task="CONFIDENTIAL BANK LEDGER RECONCILIATION TICKET-9921",
    )
    sid = res["session"]["session_id"]
    stored = svc.store.get_session(sid)
    assert stored is not None
    assert stored.world == WorldMode.CORPORATE
    assert stored.task_label is None
    assert stored.external_run_id is None

    done = svc.complete_handoff(
        session_id=sid,
        state=AgentTaskState.DONE,
        reclaimed_minutes=15,
        raw_outcome="CONFIDENTIAL EMPLOYER OUTPUT SHOULD NOT APPEAR",
    )
    assert "CONFIDENTIAL" not in done["brief"]["summary"]
    assert "CONFIDENTIAL" not in done["brief"]["spoken_brief"]


def test_personal_weaveos_http_adapter_and_webhook_harness_flow(tmp_path: Path) -> None:
    def fake_weaveos_opener(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        del timeout
        if req.full_url.endswith("/v1/flows/run"):
            return _FakeHTTPResponse({"run_id": "flow_abc123", "status": "running"})
        if req.full_url.endswith("/v1/flows/flow_abc123"):
            return _FakeHTTPResponse(
                {"run_id": "flow_abc123", "status": "completed", "final_output": "All 48 checks green."}
            )
        raise AssertionError(f"Unexpected URL: {req.full_url}")

    weaveos = WeaveOSAdapter(
        base_url="http://127.0.0.1:8787",
        token="dev-tok",
        http_opener=fake_weaveos_opener,
    )
    svc = ReclaimService(tmp_path, adapters={"weaveos": weaveos})

    res = svc.hand_off(
        world=WorldMode.PERSONAL,
        adapter_name="weaveos",
        estimated_minutes=10,
        task="Run nightly WeaveOS checks",
    )
    assert res["session"]["external_run_id"] == "flow_abc123"
    assert res["session"]["task_label"] == "Run nightly WeaveOS checks"

    poll = weaveos.poll_state("flow_abc123")
    assert poll.state == AgentTaskState.DONE
    assert poll.raw_summary == "All 48 checks green."

    # Webhook ingestion closes the loop with one quiet completion brief
    hooked = svc.ingest_agent_hook(
        external_run_id="flow_abc123",
        state=AgentTaskState.DONE,
        summary="All 48 checks green.",
    )
    assert hooked["session"]["state"] == "done"
    assert "All 48 checks green." in hooked["brief"]["summary"]
