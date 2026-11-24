"""Replaceable Agent Harness Adapters (the Grokbot-over-Cursor pattern, generalized).

Every agent harness (WeaveOS, Claude Code, Antigravity, Grokbot, Cursor, Muse) is treated
as a swappable adapter behind a minimal 2-method contract: `delegate` and `poll_state`.

Corporate World (`CorporateManualAdapter`):
- Zero employer integration, zero screen scraping, zero employer IT surface.
- Captures ONLY the user's own tap ("I handed work to the agent") and estimated window.
- Forcibly drops any task description or employer identifier.
"""

from __future__ import annotations

import json
import os
import urllib.request
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from reclaimweave.models import AgentTaskState, WorldMode


@dataclass(frozen=True)
class DelegationResult:
    adapter: str
    world: WorldMode
    state: AgentTaskState
    external_run_id: str | None = None
    safe_label: str | None = None


@dataclass(frozen=True)
class PollResult:
    state: AgentTaskState
    raw_summary: str | None = None


class HarnessAdapter(ABC):
    name: str
    world: WorldMode

    @abstractmethod
    def delegate(
        self,
        *,
        task: str | None,
        estimated_minutes: int,
    ) -> DelegationResult:
        """Initiate or register an agent handoff."""

    @abstractmethod
    def poll_state(self, external_run_id: str | None) -> PollResult:
        """Query current task state without requiring the user to watch logs."""


class CorporateManualAdapter(HarnessAdapter):
    """V0 Enterprise-safe manual handoff. Never touches or stores employer data."""

    name = "corporate_manual"
    world = WorldMode.CORPORATE

    def delegate(
        self,
        *,
        task: str | None,
        estimated_minutes: int,
    ) -> DelegationResult:
        # Deliberately ignore `task`: zero employer data leaves or enters storage.
        del task, estimated_minutes
        return DelegationResult(
            adapter=self.name,
            world=WorldMode.CORPORATE,
            state=AgentTaskState.RUNNING,
            external_run_id=None,
            safe_label=None,
        )

    def poll_state(self, external_run_id: str | None) -> PollResult:
        del external_run_id
        return PollResult(state=AgentTaskState.RUNNING, raw_summary=None)


class WebhookHarnessAdapter(HarnessAdapter):
    """Universal V1 Personal adapter for Claude Code, Antigravity, Grokbot, Cursor, Muse.

    Harnesses report state changes (`running`, `waiting`, `done`, `blocked`) via
    `POST /v1/hooks/agent-state` without Reclaim needing tight SDK coupling.
    """

    def __init__(self, harness_name: str = "webhook") -> None:
        self.name = harness_name
        self.world = WorldMode.PERSONAL

    def delegate(
        self,
        *,
        task: str | None,
        estimated_minutes: int,
    ) -> DelegationResult:
        del estimated_minutes
        run_id = f"{self.name}_{uuid.uuid4().hex[:10]}"
        label = (task or "").strip()[:120] or f"Delegated to {self.name}"
        return DelegationResult(
            adapter=self.name,
            world=WorldMode.PERSONAL,
            state=AgentTaskState.RUNNING,
            external_run_id=run_id,
            safe_label=label,
        )

    def poll_state(self, external_run_id: str | None) -> PollResult:
        del external_run_id
        return PollResult(state=AgentTaskState.RUNNING, raw_summary=None)


class WeaveOSAdapter(HarnessAdapter):
    """V1 Personal adapter delegating over HTTP to a local WeaveOS router (zero import coupling)."""

    name = "weaveos"
    world = WorldMode.PERSONAL

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        http_opener: Any | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("WEAVEOS_URL", "http://127.0.0.1:8787")).rstrip(
            "/"
        )
        self.token = token or os.environ.get("WEAVE_ROUTER_TOKEN", "")
        self._http_opener = http_opener or urllib.request.urlopen

    def _headers(self) -> dict[str, str]:
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        return hdrs

    def delegate(
        self,
        *,
        task: str | None,
        estimated_minutes: int,
    ) -> DelegationResult:
        del estimated_minutes
        goal = (task or "").strip() or "Run background home check"
        payload = json.dumps({"goal": goal, "session_id": f"reclaim-{uuid.uuid4().hex[:8]}"}).encode(
            "utf-8"
        )
        req = urllib.request.Request(
            f"{self.base_url}/v1/flows/run",
            data=payload,
            method="POST",
            headers=self._headers(),
        )
        with self._http_opener(req, timeout=15) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        status_raw = str(data.get("status", "running")).lower()
        state = _map_weaveos_status(status_raw)
        return DelegationResult(
            adapter=self.name,
            world=WorldMode.PERSONAL,
            state=state,
            external_run_id=str(data.get("run_id") or ""),
            safe_label=goal[:120],
        )

    def poll_state(self, external_run_id: str | None) -> PollResult:
        if not external_run_id:
            return PollResult(state=AgentTaskState.RUNNING)
        req = urllib.request.Request(
            f"{self.base_url}/v1/flows/{external_run_id}",
            method="GET",
            headers=self._headers(),
        )
        with self._http_opener(req, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        status_raw = str(data.get("status", "running")).lower()
        summary = data.get("final_output") or data.get("error")
        return PollResult(
            state=_map_weaveos_status(status_raw),
            raw_summary=str(summary) if summary else None,
        )


def _map_weaveos_status(status: str) -> AgentTaskState:
    if status in ("completed", "done", "succeeded"):
        return AgentTaskState.DONE
    if status in ("awaiting_approval", "waiting", "paused"):
        return AgentTaskState.WAITING
    if status in ("failed", "denied", "blocked", "error"):
        return AgentTaskState.BLOCKED
    return AgentTaskState.RUNNING


def build_default_adapters() -> dict[str, HarnessAdapter]:
    return {
        "corporate_manual": CorporateManualAdapter(),
        "weaveos": WeaveOSAdapter(),
        "claude": WebhookHarnessAdapter("claude"),
        "antigravity": WebhookHarnessAdapter("antigravity"),
        "grokbot": WebhookHarnessAdapter("grokbot"),
        "cursor": WebhookHarnessAdapter("cursor"),
        "muse": WebhookHarnessAdapter("muse"),
    }
