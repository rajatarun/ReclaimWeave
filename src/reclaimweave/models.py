"""Domain models for Reclaim (ReclaimWeave) — The Agent Companion Layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorldMode(str, Enum):
    """Two worlds supported from day one."""

    CORPORATE = "corporate"  # Zero employer integration; one-tap manual signal only
    PERSONAL = "personal"  # Full replaceable harness integration


class AgentTaskState(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    BLOCKED = "blocked"


class RecoveryZone(str, Enum):
    RED = "red"  # < 34%
    YELLOW = "yellow"  # 34% - 66%
    GREEN = "green"  # >= 67%


@dataclass(frozen=True)
class BiometricSnapshot:
    """WHOOP-compatible daily readiness snapshot (or local honest check-in)."""

    recovery_pct: int  # 0-100
    strain: float  # 0.0 - 21.0 WHOOP scale
    resting_hr_bpm: int
    hrv_ms: float
    sleep_performance_pct: int  # 0-100
    sleep_debt_min: int  # >= 0
    minutes_since_movement: int  # >= 0
    source: str = "whoop_v2"  # "whoop_v2" | "local_checkin"
    recorded_at: str = field(default_factory=utc_now_iso)
    whoop_cycle_id: str | None = None

    @property
    def zone(self) -> RecoveryZone:
        if self.recovery_pct >= 67:
            return RecoveryZone.GREEN
        if self.recovery_pct >= 34:
            return RecoveryZone.YELLOW
        return RecoveryZone.RED

    @property
    def spare_strain_capacity(self) -> float:
        """Approximate remaining daily strain headroom on the 0-21 WHOOP scale."""
        target = 15.5 if self.zone == RecoveryZone.GREEN else (11.5 if self.zone == RecoveryZone.YELLOW else 7.5)
        return round(max(0.0, target - self.strain), 1)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["zone"] = self.zone.value
        data["spare_strain_capacity"] = self.spare_strain_capacity
        return data


@dataclass(frozen=True)
class PeerPresence:
    """Ephemeral, opt-in 'free right now' team presence. Never contains work details."""

    handle: str
    display_name: str
    free_for_min: int
    vibe: str  # "walk" | "coffee" | "voice" | "quiet_tea"
    location_hint: str = "nearby"
    expires_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RedirectSuggestion:
    """A calm, body-calibrated, environment-aware step-away prompt."""

    suggestion_id: str
    category: str  # "social" | "movement" | "hydration" | "daylight" | "restoration" | "nourishment"
    title: str
    subtitle: str
    duration_min: int
    outdoor: bool
    low_impact_note: str
    estimated_co2_saved_g: int
    whoop_rationale: str
    peer_handle: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HandoffSession:
    """Tracks one agent handoff -> step-away -> quiet completion loop."""

    session_id: str
    world: WorldMode
    adapter: str  # "corporate_manual" | "weaveos" | "webhook" | "local_process"
    state: AgentTaskState
    estimated_minutes: int
    started_at: str
    # Corporate privacy invariant: task_label is ALWAYS None in CORPORATE mode.
    task_label: str | None = None
    external_run_id: str | None = None
    chosen_suggestion_id: str | None = None
    chosen_category: str | None = None
    chosen_title: str | None = None
    outdoor: bool = False
    peer_handle: str | None = None
    estimated_co2_saved_g: int = 0
    reclaimed_minutes: int = 0
    completed_at: str | None = None
    quiet_brief: str | None = None
    self_reported_stress: int | None = None  # 1 (calm) - 5 (strained)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["world"] = self.world.value
        data["state"] = self.state.value
        return data


@dataclass(frozen=True)
class QuietBrief:
    """Anti-attention completion notice: 1-2 calm sentences, zero log spam."""

    session_id: str
    headline: str
    summary: str
    watch_card_text: str  # <= 36 chars for 1x1" charm / 2x2" watch
    spoken_brief: str  # Earbud voice brief
    reclaimed_minutes: int
    state: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
