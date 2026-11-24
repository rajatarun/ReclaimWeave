"""End-to-end API and Glanceable Companion UI tests for ReclaimWeave."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from reclaimweave.api import create_app


def test_full_companion_api_flow_and_ui(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"

        ui = client.get("/")
        assert ui.status_code == 200
        assert "Reclaim" in ui.text
        assert "I handed work to the agent" in ui.text

        # Seed week & free teammates
        seed = client.post("/v1/demo/seed")
        assert seed.status_code == 200
        assert len(seed.json()["free_peers"]) == 2

        # Corporate handoff
        hand = client.post(
            "/v1/handoff",
            json={
                "world": "corporate",
                "estimated_minutes": 15,
                "task": "MUST BE STRIPPED",
                "local_hour": 14,
            },
        )
        assert hand.status_code == 200
        hdata = hand.json()
        assert hdata["session"]["task_label"] is None
        assert len(hdata["suggestions"]) >= 2

        # Accept first suggestion
        sid = hdata["session"]["session_id"]
        sug = hdata["suggestions"][0]
        acc = client.post(
            f"/v1/handoff/{sid}/accept",
            json={
                "suggestion_id": sug["suggestion_id"],
                "category": sug["category"],
                "title": sug["title"],
                "outdoor": sug["outdoor"],
                "estimated_co2_saved_g": sug["estimated_co2_saved_g"],
                "peer_handle": sug.get("peer_handle"),
            },
        )
        assert acc.status_code == 200
        assert acc.json()["ambient_card"]["mode"] == "away"

        # Complete via quiet brief
        done = client.post(
            f"/v1/handoff/{sid}/complete",
            json={"state": "done", "reclaimed_minutes": 15, "self_reported_stress": 2},
        )
        assert done.status_code == 200
        assert done.json()["brief"]["reclaimed_minutes"] == 15

        # Ledger & ambient endpoints
        ledger = client.get("/v1/ledger")
        assert ledger.status_code == 200
        assert ledger.json()["totals"]["screen_time_avoided_min"] >= 365

        card = client.get("/v1/ambient/card")
        assert card.status_code == 200
        assert card.json()["mode"] == "ready"
