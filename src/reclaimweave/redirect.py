"""Anti-Attention Redirect Engine.

Generates at most 2-3 calm, body-calibrated, socially-aware, and environment-first
suggestions when a user hands work to an AI agent.

Design invariants:
1. Low WHOOP recovery (<34% red) -> restorative, gentle options (hydration, window daylight,
   quiet breathwork, unhurried tea with a free colleague).
2. Moderate recovery (34-66% yellow) -> 10m outdoor walk, stretch, quick walk-and-talk.
3. High recovery (>=67% green) & spare strain -> brisk 15-25m outdoor walk, real movement,
   outdoor conversation, or cooking a real meal.
4. Social-first: when a teammate is also free right now, surface human connection first.
5. Environment-first: bias toward outdoor, zero-carbon actions and log avoided footprint.
6. Anti-fatigue: enforce a quiet cooldown if the user just dismissed or received a prompt.
"""

from __future__ import annotations

from datetime import datetime, timezone

from reclaimweave.models import (
    BiometricSnapshot,
    PeerPresence,
    RecoveryZone,
    RedirectSuggestion,
)

# Typical avoided carbon when stepping out on foot / resting screen vs idle high-power display
# or taking a short errand drive (~180g CO2/km car trip avoided or screen idle wattage).
_OUTDOOR_WALK_CO2_G = 140
_SHORT_DAYLIGHT_CO2_G = 35
_HOME_COOK_CO2_G = 280


def is_in_fatigue_cooldown(
    last_prompted_iso: str | None,
    *,
    cooldown_minutes: int = 12,
    now: datetime | None = None,
) -> bool:
    """Return True if a prompt was shown too recently (prevents nagging)."""
    if not last_prompted_iso:
        return False
    try:
        prev = datetime.fromisoformat(last_prompted_iso)
    except ValueError:
        return False
    current = now or datetime.now(timezone.utc)
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=timezone.utc)
    delta_min = (current - prev).total_seconds() / 60.0
    return 0.0 <= delta_min < cooldown_minutes


