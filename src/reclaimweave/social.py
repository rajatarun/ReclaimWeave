"""Social Layer — 'Who is free right now?' team presence coordinator.

Connection is a core health mechanic, not a side feature.
Privacy invariant: presence shares ONLY `{handle, display_name, free_for_min, vibe, location_hint}`
with a strict TTL — never employer names, codebases, prompts, or task descriptions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from reclaimweave.models import PeerPresence
from reclaimweave.store import ReclaimStore

_ALLOWED_VIBES = frozenset({"walk", "coffee", "voice", "quiet_tea"})


class SocialCoordinator:
    def __init__(self, store: ReclaimStore) -> None:
        self.store = store

    def announce_free(
        self,
        *,
        handle: str,
        display_name: str,
        free_for_min: int,
        vibe: str = "walk",
        location_hint: str = "nearby",
    ) -> PeerPresence:
        norm_vibe = vibe if vibe in _ALLOWED_VIBES else "walk"
        minutes = max(3, min(90, int(free_for_min)))
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        peer = PeerPresence(
            handle=handle.strip().lower().replace(" ", "_"),
            display_name=display_name.strip() or handle.strip(),
            free_for_min=minutes,
            vibe=norm_vibe,
            location_hint=location_hint.strip() or "nearby",
            expires_at=expires_at,
        )
        return self.store.upsert_peer(peer)

    def who_is_free(self, *, exclude_handle: str | None = None) -> list[PeerPresence]:
        peers = self.store.list_active_peers()
        if exclude_handle:
            norm = exclude_handle.strip().lower()
            peers = [p for p in peers if p.handle != norm]
        return peers

    def connect_with_peer(
        self,
        *,
        peer_handle: str,
        vibe: str = "walk",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        norm_vibe = vibe if vibe in _ALLOWED_VIBES else "walk"
        return self.store.record_social_connection(
            peer_handle=peer_handle.strip().lower(),
            vibe=norm_vibe,
            session_id=session_id,
        )
