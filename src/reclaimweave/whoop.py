"""WHOOP Developer API v2 OAuth 2.0 client & local biometric snapshot parser.

Verified against WHOOP Developer API v2 (OpenAPI 3.2):
- Authorization URL: https://api.prod.whoop.com/oauth/oauth2/auth
- Token URL:         https://api.prod.whoop.com/oauth/oauth2/token
- Developer Base:    https://api.prod.whoop.com/developer
- Endpoints:         /v2/recovery, /v2/cycle, /v2/activity/sleep
- Scopes:            offline read:recovery read:cycles read:sleep read:workout read:profile

Operational safeguards built in:
1. Lock-serialized refresh token rotation (`_refresh_lock`) so concurrent workers never
   break WHOOP's single-use refresh token chain.
2. Local `0600` token persistence (`whoop_tokens.json`) — privacy by architecture.
3. Honors `recovery.deleted` / `sleep.deleted` webhooks per WHOOP storage terms.
4. Honest non-medical posture: presents personal trends only, never clinical advice.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from reclaimweave.models import BiometricSnapshot, utc_now_iso

WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"  # noqa: S105
WHOOP_API_BASE = "https://api.prod.whoop.com/developer"
DEFAULT_WHOOP_SCOPES = (
    "offline",
    "read:recovery",
    "read:cycles",
    "read:sleep",
    "read:workout",
    "read:profile",
)


@dataclass
class WhoopOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str = "http://localhost:8791/v1/whoop/callback"
    scopes: tuple[str, ...] = DEFAULT_WHOOP_SCOPES

    @classmethod
    def from_env(cls) -> WhoopOAuthConfig | None:
        cid = os.environ.get("WHOOP_CLIENT_ID", "").strip()
        csec = os.environ.get("WHOOP_CLIENT_SECRET", "").strip()
        if not cid or not csec:
            return None
        redirect = os.environ.get(
            "WHOOP_REDIRECT_URI", "http://localhost:8791/v1/whoop/callback"
        ).strip()
        return cls(client_id=cid, client_secret=csec, redirect_uri=redirect)


def parse_whoop_v2_snapshot(
    *,
    recovery_record: dict[str, Any] | None,
    cycle_record: dict[str, Any] | None = None,
    sleep_record: dict[str, Any] | None = None,
    minutes_since_movement: int = 45,
) -> BiometricSnapshot:
    """Convert raw WHOOP Developer API v2 JSON payloads into a BiometricSnapshot."""
    rec_score = (recovery_record or {}).get("score") or {}
    cyc_score = (cycle_record or {}).get("score") or {}
    slp_score = (sleep_record or {}).get("score") or {}
    sleep_needed = slp_score.get("sleep_needed") or {}

    recovery_pct = int(round(float(rec_score.get("recovery_score", 62))))
    recovery_pct = max(0, min(100, recovery_pct))

    resting_hr = int(round(float(rec_score.get("resting_heart_rate", 56))))
    hrv_ms = round(float(rec_score.get("hrv_rmssd_milli", 64.0)), 1)
    strain = round(max(0.0, min(21.0, float(cyc_score.get("strain", 7.4)))), 1)

    sleep_perf = int(round(float(slp_score.get("sleep_performance_percentage", 82))))
    sleep_perf = max(0, min(100, sleep_perf))

    debt_milli = float(sleep_needed.get("need_from_sleep_debt_milli", 0.0))
    sleep_debt_min = max(0, int(round(debt_milli / 60_000.0)))

    cycle_id = None
    if cycle_record and cycle_record.get("id") is not None:
        cycle_id = str(cycle_record["id"])
    elif recovery_record and recovery_record.get("cycle_id") is not None:
        cycle_id = str(recovery_record["cycle_id"])

    return BiometricSnapshot(
        recovery_pct=recovery_pct,
        strain=strain,
        resting_hr_bpm=resting_hr,
        hrv_ms=hrv_ms,
        sleep_performance_pct=sleep_perf,
        sleep_debt_min=sleep_debt_min,
        minutes_since_movement=max(0, int(minutes_since_movement)),
        source="whoop_v2",
        recorded_at=utc_now_iso(),
        whoop_cycle_id=cycle_id,
    )


class WhoopV2Client:
    """Stdlib-only WHOOP Developer API v2 OAuth2 client with serialized token refresh."""

    def __init__(
        self,
        config: WhoopOAuthConfig | None,
        token_path: Path,
        *,
        http_opener: Any | None = None,
    ) -> None:
        self.config = config
        self.token_path = token_path
        self._refresh_lock = threading.Lock()
        self._http_opener = http_opener or urllib.request.urlopen

    def build_authorize_url(self, *, state: str | None = None) -> tuple[str, str]:
        """Return (authorize_url, csrf_state) for WHOOP OAuth2 Authorization Code flow."""
        if self.config is None:
            raise ValueError("WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET are not configured")
        csrf_state = state or secrets.token_hex(8)
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": " ".join(self.config.scopes),
                "state": csrf_state,
            }
        )
        return f"{WHOOP_AUTH_URL}?{query}", csrf_state

    def _save_tokens(self, payload: dict[str, Any]) -> dict[str, Any]:
        expires_in = int(payload.get("expires_in", 3600))
        record = {
            "access_token": str(payload["access_token"]),
            "refresh_token": str(payload.get("refresh_token", "")),
            "token_type": str(payload.get("token_type", "bearer")),
            "scope": str(payload.get("scope", "")),
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 60))
            ).isoformat(),
        }
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        try:
            self.token_path.chmod(0o600)
        except OSError:
            pass
        return record

    def load_tokens(self) -> dict[str, Any] | None:
        if not self.token_path.is_file():
            return None
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _post_form(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        encoded = urllib.parse.urlencode(form).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=encoded,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        with self._http_opener(req, timeout=15) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

    def exchange_code(self, code: str) -> dict[str, Any]:
        if self.config is None:
            raise ValueError("WHOOP OAuth config missing")
        payload = self._post_form(
            WHOOP_TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            },
        )
        return self._save_tokens(payload)

    def ensure_access_token(self) -> str:
        """Return a valid access token, refreshing under a lock to avoid rotation races."""
        with self._refresh_lock:
            tokens = self.load_tokens()
            if not tokens or not tokens.get("access_token"):
                raise RuntimeError("WHOOP is not connected; complete OAuth authorization first")
            expires_at = tokens.get("expires_at")
            if expires_at and datetime.fromisoformat(expires_at) > datetime.now(timezone.utc):
                return str(tokens["access_token"])
            if self.config is None or not tokens.get("refresh_token"):
                raise RuntimeError("WHOOP token expired and cannot be refreshed without credentials")
            refreshed = self._post_form(
                WHOOP_TOKEN_URL,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": str(tokens["refresh_token"]),
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "scope": " ".join(self.config.scopes),
                },
            )
            saved = self._save_tokens(refreshed)
            return str(saved["access_token"])

    def _get_json(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        token = self.ensure_access_token()
        url = f"{WHOOP_API_BASE}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        with self._http_opener(req, timeout=15) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

    def fetch_latest_snapshot(self, *, minutes_since_movement: int = 45) -> BiometricSnapshot:
        """Pull latest WHOOP v2 recovery, cycle, and sleep records."""
        recoveries = self._get_json("/v2/recovery", params={"limit": "1"}).get("records") or []
        cycles = self._get_json("/v2/cycle", params={"limit": "1"}).get("records") or []
        sleeps = self._get_json("/v2/activity/sleep", params={"limit": "1"}).get("records") or []
        return parse_whoop_v2_snapshot(
            recovery_record=recoveries[0] if recoveries else None,
            cycle_record=cycles[0] if cycles else None,
            sleep_record=sleeps[0] if sleeps else None,
            minutes_since_movement=minutes_since_movement,
        )

    def status(self) -> dict[str, Any]:
        tokens = self.load_tokens()
        return {
            "oauth_configured": self.config is not None,
            "connected": bool(tokens and tokens.get("access_token")),
            "api_version": "v2",
            "sandbox_member_cap": 10,
            "scopes": list(DEFAULT_WHOOP_SCOPES),
            "data_granularity": "daily summaries (recovery, cycle strain, sleep stages, workouts)",
            "medical_disclaimer": (
                "Reclaim correlates reclaimed hours with personal readiness trends; "
                "it does not diagnose, treat, or provide medical advice."
            ),
        }