def recommend_redirects(
    *,
    biometrics: BiometricSnapshot,
    free_window_min: int,
    free_peers: list[PeerPresence] | None = None,
    local_hour: int | None = None,
    max_options: int = 3,
) -> list[RedirectSuggestion]:
    """Select 2-3 calm step-away suggestions matched to WHOOP readiness and free teammates."""
    window = max(3, min(120, int(free_window_min)))
    peers = free_peers or []
    hour = local_hour if local_hour is not None else datetime.now(timezone.utc).hour
    is_daylight = 6 <= hour <= 18
    zone = biometrics.zone
    suggestions: list[RedirectSuggestion] = []

    # 1. Social-first mechanic: connection is the core lever when a teammate is free right now.
    if peers:
        best_peer = peers[0]
        shared_min = max(5, min(window, best_peer.free_for_min, 20))
        if zone == RecoveryZone.RED:
            suggestions.append(
                RedirectSuggestion(
                    suggestion_id=f"social_quiet_{best_peer.handle}",
                    category="social",
                    title=f"Quiet tea or voice catch-up with {best_peer.display_name}",
                    subtitle=(
                        f"{best_peer.display_name} also handed work to their agent "
                        f"(~{best_peer.free_for_min}m free, {best_peer.location_hint}). "
                        "Keep it easy — no work talk required."
                    ),
                    duration_min=shared_min,
                    outdoor=False,
                    low_impact_note="Zero-screen human connection; low nervous-system load.",
                    estimated_co2_saved_g=25,
                    whoop_rationale=(
                        f"Recovery is {biometrics.recovery_pct}% (red) — "
                        "social warmth without physical strain."
                    ),
                    peer_handle=best_peer.handle,
                )
            )
        else:
            outdoor_walk = best_peer.vibe in ("walk", "coffee") and is_daylight
            verb = "Walk & talk" if outdoor_walk else "Quick unhurried chat"
            suggestions.append(
                RedirectSuggestion(
                    suggestion_id=f"social_walk_{best_peer.handle}",
                    category="social",
                    title=f"{verb} with {best_peer.display_name}",
                    subtitle=(
                        f"{best_peer.display_name} is free right now (~{best_peer.free_for_min}m, "
                        f"{best_peer.location_hint}). Step away together while your agents run."
                    ),
                    duration_min=shared_min,
                    outdoor=outdoor_walk,
                    low_impact_note="On foot & outdoors — zero transit footprint."
                    if outdoor_walk
                    else "Screen off, real human conversation.",
                    estimated_co2_saved_g=_OUTDOOR_WALK_CO2_G if outdoor_walk else 30,
                    whoop_rationale=(
                        f"Recovery {biometrics.recovery_pct}% ({zone.value}) with "
                        f"{biometrics.spare_strain_capacity} spare strain — ideal for moving conversation."
                    ),
                    peer_handle=best_peer.handle,
                )
            )

    # 2. Biometric-calibrated body option (Red vs Yellow vs Green)
    if zone == RecoveryZone.RED:
        suggestions.append(
            RedirectSuggestion(
                suggestion_id="red_daylight_breath",
                category="daylight" if is_daylight else "restoration",
                title="5 minutes of natural daylight & slow exhale"
                if is_daylight
                else "5 minutes eyes-off-screen & quiet breathing",
                subtitle=(
                    "Step onto a porch, balcony, or open window. Look at the horizon and let your "
                    "eyes soften off near-focus glass."
                ),
                duration_min=min(window, 7),
                outdoor=is_daylight,
                low_impact_note="Stepping outside costs nothing and lowers screen load.",
                estimated_co2_saved_g=_SHORT_DAYLIGHT_CO2_G,
                whoop_rationale=(
                    f"Recovery is {biometrics.recovery_pct}% with {biometrics.sleep_debt_min}m "
                    "sleep debt — gentle nervous-system downshift beats pushing strain."
                ),
            )
        )
        suggestions.append(
            RedirectSuggestion(
                suggestion_id="red_hydrate_stretch",
                category="hydration",
                title="Full glass of water & gentle neck/shoulder release",
                subtitle="Walk to the kitchen slowly, drink 350ml water, and roll your shoulders.",
                duration_min=min(window, 5),
                outdoor=False,
                low_impact_note="Tap water, zero packaging, zero screen.",
                estimated_co2_saved_g=20,
                whoop_rationale=(
                    f"Resting HR {biometrics.resting_hr_bpm} bpm / HRV {biometrics.hrv_ms} ms — "
                    "hydration supports recovery without adding cardiovascular strain."
                ),
            )
        )
    elif zone == RecoveryZone.YELLOW:
        walk_min = min(window, 12)
        suggestions.append(
            RedirectSuggestion(
                suggestion_id="yellow_outdoor_walk",
                category="movement",
                title=f"{walk_min}-minute easy outdoor walk",
                subtitle=(
                    "Leave the phone in your pocket. Walk around the block at an easy conversational pace."
                ),
                duration_min=walk_min,
                outdoor=True,
                low_impact_note="Walk outdoors instead of scrolling indoors.",
                estimated_co2_saved_g=_OUTDOOR_WALK_CO2_G,
                whoop_rationale=(
                    f"Recovery {biometrics.recovery_pct}% (yellow) and "
                    f"{biometrics.minutes_since_movement}m since last movement — steady Zone 1 movement."
                ),
            )
        )
        suggestions.append(
            RedirectSuggestion(
                suggestion_id="yellow_hydrate_daylight",
                category="hydration",
                title="Water, posture reset & 3 minutes of sky",
                subtitle="Refill your glass, stand tall, and step outside for fresh air.",
                duration_min=min(window, 6),
                outdoor=is_daylight,
                low_impact_note="Fresh air and tap water; monitor sleeps while you step away.",
                estimated_co2_saved_g=_SHORT_DAYLIGHT_CO2_G,
                whoop_rationale=(
                    f"Sleep performance {biometrics.sleep_performance_pct}% — "
                    "light movement and hydration steady afternoon energy."
                ),
            )
        )
    else:  # GREEN recovery
        if biometrics.spare_strain_capacity >= 3.0 and window >= 10:
            walk_min = min(window, 22)
            suggestions.append(
                RedirectSuggestion(
                    suggestion_id="green_brisk_walk",
                    category="movement",
                    title=f"{walk_min}-minute brisk outdoor walk",
                    subtitle=(
                        "Your body has headroom today. Pick a tree-lined route, swing your arms, "
                        "and let the agent finish without you watching."
                    ),
                    duration_min=walk_min,
                    outdoor=True,
                    low_impact_note="On foot, outdoors — zero-emission movement.",
                    estimated_co2_saved_g=_OUTDOOR_WALK_CO2_G,
                    whoop_rationale=(
                        f"Green recovery ({biometrics.recovery_pct}%) with "
                        f"{biometrics.spare_strain_capacity} strain capacity remaining."
                    ),
                )
            )
        else:
            suggestions.append(
                RedirectSuggestion(
                    suggestion_id="green_daylight_walk",
                    category="daylight",
                    title=f"{min(window, 10)}-minute sunlight loop outside",
                    subtitle="Brief walk outside to reset focus and clock daylight.",
                    duration_min=min(window, 10),
                    outdoor=True,
                    low_impact_note="Zero-carbon reset in natural daylight.",
                    estimated_co2_saved_g=_SHORT_DAYLIGHT_CO2_G,
                    whoop_rationale=(
                        f"Green recovery ({biometrics.recovery_pct}%) — keep momentum gentle since "
                        f"day strain is already {biometrics.strain}."
                    ),
                )
            )

        if window >= 15:
            suggestions.append(
                RedirectSuggestion(
                    suggestion_id="green_cook_real_food",
                    category="nourishment",
                    title="Prep real food or tea in the kitchen",
                    subtitle=(
                        "Use the agent window to chop vegetables, make a proper lunch, or brew loose tea "
                        "instead of ordering delivery."
                    ),
                    duration_min=min(window, 20),
                    outdoor=False,
                    low_impact_note="Cooking at home avoids delivery packaging & courier emissions.",
                    estimated_co2_saved_g=_HOME_COOK_CO2_G,
                    whoop_rationale=(
                        f"Green recovery ({biometrics.recovery_pct}%) — fuel the rest of your day well."
                    ),
                )
            )
        else:
            suggestions.append(
                RedirectSuggestion(
                    suggestion_id="green_water_mobility",
                    category="hydration",
                    title="Drink water & 5-minute hip/spine mobility",
                    subtitle=(
                        f"It has been {biometrics.minutes_since_movement} minutes since you moved. "
                        "Hydrate and open your hips away from the desk."
                    ),
                    duration_min=min(window, 6),
                    outdoor=False,
                    low_impact_note="Zero equipment, screen off.",
                    estimated_co2_saved_g=20,
                    whoop_rationale=(
                        f"HRV {biometrics.hrv_ms} ms / Recovery {biometrics.recovery_pct}% — "
                        "quick mobility keeps your body loose."
                    ),
                )
            )

    # Always cap at 2-3 calm choices (anti-attention discipline)
    return suggestions[: max(2, min(3, max_options))]
