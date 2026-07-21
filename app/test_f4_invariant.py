"""WS-D safety test: the F4 receipt gate is UNBYPASSABLE from the client, and the
server binds loopback-only. Run: .venv-gemma/bin/python -m pytest app/test_f4_invariant.py -v

These are the two invariants a demo must never violate:
  1. No receipt while any flagged field is unconfirmed (server refuses with 409).
  2. The gate opens ONLY after the human-confirmation event lands.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.server import HOST, app  # noqa: E402

client = TestClient(app)


def _new_session() -> str:
    return client.post("/api/session").json()["session_id"]


def test_receipt_refused_while_flag_unconfirmed():
    sid = _new_session()
    r = client.get(f"/api/session/{sid}/receipt")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"] == "gate_blocked"
    assert "d3.rows[0].units" in body["unconfirmed"]


def test_gate_opens_only_after_confirmation():
    sid = _new_session()
    # confirming a NON-flagged field must not open the gate (and is refused)
    bad = client.post(f"/api/session/{sid}/confirm",
                      json={"field": "d1.date", "value": "2026-07-06"})
    assert bad.status_code == 422
    assert client.get(f"/api/session/{sid}/receipt").status_code == 409

    # the real human-confirmation event on the flagged field opens it
    ok = client.post(f"/api/session/{sid}/confirm",
                     json={"field": "d3.rows[0].units", "value": "298"})
    assert ok.status_code == 200
    assert ok.json()["gate_blocks"] is False
    rec = client.get(f"/api/session/{sid}/receipt")
    assert rec.status_code == 200, rec.text
    assert rec.json()["shortfall"] == "20.00"
    assert rec.json()["state"] == "RECEIPT"


def test_confirm_requires_value():
    sid = _new_session()
    r = client.post(f"/api/session/{sid}/confirm", json={"field": "d3.rows[0].units"})
    assert r.status_code == 422


def test_crop_id_rejects_path_traversal():
    r = client.get("/crop/sha256/..%2f..%2fetc%2fpasswd.png")
    assert r.status_code in (400, 404)


def test_server_binds_loopback_only():
    assert HOST in ("127.0.0.1", "localhost")
    assert client.get("/api/trust").json()["loopback_only"] is True


def test_unknown_session_404():
    assert client.get("/api/session/deadbeef/receipt").status_code == 404


if __name__ == "__main__":
    # No pytest in this venv — run as a plain script (ponytail: stdlib runner, not a framework).
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
