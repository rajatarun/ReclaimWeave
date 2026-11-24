"""Quiet Completion Brief & Ambient Surface Formatter (Watch, Charm, Earbuds).

Anti-attention discipline:
- Never stream raw logs or progress bars.
- Emit ONE quiet 1-2 sentence completion brief when the agent finishes or is blocked.
- Also render a <=36-char card for 1x1" pocket charms / 2x2" watches and a calm spoken
  line for earbud audio briefs.
"""

from __future__ import annotations

from reclaimweave.models import AgentTaskState, HandoffSession, QuietBrief, WorldMode


def build_quiet_brief(
    session: HandoffSession,
    *,
    raw_outcome: str | None = None,
) -> QuietBrief:
    """Create a calm, concise completion notice tailored to Personal vs Corporate mode."""
    reclaimed = max(0, int(session.reclaimed_minutes))

    if session.state == AgentTaskState.BLOCKED:
        headline = "Agent paused for your input"
        if session.world == WorldMode.CORPORATE:
            summary = (
                f"Your agent needs a quick look when you are ready. "
                f"You reclaimed {reclaimed} min away from the screen."
            )
        else:
            detail = _clip_sentence(raw_outcome) or "It hit a decision point and paused cleanly."
            summary = f"{detail} You stepped away for {reclaimed} min."
        watch_text = f"Agent paused ({reclaimed}m away)"[:36]
        spoken = f"Your agent paused for your input. You reclaimed {reclaimed} minutes."
    else:
        headline = "Ready when you are"
        if session.world == WorldMode.CORPORATE:
            activity = session.chosen_title or "stepping away"
            summary = (
                f"Welcome back from {activity.lower()}. "
                f"You reclaimed {reclaimed} min of screen-free life."
            )
        else:
            detail = _clip_sentence(raw_outcome) or "Your delegated run finished cleanly."
            summary = f"{detail} You avoided {reclaimed} min of agent-watching."
        watch_text = f"Done. +{reclaimed}m reclaimed"[:36]
        spoken = f"Your agent finished. {summary}"

    return QuietBrief(
        session_id=session.session_id,
        headline=headline,
        summary=summary,
        watch_card_text=watch_text,
        spoken_brief=spoken,
        reclaimed_minutes=reclaimed,
        state=session.state.value,
    )


def _clip_sentence(text: str | None, max_len: int = 140) -> str:
    if not text:
        return ""
    clean = " ".join(text.strip().split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"
