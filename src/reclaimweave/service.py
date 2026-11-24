"""High-level orchestration service for Reclaim (ReclaimWeave)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from reclaimweave.adapters import HarnessAdapter, build_default_adapters
from reclaimweave.brief import build_quiet_brief
from reclaimweave.ledger import build_reclamation_ledger
from reclaimweave.models import (
    AgentTaskState,
    BiometricSnapshot,
    HandoffSession,
    RedirectSuggestion,
    WorldMode,
    utc_now_iso,
)
from reclaimweave.redirect import is_in_fatigue_cooldown, recommend_redirects
from reclaimweave.social import SocialCoordinator
from reclaimweave.store import ReclaimStore
from reclaimweave.whoop import WhoopOAuthConfig, WhoopV2Client


class ReclaimService:
    """Orchestrates the full Reclaim loop across Corporate & Personal worlds."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        adapters: dict[str, HarnessAdapter] | None = None,
        whoop_client: WhoopV2Client | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = ReclaimStore(self.data_dir / "reclaim.sqlite")
        self.social = SocialCoordinator(self.store)
        self.adapters = adapters if adapters is not None else build_default_adapters()
        self.whoop = whoop_client or WhoopV2Client(
            WhoopOAuthConfig.from_env(),
            self.data_dir / "whoop_tokens.json",
        )

    def current_biometrics(self) -> BiometricSnapshot:
        """Return latest local biometric snapshot, or a sane neutral baseline."""
        snap = self.store.latest_biometric()
        if snap is not None:
            return snap
        default_snap = BiometricSnapshot(
            recovery_pct=74,
            strain=6.8,
            resting_hr_bpm=54,
            hrv_ms=72.0,
            sleep_performance_pct=86,
            sleep_debt_min=20,
            minutes_since_movement=55,
            source="local_checkin",
        )
        return self.store.save_biometric(default_snap)

    def record_biometrics(self, snap: BiometricSnapshot) -> BiometricSnapshot:
        return self.store.save_biometric(snap)

    def hand_off(
        self,
        *,
        world: WorldMode = WorldMode.CORPORATE,
        adapter_name: str | None = None,
        estimated_minutes: int = 15,
        task: str | None = None,
        local_hour: int | None = None,
        respect_cooldown: bool = False,
        announce_free_handle: str | None = None,
        announce_display_name: str | None = None,
    ) -> dict[str, Any]:
        """Start a handoff loop and return 2-3 calm, WHOOP-calibrated step-away suggestions."""
        resolved_adapter_name = (
            "corporate_manual"
            if world == WorldMode.CORPORATE
            else (adapter_name or "claude")
        )
        adapter = self.adapters.get(resolved_adapter_name)
        if adapter is None:
            raise ValueError(f"Unknown harness adapter {resolved_adapter_name!r}")

        delegation = adapter.delegate(
            task=None if world == WorldMode.CORPORATE else task,
            estimated_minutes=estimated_minutes,
        )

        session = HandoffSession(
            session_id=f"rec_{uuid.uuid4().hex[:10]}",
            world=world,
            adapter=delegation.adapter,
            state=delegation.state,
            estimated_minutes=max(3, min(180, int(estimated_minutes))),
            started_at=utc_now_iso(),
            task_label=None if world == WorldMode.CORPORATE else delegation.safe_label,
            external_run_id=None if world == WorldMode.CORPORATE else delegation.external_run_id,
        )
        self.store.save_session(session)

        if announce_free_handle:
            self.social.announce_free(
                handle=announce_free_handle,
                display_name=announce_display_name or announce_free_handle,
                free_for_min=session.estimated_minutes,
                vibe="walk",
            )

        biometrics = self.current_biometrics()
        free_peers = self.social.who_is_free(exclude_handle=announce_free_handle)
        cooldown_active = respect_cooldown and is_in_fatigue_cooldown(
            self.store.get_meta("last_prompted_at")
        )

        suggestions: list[RedirectSuggestion] = []
        if not cooldown_active:
            suggestions = recommend_redirects(
                biometrics=biometrics,
                free_window_min=session.estimated_minutes,
                free_peers=free_peers,
                local_hour=local_hour,
            )
            self.store.set_meta("last_prompted_at", utc_now_iso())

        return {
            "session": session.to_dict(),
            "biometrics": biometrics.to_dict(),
            "cooldown_active": cooldown_active,
            "free_peers": [p.to_dict() for p in free_peers],
            "suggestions": [s.to_dict() for s in suggestions],
            "ambient_card": self.build_ambient_card(session=session, suggestions=suggestions),
        }

    def accept_suggestion(
        self,
        *,
        session_id: str,
        suggestion_id: str,
        category: str,
        title: str,
        outdoor: bool = False,
        estimated_co2_saved_g: int = 35,
        peer_handle: str | None = None,
    ) -> HandoffSession:
        session = self.store.get_session(session_id)
        if session is None:
            raise KeyError(f"Unknown session {session_id!r}")
        session.chosen_suggestion_id = suggestion_id
        session.chosen_category = category
        session.chosen_title = title
        session.outdoor = bool(outdoor)
        session.estimated_co2_saved_g = max(0, int(estimated_co2_saved_g))
        session.peer_handle = peer_handle
        if peer_handle:
            self.social.connect_with_peer(
                peer_handle=peer_handle,
                vibe="walk" if outdoor else "voice",
                session_id=session_id,
            )
        return self.store.save_session(session)

    def complete_handoff(
        self,
        *,
        session_id: str,
        state: AgentTaskState = AgentTaskState.DONE,
        reclaimed_minutes: int | None = None,
        raw_outcome: str | None = None,
        self_reported_stress: int | None = None,
    ) -> dict[str, Any]:
        """Finish a handoff, log reclaimed minutes, and return ONE quiet completion brief."""
        session = self.store.get_session(session_id)
        if session is None:
            raise KeyError(f"Unknown session {session_id!r}")

        session.state = state
        session.completed_at = utc_now_iso()
        if reclaimed_minutes is not None:
            session.reclaimed_minutes = max(0, int(reclaimed_minutes))
        else:
            session.reclaimed_minutes = session.estimated_minutes
        if self_reported_stress is not None:
            session.self_reported_stress = max(1, min(5, int(self_reported_stress)))

        brief = build_quiet_brief(
            session,
            raw_outcome=None if session.world == WorldMode.CORPORATE else raw_outcome,
        )
        session.quiet_brief = brief.summary
        self.store.save_session(session)

        return {
            "session": session.to_dict(),
            "brief": brief.to_dict(),
            "ledger": build_reclamation_ledger(self.store),
        }

    def ingest_agent_hook(
        self,
        *,
        external_run_id: str | None = None,
        session_id: str | None = None,
        state: AgentTaskState = AgentTaskState.DONE,
        summary: str | None = None,
    ) -> dict[str, Any]:
        """Receive a quiet state update from Claude Code, Antigravity, Grokbot, Cursor, or WeaveOS."""
        target: HandoffSession | None = None
        if session_id:
            target = self.store.get_session(session_id)
        elif external_run_id:
            for s in self.store.list_sessions(days=2, limit=50):
                if s.external_run_id == external_run_id:
                    target = s
                    break
        if target is None:
            target = self.store.get_active_session()
        if target is None:
            raise KeyError("No active handoff session matches this webhook")

        if state in (AgentTaskState.DONE, AgentTaskState.BLOCKED):
            return self.complete_handoff(
                session_id=target.session_id,
                state=state,
                raw_outcome=summary,
            )

        target.state = state
        self.store.save_session(target)
        return {"session": target.to_dict(), "brief": None}

    def handle_whoop_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle WHOOP v2 webhooks including mandatory deletion events."""
        event_type = str(payload.get("type") or "")
        record_id = str(payload.get("id") or "")
        if event_type in ("recovery.deleted", "sleep.deleted") and record_id:
            deleted = self.store.delete_whoop_cycle(record_id)
            return {"event": event_type, "deleted_records": deleted}
        return {"event": event_type, "acknowledged": True}

    def build_ambient_card(
        self,
        *,
        session: HandoffSession | None = None,
        suggestions: list[RedirectSuggestion] | None = None,
    ) -> dict[str, Any]:
        """Render quiet glanceable payloads for 2x2\" watch, 1x1\" charm, and counter-top display."""
        active = session or self.store.get_active_session()
        bio = self.current_biometrics()
        ledger = build_reclamation_ledger(self.store, days=7)
        today_reclaimed = int(ledger["today"]["minutes_reclaimed"])

        if active and active.state in (AgentTaskState.RUNNING, AgentTaskState.WAITING):
            top = suggestions[0] if suggestions else None
            watch_line = (
                active.chosen_title
                or (top.title if top else f"Step away · {active.estimated_minutes}m")
            )
            return {
                "mode": "away",
                "watch_2x2": {
                    "top": f"REC {bio.recovery_pct}% · {bio.zone.value.upper()}",
                    "headline": watch_line[:34],
                    "footer": f"Agent running · ~{active.estimated_minutes}m",
                    "button_a": "I'm stepping away",
                    "button_b": "Check brief",
                },
                "charm_1x1": {
                    "line1": f"FREE {active.estimated_minutes}m",
                    "line2": (watch_line[:18]),
                    "line3": f"REC {bio.recovery_pct}%",
                },
            }

        return {
            "mode": "ready",
            "watch_2x2": {
                "top": f"REC {bio.recovery_pct}% · {bio.zone.value.upper()}",
                "headline": f"{today_reclaimed}m reclaimed today",
                "footer": "Tap when you hand off",
                "button_a": "Handed to agent",
                "button_b": "Who's free?",
            },
            "charm_1x1": {
                "line1": f"{today_reclaimed}m BACK",
                "line2": f"REC {bio.recovery_pct}%",
                "line3": "TAP HANDOFF",
            },
        }

    def seed_realistic_week(self) -> dict[str, Any]:
        """Seed 6 prior days + 2 free teammates so the user can experience the full ledger & social loop."""
        now = datetime.now(timezone.utc)
        week_data = [
            # (days_ago, recovery, strain, rhr, hrv, sleep_perf, debt, handed_m, reclaimed_m, outdoor, peer, stress)
            (6, 54, 9.2, 58, 56.0, 74, 48, 70, 25, False, None, 4),
            (5, 61, 10.4, 57, 61.5, 79, 36, 85, 45, True, "maya", 3),
            (4, 68, 11.1, 55, 68.0, 84, 24, 95, 65, True, "dev", 2),
            (3, 47, 8.0, 59, 51.0, 69, 55, 60, 40, False, None, 3),
            (2, 76, 12.2, 53, 75.4, 89, 15, 110, 85, True, "maya", 2),
            (1, 82, 11.8, 52, 81.2, 92, 10, 105, 90, True, "elena", 1),
        ]
        for (
            days_ago,
            rec,
            strain,
            rhr,
            hrv,
            slp,
            debt,
            handed,
            reclaimed,
            outdoor,
            peer,
            stress,
        ) in week_data:
            ts = (now - timedelta(days=days_ago)).replace(hour=14, minute=15, second=0, microsecond=0)
            iso = ts.isoformat()
            self.store.save_biometric(
                BiometricSnapshot(
                    recovery_pct=rec,
                    strain=strain,
                    resting_hr_bpm=rhr,
                    hrv_ms=hrv,
                    sleep_performance_pct=slp,
                    sleep_debt_min=debt,
                    minutes_since_movement=40,
                    source="whoop_v2",
                    recorded_at=iso,
                )
            )
            sess = HandoffSession(
                session_id=f"seed_day_{days_ago}",
                world=WorldMode.CORPORATE if days_ago % 2 == 0 else WorldMode.PERSONAL,
                adapter="corporate_manual" if days_ago % 2 == 0 else "claude",
                state=AgentTaskState.DONE,
                estimated_minutes=handed,
                started_at=iso,
                task_label=None if days_ago % 2 == 0 else "Background refactor & test run",
                chosen_category="social" if peer else ("movement" if outdoor else "hydration"),
                chosen_title=(
                    f"Walk & talk with {peer.title()}"
                    if peer
                    else ("15-min outdoor walk" if outdoor else "Water & daylight reset")
                ),
                outdoor=outdoor,
                peer_handle=peer,
                estimated_co2_saved_g=140 if outdoor else 35,
                reclaimed_minutes=reclaimed,
                completed_at=(ts + timedelta(minutes=reclaimed)).isoformat(),
                quiet_brief=f"Reclaimed {reclaimed} min away from the screen.",
                self_reported_stress=stress,
            )
            self.store.save_session(sess)

        # Today's fresh WHOOP snapshot
        self.store.save_biometric(
            BiometricSnapshot(
                recovery_pct=78,
                strain=6.4,
                resting_hr_bpm=53,
                hrv_ms=77.5,
                sleep_performance_pct=88,
                sleep_debt_min=15,
                minutes_since_movement=62,
                source="whoop_v2",
            )
        )

        # Two teammates who also handed work to an agent right now
        self.social.announce_free(
            handle="maya",
            display_name="Maya Chen",
            free_for_min=18,
            vibe="walk",
            location_hint="South courtyard",
        )
        self.social.announce_free(
            handle="dev",
            display_name="Dev Patel",
            free_for_min=12,
            vibe="voice",
            location_hint="Remote · earbuds in",
        )

        return {
            "seeded": True,
            "ledger": build_reclamation_ledger(self.store),
            "free_peers": [p.to_dict() for p in self.social.who_is_free()],
            "biometrics": self.current_biometrics().to_dict(),
        }
