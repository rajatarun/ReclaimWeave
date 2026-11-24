"""Core loop tests: WHOOP-calibrated redirects, social-first matching, anti-fatigue, ledger & quiet brief."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from reclaimweave import (
    AgentTaskState,
    BiometricSnapshot,
    ReclaimService,
    RecoveryZone,
    WorldMode,
    build_reclamation_ledger,
    is_in_fatigue_cooldown,
    recommend_redirects,
)


def test_redirects_adapt_to_red_vs_green_whoop_recovery() -> None:
    red_bio = BiometricSnapshot(
        recovery_pct=24,
        strain=9.0,
        resting_hr_bpm=62,
        hrv_ms=39.0,
        sleep_performance_pct=61,
        sleep_debt_min=75,
        minutes_since_movement=70,
    )
    assert red_bio.zone == RecoveryZone.RED
    red_options = recommend_redirects(biometrics=red_bio, free_window_min=20, local_hour=14)
    assert 2 <= len(red_options) <= 3
    assert all("brisk" not in s.title.lower() for s in red_options)
    assert any(s.category in ("daylight", "restoration", "hydration") for s in red_options)

    green_bio = BiometricSnapshot(
        recovery_pct=86,
        strain=5.2,
        resting_hr_bpm=50,
        hrv_ms=85.0,
        sleep_performance_pct=93,
        sleep_debt_min=5,
        minutes_since_movement=65,
    )
    assert green_bio.zone == RecoveryZone.GREEN
    green_options = recommend_redirects(biometrics=green_bio, free_window_min=20, local_hour=14)
    assert 2 <= len(green_options) <= 3
    assert any("brisk outdoor walk" in s.title.lower() and s.outdoor for s in green_options)


def test_social_first_surfaces_free_teammate_and_anti_fatigue_cooldown(tmp_path: Path) -> None:
    svc = ReclaimService(tmp_path)
    svc.social.announce_free(
        handle="maya",
        display_name="Maya Chen",
        free_for_min=18,
        vibe="walk",
        location_hint="Courtyard",
    )

    first = svc.hand_off(
        world=WorldMode.CORPORATE,
        estimated_minutes=15,
        local_hour=13,
        respect_cooldown=True,
    )
    assert first["cooldown_active"] is False
    assert first["suggestions"][0]["category"] == "social"
    assert first["suggestions"][0]["peer_handle"] == "maya"

    # Immediate second prompt respects anti-fatigue cooldown
    second = svc.hand_off(
        world=WorldMode.CORPORATE,
        estimated_minutes=15,
        local_hour=13,
        respect_cooldown=True,
    )
    assert second["cooldown_active"] is True
    assert second["suggestions"] == []

    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat()
    assert is_in_fatigue_cooldown(old_ts, cooldown_minutes=12) is False


def test_ledger_honest_correlation_and_quiet_brief_constraints(tmp_path: Path) -> None:
    svc = ReclaimService(tmp_path)
    svc.seed_realistic_week()

    handoff = svc.hand_off(world=WorldMode.CORPORATE, estimated_minutes=20, local_hour=11)
    sid = handoff["session"]["session_id"]
    top = handoff["suggestions"][0]
    svc.accept_suggestion(
        session_id=sid,
        suggestion_id=top["suggestion_id"],
        category=top["category"],
        title=top["title"],
        outdoor=top["outdoor"],
        estimated_co2_saved_g=top["estimated_co2_saved_g"],
        peer_handle=top.get("peer_handle"),
    )

    done = svc.complete_handoff(
        session_id=sid,
        state=AgentTaskState.DONE,
        reclaimed_minutes=20,
        self_reported_stress=1,
    )
    brief = done["brief"]
    assert len(brief["watch_card_text"]) <= 36
    assert "20" in brief["summary"]

    ledger = build_reclamation_ledger(svc.store, days=14)
    assert ledger["totals"]["screen_time_avoided_min"] >= 370
    assert ledger["totals"]["human_conversations"] >= 4
    assert ledger["totals"]["co2_avoided_g"] > 0
    assert ledger["associations"]["paired_days"] >= 6
    assert ledger["associations"]["reclaimed_vs_recovery_r"] is not None
    assert "not causal proof" in ledger["associations"]["honest_note"].lower()
