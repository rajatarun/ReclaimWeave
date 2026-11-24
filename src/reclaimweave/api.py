"""Local-first FastAPI server for Reclaim (ReclaimWeave)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from reclaimweave.ledger import build_reclamation_ledger
from reclaimweave.models import AgentTaskState, BiometricSnapshot, WorldMode
from reclaimweave.service import ReclaimService

_UI_DIR = Path(__file__).parent / "ui"


class HandoffRequest(BaseModel):
    world: WorldMode = WorldMode.CORPORATE
    adapter: str | None = None
    estimated_minutes: int = Field(default=15, ge=3, le=180)
    task: str | None = None
    local_hour: int | None = Field(default=None, ge=0, le=23)
    respect_cooldown: bool = False
    announce_free_handle: str | None = None
    announce_display_name: str | None = None


class AcceptSuggestionRequest(BaseModel):
    suggestion_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    title: str = Field(min_length=1)
    outdoor: bool = False
    estimated_co2_saved_g: int = Field(default=35, ge=0)
    peer_handle: str | None = None


class CompleteHandoffRequest(BaseModel):
    state: AgentTaskState = AgentTaskState.DONE
    reclaimed_minutes: int | None = Field(default=None, ge=0, le=240)
    raw_outcome: str | None = None
    self_reported_stress: int | None = Field(default=None, ge=1, le=5)


class AgentStateHookRequest(BaseModel):
    session_id: str | None = None
    external_run_id: str | None = None
    state: AgentTaskState = AgentTaskState.DONE
    summary: str | None = None


class BiometricUpdateRequest(BaseModel):
    recovery_pct: int = Field(ge=0, le=100)
    strain: float = Field(ge=0.0, le=21.0)
    resting_hr_bpm: int = Field(default=55, ge=30, le=140)
    hrv_ms: float = Field(default=68.0, ge=5.0, le=250.0)
    sleep_performance_pct: int = Field(default=84, ge=0, le=100)
    sleep_debt_min: int = Field(default=20, ge=0, le=600)
    minutes_since_movement: int = Field(default=45, ge=0, le=720)
    source: str = "local_checkin"


class AnnounceFreeRequest(BaseModel):
    handle: str = Field(min_length=1, max_length=40)
    display_name: str = Field(min_length=1, max_length=80)
    free_for_min: int = Field(default=15, ge=3, le=90)
    vibe: str = "walk"
    location_hint: str = "nearby"


class SocialConnectRequest(BaseModel):
    peer_handle: str = Field(min_length=1, max_length=40)
    vibe: str = "walk"
    session_id: str | None = None


def create_app(data_dir: Path | str | None = None) -> FastAPI:
    resolved_dir = Path(
        data_dir or os.environ.get("RECLAIM_DATA_DIR", Path.home() / ".reclaimweave")
    )
    service = ReclaimService(resolved_dir)
    app = FastAPI(
        title="Reclaim — The Agent Companion Layer",
        version="0.1.0",
        description="WHOOP for the life around agent work. Anti-attention, local-first.",
    )
    app.state.reclaim = service

    @app.get("/", response_class=HTMLResponse)
    def companion_ui() -> HTMLResponse:
        return HTMLResponse((_UI_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "reclaimweave", "version": "0.1.0"}

    @app.get("/v1/status")
    def status() -> dict[str, Any]:
        active = service.store.get_active_session()
        return {
            "biometrics": service.current_biometrics().to_dict(),
            "whoop": service.whoop.status(),
            "active_session": active.to_dict() if active else None,
            "free_peers": [p.to_dict() for p in service.social.who_is_free()],
            "adapters": sorted(service.adapters.keys()),
            "ambient_card": service.build_ambient_card(session=active),
            "ledger": build_reclamation_ledger(service.store),
        }

    @app.post("/v1/handoff")
    def handoff(req: HandoffRequest) -> dict[str, Any]:
        try:
            return service.hand_off(
                world=req.world,
                adapter_name=req.adapter,
                estimated_minutes=req.estimated_minutes,
                task=req.task,
                local_hour=req.local_hour,
                respect_cooldown=req.respect_cooldown,
                announce_free_handle=req.announce_free_handle,
                announce_display_name=req.announce_display_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/handoff/{session_id}/accept")
    def accept_suggestion(session_id: str, req: AcceptSuggestionRequest) -> dict[str, Any]:
        try:
            sess = service.accept_suggestion(
                session_id=session_id,
                suggestion_id=req.suggestion_id,
                category=req.category,
                title=req.title,
                outdoor=req.outdoor,
                estimated_co2_saved_g=req.estimated_co2_saved_g,
                peer_handle=req.peer_handle,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "session": sess.to_dict(),
            "ambient_card": service.build_ambient_card(session=sess),
        }

    @app.post("/v1/handoff/{session_id}/complete")
    def complete_handoff(session_id: str, req: CompleteHandoffRequest) -> dict[str, Any]:
        try:
            return service.complete_handoff(
                session_id=session_id,
                state=req.state,
                reclaimed_minutes=req.reclaimed_minutes,
                raw_outcome=req.raw_outcome,
                self_reported_stress=req.self_reported_stress,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/hooks/agent-state")
    def agent_state_hook(req: AgentStateHookRequest) -> dict[str, Any]:
        try:
            return service.ingest_agent_hook(
                session_id=req.session_id,
                external_run_id=req.external_run_id,
                state=req.state,
                summary=req.summary,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/biometrics")
    def update_biometrics(req: BiometricUpdateRequest) -> dict[str, Any]:
        snap = service.record_biometrics(
            BiometricSnapshot(
                recovery_pct=req.recovery_pct,
                strain=req.strain,
                resting_hr_bpm=req.resting_hr_bpm,
                hrv_ms=req.hrv_ms,
                sleep_performance_pct=req.sleep_performance_pct,
                sleep_debt_min=req.sleep_debt_min,
                minutes_since_movement=req.minutes_since_movement,
                source=req.source,
            )
        )
        return {
            "biometrics": snap.to_dict(),
            "ambient_card": service.build_ambient_card(),
        }

    @app.get("/v1/whoop/status")
    def whoop_status() -> dict[str, Any]:
        return service.whoop.status()

    @app.get("/v1/whoop/authorize")
    def whoop_authorize() -> dict[str, Any]:
        try:
            url, state = service.whoop.build_authorize_url()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        service.store.set_meta("whoop_oauth_state", state)
        return {"authorize_url": url, "state": state}

    @app.get("/v1/whoop/callback")
    def whoop_callback(code: str = Query(...), state: str = Query(...)) -> dict[str, Any]:
        expected_state = service.store.get_meta("whoop_oauth_state")
        if expected_state and state != expected_state:
            raise HTTPException(status_code=400, detail="OAuth state mismatch")
        try:
            service.whoop.exchange_code(code)
            snap = service.whoop.fetch_latest_snapshot()
            service.record_biometrics(snap)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"WHOOP sync failed: {exc}") from exc
        return {"connected": True, "biometrics": snap.to_dict()}

    @app.post("/v1/whoop/webhook")
    def whoop_webhook(payload: dict[str, Any]) -> dict[str, Any]:
        return service.handle_whoop_webhook(payload)

    @app.get("/v1/social/free")
    def social_free() -> dict[str, Any]:
        return {"peers": [p.to_dict() for p in service.social.who_is_free()]}

    @app.post("/v1/social/free")
    def social_announce_free(req: AnnounceFreeRequest) -> dict[str, Any]:
        peer = service.social.announce_free(
            handle=req.handle,
            display_name=req.display_name,
            free_for_min=req.free_for_min,
            vibe=req.vibe,
            location_hint=req.location_hint,
        )
        return {"peer": peer.to_dict()}

    @app.post("/v1/social/connect")
    def social_connect(req: SocialConnectRequest) -> dict[str, Any]:
        return service.social.connect_with_peer(
            peer_handle=req.peer_handle,
            vibe=req.vibe,
            session_id=req.session_id,
        )

    @app.get("/v1/ledger")
    def ledger(days: int = Query(default=14, ge=1, le=90)) -> dict[str, Any]:
        return build_reclamation_ledger(service.store, days=days)

    @app.get("/v1/ambient/card")
    def ambient_card() -> dict[str, Any]:
        return service.build_ambient_card()

    @app.post("/v1/demo/seed")
    def demo_seed() -> dict[str, Any]:
        return service.seed_realistic_week()

    return app


app = create_app()
