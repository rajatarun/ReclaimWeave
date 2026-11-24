"""CLI entrypoint for Reclaim (`reclaim`)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from reclaimweave.ledger import build_reclamation_ledger
from reclaimweave.models import AgentTaskState, BiometricSnapshot, WorldMode
from reclaimweave.service import ReclaimService


def _default_data_dir() -> Path:
    return Path(os.environ.get("RECLAIM_DATA_DIR", Path.home() / ".reclaimweave"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reclaim",
        description="Reclaim — The Agent Companion Layer (anti-attention, WHOOP-calibrated).",
    )
    parser.add_argument("--data-dir", type=Path, default=_default_data_dir())
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_hand = sub.add_parser("handoff", help="Signal that you handed work to an agent")
    p_hand.add_argument("--world", choices=["corporate", "personal"], default="corporate")
    p_hand.add_argument("--adapter", default=None)
    p_hand.add_argument("--minutes", type=int, default=15)
    p_hand.add_argument("--task", default=None, help="Personal mode only (ignored in corporate mode)")
    p_hand.add_argument("--json", action="store_true", dest="as_json")

    p_done = sub.add_parser("done", help="Complete active handoff and print quiet brief")
    p_done.add_argument("--session-id", default=None)
    p_done.add_argument("--reclaimed", type=int, default=None)
    p_done.add_argument("--stress", type=int, default=None, choices=[1, 2, 3, 4, 5])
    p_done.add_argument("--blocked", action="store_true")
    p_done.add_argument("--json", action="store_true", dest="as_json")

    p_whoop = sub.add_parser("whoop", help="Record or view WHOOP / local readiness snapshot")
    p_whoop.add_argument("--recovery", type=int, default=None)
    p_whoop.add_argument("--strain", type=float, default=6.5)
    p_whoop.add_argument("--rhr", type=int, default=55)
    p_whoop.add_argument("--hrv", type=float, default=68.0)
    p_whoop.add_argument("--sleep-debt", type=int, default=20)
    p_whoop.add_argument("--since-move", type=int, default=45)

    p_free = sub.add_parser("free", help="List or announce who is free right now")
    p_free.add_argument("--handle", default=None)
    p_free.add_argument("--name", default=None)
    p_free.add_argument("--minutes", type=int, default=15)
    p_free.add_argument("--vibe", choices=["walk", "coffee", "voice", "quiet_tea"], default="walk")
    p_free.add_argument("--where", default="nearby")

    p_ledger = sub.add_parser("ledger", help="Show the Reclaimed Life Ledger")
    p_ledger.add_argument("--days", type=int, default=14)
    p_ledger.add_argument("--json", action="store_true", dest="as_json")

    p_serve = sub.add_parser("serve", help="Run local Reclaim companion server & glanceable UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8791)
    p_serve.add_argument("--seed", action="store_true", help="Seed a realistic week & free teammates")

    args = parser.parse_args(argv)
    service = ReclaimService(args.data_dir)

    if args.cmd == "handoff":
        result = service.hand_off(
            world=WorldMode(args.world),
            adapter_name=args.adapter,
            estimated_minutes=args.minutes,
            task=args.task,
        )
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            bio = result["biometrics"]
            print(
                f"Handoff started ({result['session']['session_id']}) — "
                f"Recovery {bio['recovery_pct']}% ({bio['zone'].upper()})\n"
            )
            print("Step away for a moment:")
            for idx, s in enumerate(result["suggestions"], start=1):
                print(f"  {idx}. {s['title']} ({s['duration_min']}m) — {s['subtitle']}")
        return 0

    if args.cmd == "done":
        sid = args.session_id
        if not sid:
            active = service.store.get_active_session()
            if active is None:
                print("No active handoff session.")
                return 1
            sid = active.session_id
        res = service.complete_handoff(
            session_id=sid,
            state=AgentTaskState.BLOCKED if args.blocked else AgentTaskState.DONE,
            reclaimed_minutes=args.reclaimed,
            self_reported_stress=args.stress,
        )
        if args.as_json:
            print(json.dumps(res, indent=2))
        else:
            brief = res["brief"]
            print(f"{brief['headline']}\n{brief['summary']}")
        return 0

    if args.cmd == "whoop":
        if args.recovery is not None:
            snap = service.record_biometrics(
                BiometricSnapshot(
                    recovery_pct=args.recovery,
                    strain=args.strain,
                    resting_hr_bpm=args.rhr,
                    hrv_ms=args.hrv,
                    sleep_performance_pct=85,
                    sleep_debt_min=args.sleep_debt,
                    minutes_since_movement=args.since_move,
                    source="local_checkin",
                )
            )
            print(json.dumps(snap.to_dict(), indent=2))
        else:
            print(
                json.dumps(
                    {
                        "latest": service.current_biometrics().to_dict(),
                        "whoop_api": service.whoop.status(),
                    },
                    indent=2,
                )
            )
        return 0

    if args.cmd == "free":
        if args.handle:
            peer = service.social.announce_free(
                handle=args.handle,
                display_name=args.name or args.handle,
                free_for_min=args.minutes,
                vibe=args.vibe,
                location_hint=args.where,
            )
            print(json.dumps(peer.to_dict(), indent=2))
        else:
            print(json.dumps([p.to_dict() for p in service.social.who_is_free()], indent=2))
        return 0

    if args.cmd == "ledger":
        led = build_reclamation_ledger(service.store, days=args.days)
        if args.as_json:
            print(json.dumps(led, indent=2))
        else:
            t = led["totals"]
            print(
                f"Reclaimed Life Ledger ({args.days}d)\n"
                f"  Hours handed to agents : {t['hours_handed_to_agents']}h\n"
                f"  Hours reclaimed        : {t['hours_reclaimed']}h ({t['reclamation_pct']}%)\n"
                f"  Screen time avoided    : {t['screen_time_avoided_min']} min\n"
                f"  Outdoor time           : {t['outdoor_minutes']} min\n"
                f"  Human conversations    : {t['human_conversations']}\n"
                f"  Estimated CO2 avoided  : {t['co2_avoided_g']} g\n"
                f"  Note: {led['associations']['honest_note']}"
            )
        return 0

    if args.cmd == "serve":
        import uvicorn

        from reclaimweave.api import create_app

        if args.seed:
            service.seed_realistic_week()
        os.environ["RECLAIM_DATA_DIR"] = str(args.data_dir)
        uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
