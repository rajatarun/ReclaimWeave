"""The Reclamation Ledger — WHOOP-style daily readiness view for reclaimed life.

Measures the explicit trade:
  hours handed to agents  vs.  hours reclaimed  ->  how your body responded.

Honest Measurement Principle:
All correlations between reclaimed time and WHOOP metrics (recovery, resting HR,
sleep, self-reported stress) are explicitly labeled as observational personal
trends — NEVER causal proof or medical advice.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from reclaimweave.models import BiometricSnapshot, HandoffSession
from reclaimweave.store import ReclaimStore

HONEST_MEASUREMENT_NOTE = (
    "Observed personal association across your logged days — suggestive, not causal proof "
    "and not medical advice."
)


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0.0 or den_y == 0.0:
        return None
    return round(num / (den_x * den_y), 2)


def build_reclamation_ledger(store: ReclaimStore, *, days: int = 14) -> dict[str, Any]:
    """Build today's readiness summary and multi-day reclaimed-life ledger."""
    sessions = store.list_sessions(days=days, limit=500)
    biometrics = store.list_biometrics_since(days=days)
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sessions_by_day: dict[str, list[HandoffSession]] = defaultdict(list)
    for s in sessions:
        sessions_by_day[s.started_at[:10]].append(s)

    bio_by_day: dict[str, BiometricSnapshot] = {}
    for b in biometrics:
        bio_by_day[b.recorded_at[:10]] = b  # latest snapshot per day

    all_days = sorted(set(sessions_by_day.keys()) | set(bio_by_day.keys()))
    daily_rows: list[dict[str, Any]] = []
    reclaimed_series: list[float] = []
    recovery_series: list[float] = []
    rhr_series: list[float] = []

    for day in all_days:
        day_sessions = sessions_by_day.get(day, [])
        handed_min = sum(s.estimated_minutes for s in day_sessions)
        reclaimed_min = sum(s.reclaimed_minutes for s in day_sessions)
        outdoor_min = sum(s.reclaimed_minutes for s in day_sessions if s.outdoor)
        conversations = sum(1 for s in day_sessions if s.peer_handle)
        water_breaks = sum(1 for s in day_sessions if s.chosen_category == "hydration")
        co2_g = sum(s.estimated_co2_saved_g for s in day_sessions)
        stress_vals = [s.self_reported_stress for s in day_sessions if s.self_reported_stress]
        avg_stress = round(sum(stress_vals) / len(stress_vals), 1) if stress_vals else None

        bio = bio_by_day.get(day)
        if bio is not None and reclaimed_min > 0:
            reclaimed_series.append(float(reclaimed_min))
            recovery_series.append(float(bio.recovery_pct))
            rhr_series.append(float(bio.resting_hr_bpm))

        daily_rows.append(
            {
                "date": day,
                "handoffs": len(day_sessions),
                "minutes_handed_to_agents": handed_min,
                "minutes_reclaimed": reclaimed_min,
                "screen_time_avoided_min": reclaimed_min,
                "outdoor_minutes": outdoor_min,
                "human_conversations": conversations,
                "water_breaks": water_breaks,
                "co2_avoided_g": co2_g,
                "avg_self_reported_stress": avg_stress,
                "recovery_pct": bio.recovery_pct if bio else None,
                "resting_hr_bpm": bio.resting_hr_bpm if bio else None,
                "hrv_ms": bio.hrv_ms if bio else None,
                "sleep_performance_pct": bio.sleep_performance_pct if bio else None,
            }
        )

    today_row = next((r for r in daily_rows if r["date"] == today_key), None) or {
        "date": today_key,
        "handoffs": 0,
        "minutes_handed_to_agents": 0,
        "minutes_reclaimed": 0,
        "screen_time_avoided_min": 0,
        "outdoor_minutes": 0,
        "human_conversations": 0,
        "water_breaks": 0,
        "co2_avoided_g": 0,
        "avg_self_reported_stress": None,
        "recovery_pct": bio_by_day[today_key].recovery_pct if today_key in bio_by_day else None,
        "resting_hr_bpm": bio_by_day[today_key].resting_hr_bpm if today_key in bio_by_day else None,
        "hrv_ms": bio_by_day[today_key].hrv_ms if today_key in bio_by_day else None,
        "sleep_performance_pct": (
            bio_by_day[today_key].sleep_performance_pct if today_key in bio_by_day else None
        ),
    }

    total_handed_min = sum(r["minutes_handed_to_agents"] for r in daily_rows)
    total_reclaimed_min = sum(r["minutes_reclaimed"] for r in daily_rows)
    reclamation_pct = (
        round((total_reclaimed_min / total_handed_min) * 100) if total_handed_min > 0 else 100
    )

    return {
        "window_days": days,
        "today": today_row,
        "totals": {
            "hours_handed_to_agents": round(total_handed_min / 60.0, 2),
            "hours_reclaimed": round(total_reclaimed_min / 60.0, 2),
            "screen_time_avoided_min": total_reclaimed_min,
            "reclamation_pct": min(100, reclamation_pct),
            "outdoor_minutes": sum(r["outdoor_minutes"] for r in daily_rows),
            "human_conversations": sum(r["human_conversations"] for r in daily_rows),
            "co2_avoided_g": sum(r["co2_avoided_g"] for r in daily_rows),
        },
        "associations": {
            "reclaimed_vs_recovery_r": _pearson_r(reclaimed_series, recovery_series),
            "reclaimed_vs_resting_hr_r": _pearson_r(reclaimed_series, rhr_series),
            "paired_days": len(reclaimed_series),
            "honest_note": HONEST_MEASUREMENT_NOTE,
        },
        "daily": daily_rows[-days:],
    }
