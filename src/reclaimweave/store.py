"""Local-first SQLite WAL storage for ReclaimWeave.

Privacy-by-architecture guarantee:
- All personal biometric snapshots, handoffs, and ledger entries stay on the local device.
- In CORPORATE mode, `task_label` and `external_run_id` are forcibly stripped to `None`
  at the storage boundary so employer work details can never be persisted.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from reclaimweave.models import (
    AgentTaskState,
    BiometricSnapshot,
    HandoffSession,
    PeerPresence,
    WorldMode,
    utc_now_iso,
)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS biometric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    day_key TEXT NOT NULL,
    recovery_pct INTEGER NOT NULL,
    strain REAL NOT NULL,
    resting_hr_bpm INTEGER NOT NULL,
    hrv_ms REAL NOT NULL,
    sleep_performance_pct INTEGER NOT NULL,
    sleep_debt_min INTEGER NOT NULL,
    minutes_since_movement INTEGER NOT NULL,
    source TEXT NOT NULL,
    whoop_cycle_id TEXT
);

CREATE TABLE IF NOT EXISTS handoff_sessions (
    session_id TEXT PRIMARY KEY,
    world TEXT NOT NULL,
    adapter TEXT NOT NULL,
    state TEXT NOT NULL,
    estimated_minutes INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    day_key TEXT NOT NULL,
    task_label TEXT,
    external_run_id TEXT,
    chosen_suggestion_id TEXT,
    chosen_category TEXT,
    chosen_title TEXT,
    outdoor INTEGER NOT NULL DEFAULT 0,
    peer_handle TEXT,
    estimated_co2_saved_g INTEGER NOT NULL DEFAULT 0,
    reclaimed_minutes INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    quiet_brief TEXT,
    self_reported_stress INTEGER
);

CREATE TABLE IF NOT EXISTS peer_presence (
    handle TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    free_for_min INTEGER NOT NULL,
    vibe TEXT NOT NULL,
    location_hint TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_handle TEXT NOT NULL,
    vibe TEXT NOT NULL,
    session_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companion_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _day_key(iso_ts: str | None = None) -> str:
    if iso_ts and len(iso_ts) >= 10:
        return iso_ts[:10]
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class ReclaimStore:
    """Thread-safe local SQLite WAL store for Reclaim."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # --- Biometrics ---

    def save_biometric(self, snap: BiometricSnapshot) -> BiometricSnapshot:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO biometric_snapshots (
                    recorded_at, day_key, recovery_pct, strain, resting_hr_bpm,
                    hrv_ms, sleep_performance_pct, sleep_debt_min,
                    minutes_since_movement, source, whoop_cycle_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap.recorded_at,
                    _day_key(snap.recorded_at),
                    int(snap.recovery_pct),
                    float(snap.strain),
                    int(snap.resting_hr_bpm),
                    float(snap.hrv_ms),
                    int(snap.sleep_performance_pct),
                    int(snap.sleep_debt_min),
                    int(snap.minutes_since_movement),
                    snap.source,
                    snap.whoop_cycle_id,
                ),
            )
        return snap

    def latest_biometric(self) -> BiometricSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM biometric_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return self._row_to_biometric(row)

    def delete_whoop_cycle(self, whoop_cycle_id: str) -> int:
        """Honor WHOOP recovery.deleted / sleep.deleted webhooks cleanly."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM biometric_snapshots WHERE whoop_cycle_id = ?",
                (whoop_cycle_id,),
            )
            return cur.rowcount

    def list_biometrics_since(self, days: int = 30) -> list[BiometricSnapshot]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM biometric_snapshots WHERE day_key >= ? ORDER BY id ASC",
                (cutoff,),
            ).fetchall()
        return [self._row_to_biometric(r) for r in rows]

    @staticmethod
    def _row_to_biometric(row: sqlite3.Row) -> BiometricSnapshot:
        return BiometricSnapshot(
            recovery_pct=int(row["recovery_pct"]),
            strain=float(row["strain"]),
            resting_hr_bpm=int(row["resting_hr_bpm"]),
            hrv_ms=float(row["hrv_ms"]),
            sleep_performance_pct=int(row["sleep_performance_pct"]),
            sleep_debt_min=int(row["sleep_debt_min"]),
            minutes_since_movement=int(row["minutes_since_movement"]),
            source=str(row["source"]),
            recorded_at=str(row["recorded_at"]),
            whoop_cycle_id=row["whoop_cycle_id"],
        )

    # --- Handoff Sessions ---

    def save_session(self, session: HandoffSession) -> HandoffSession:
        # Privacy-by-architecture hard guard: corporate mode NEVER stores work metadata.
        if session.world == WorldMode.CORPORATE:
            session.task_label = None
            session.external_run_id = None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO handoff_sessions (
                    session_id, world, adapter, state, estimated_minutes, started_at, day_key,
                    task_label, external_run_id, chosen_suggestion_id, chosen_category,
                    chosen_title, outdoor, peer_handle, estimated_co2_saved_g,
                    reclaimed_minutes, completed_at, quiet_brief, self_reported_stress
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    state=excluded.state,
                    chosen_suggestion_id=excluded.chosen_suggestion_id,
                    chosen_category=excluded.chosen_category,
                    chosen_title=excluded.chosen_title,
                    outdoor=excluded.outdoor,
                    peer_handle=excluded.peer_handle,
                    estimated_co2_saved_g=excluded.estimated_co2_saved_g,
                    reclaimed_minutes=excluded.reclaimed_minutes,
                    completed_at=excluded.completed_at,
                    quiet_brief=excluded.quiet_brief,
                    self_reported_stress=excluded.self_reported_stress
                """,
                (
                    session.session_id,
                    session.world.value,
                    session.adapter,
                    session.state.value,
                    int(session.estimated_minutes),
                    session.started_at,
                    _day_key(session.started_at),
                    session.task_label,
                    session.external_run_id,
                    session.chosen_suggestion_id,
                    session.chosen_category,
                    session.chosen_title,
                    1 if session.outdoor else 0,
                    session.peer_handle,
                    int(session.estimated_co2_saved_g),
                    int(session.reclaimed_minutes),
                    session.completed_at,
                    session.quiet_brief,
                    session.self_reported_stress,
                ),
            )
        return session

    def get_session(self, session_id: str) -> HandoffSession | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM handoff_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def get_active_session(self) -> HandoffSession | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM handoff_sessions
                WHERE state IN ('running', 'waiting')
                ORDER BY started_at DESC LIMIT 1
                """
            ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, *, days: int = 30, limit: int = 200) -> list[HandoffSession]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM handoff_sessions
                WHERE day_key >= ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> HandoffSession:
        return HandoffSession(
            session_id=str(row["session_id"]),
            world=WorldMode(row["world"]),
            adapter=str(row["adapter"]),
            state=AgentTaskState(row["state"]),
            estimated_minutes=int(row["estimated_minutes"]),
            started_at=str(row["started_at"]),
            task_label=row["task_label"],
            external_run_id=row["external_run_id"],
            chosen_suggestion_id=row["chosen_suggestion_id"],
            chosen_category=row["chosen_category"],
            chosen_title=row["chosen_title"],
            outdoor=bool(row["outdoor"]),
            peer_handle=row["peer_handle"],
            estimated_co2_saved_g=int(row["estimated_co2_saved_g"]),
            reclaimed_minutes=int(row["reclaimed_minutes"]),
            completed_at=row["completed_at"],
            quiet_brief=row["quiet_brief"],
            self_reported_stress=row["self_reported_stress"],
        )

    # --- Ephemeral Team Presence ---

    def upsert_peer(self, peer: PeerPresence) -> PeerPresence:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO peer_presence (
                    handle, display_name, free_for_min, vibe, location_hint, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(handle) DO UPDATE SET
                    display_name=excluded.display_name,
                    free_for_min=excluded.free_for_min,
                    vibe=excluded.vibe,
                    location_hint=excluded.location_hint,
                    expires_at=excluded.expires_at
                """,
                (
                    peer.handle,
                    peer.display_name,
                    int(peer.free_for_min),
                    peer.vibe,
                    peer.location_hint,
                    peer.expires_at,
                ),
            )
        return peer

    def list_active_peers(self, *, now_iso: str | None = None) -> list[PeerPresence]:
        now = now_iso or utc_now_iso()
        with self._connect() as conn:
            conn.execute("DELETE FROM peer_presence WHERE expires_at <= ?", (now,))
            rows = conn.execute(
                "SELECT * FROM peer_presence WHERE expires_at > ? ORDER BY free_for_min DESC",
                (now,),
            ).fetchall()
        return [
            PeerPresence(
                handle=str(r["handle"]),
                display_name=str(r["display_name"]),
                free_for_min=int(r["free_for_min"]),
                vibe=str(r["vibe"]),
                location_hint=str(r["location_hint"]),
                expires_at=str(r["expires_at"]),
            )
            for r in rows
        ]

    def record_social_connection(
        self, peer_handle: str, vibe: str, session_id: str | None = None
    ) -> dict[str, Any]:
        ts = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO social_connections (peer_handle, vibe, session_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (peer_handle, vibe, session_id, ts),
            )
        return {
            "peer_handle": peer_handle,
            "vibe": vibe,
            "session_id": session_id,
            "connected_at": ts,
        }

    # --- Key-Value Metadata (e.g., anti-fatigue cooldown timestamps) ---

    def set_meta(self, key: str, value: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO companion_meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, json.dumps(value)),
            )

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM companion_meta WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])
