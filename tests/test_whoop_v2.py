"""Tests for WHOOP Developer API v2 OAuth2 client, parsers, refresh rotation, and deletion webhooks."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from reclaimweave.service import ReclaimService
from reclaimweave.whoop import (
    WHOOP_AUTH_URL,
    WhoopOAuthConfig,
    WhoopV2Client,
    parse_whoop_v2_snapshot,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_parse_whoop_v2_snapshot_maps_recovery_cycle_and_sleep() -> None:
    snap = parse_whoop_v2_snapshot(
        recovery_record={
            "cycle_id": 98765,
            "score": {
                "recovery_score": 81.4,
                "resting_heart_rate": 52.0,
                "hrv_rmssd_milli": 79.35,
            },
        },
        cycle_record={"id": 98765, "score": {"strain": 8.64}},
        sleep_record={
            "score": {
                "sleep_performance_percentage": 91.0,
                "sleep_needed": {"need_from_sleep_debt_milli": 1_800_000},  # 30m
            }
        },
        minutes_since_movement=50,
    )
    assert snap.recovery_pct == 81
    assert snap.resting_hr_bpm == 52
    assert snap.hrv_ms == 79.3 or snap.hrv_ms == 79.4
    assert snap.strain == 8.6
    assert snap.sleep_performance_pct == 91
    assert snap.sleep_debt_min == 30
    assert snap.whoop_cycle_id == "98765"


def test_whoop_oauth_authorize_exchange_refresh_and_fetch(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_opener(req: Any, timeout: int = 15) -> _FakeResponse:
        del timeout
        url = req.full_url
        calls.append(url)
        if "/oauth/oauth2/token" in url:
            body = req.data.decode("utf-8")
            if "grant_type=authorization_code" in body:
                return _FakeResponse(
                    {
                        "access_token": "tok_initial",
                        "refresh_token": "ref_1",
                        "expires_in": 3600,
                    }
                )
            return _FakeResponse(
                {
                    "access_token": "tok_refreshed",
                    "refresh_token": "ref_2",
                    "expires_in": 3600,
                }
            )
        if "/v2/recovery" in url:
            return _FakeResponse(
                {"records": [{"cycle_id": 55, "score": {"recovery_score": 73, "resting_heart_rate": 54, "hrv_rmssd_milli": 66.2}}]}
            )
        if "/v2/cycle" in url:
            return _FakeResponse({"records": [{"id": 55, "score": {"strain": 7.1}}]})
        if "/v2/activity/sleep" in url:
            return _FakeResponse(
                {"records": [{"score": {"sleep_performance_percentage": 85, "sleep_needed": {"need_from_sleep_debt_milli": 600_000}}}]}
            )
        raise AssertionError(f"Unexpected URL: {url}")

    cfg = WhoopOAuthConfig(client_id="cid_test", client_secret="csec_test")
    token_path = tmp_path / "whoop_tokens.json"
    client = WhoopV2Client(cfg, token_path, http_opener=fake_opener)

    auth_url, state = client.build_authorize_url(state="deadbeef")
    assert auth_url.startswith(WHOOP_AUTH_URL)
    assert "read%3Arecovery" in auth_url
    assert "offline" in auth_url
    assert state == "deadbeef"

    client.exchange_code("code_123")
    assert token_path.is_file()
    assert (token_path.stat().st_mode & 0o777) == 0o600

    # Force expiry to verify serialized refresh rotation
    saved = json.loads(token_path.read_text(encoding="utf-8"))
    saved["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    token_path.write_text(json.dumps(saved), encoding="utf-8")

    snap = client.fetch_latest_snapshot()
    assert snap.recovery_pct == 73
    assert snap.strain == 7.1
    assert snap.sleep_debt_min == 10
    assert json.loads(token_path.read_text(encoding="utf-8"))["refresh_token"] == "ref_2"


def test_whoop_deletion_webhook_removes_stored_cycle(tmp_path: Path) -> None:
    svc = ReclaimService(tmp_path)
    snap = parse_whoop_v2_snapshot(
        recovery_record={"cycle_id": "cyc_del_1", "score": {"recovery_score": 65}},
        cycle_record={"id": "cyc_del_1", "score": {"strain": 8.0}},
    )
    svc.record_biometrics(snap)
    res = svc.handle_whoop_webhook({"type": "recovery.deleted", "id": "cyc_del_1"})
    assert res["deleted_records"] == 1
