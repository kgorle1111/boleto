"""Load the demo_session fixtures as FROZEN §10.2 contract objects.

Until WS-A/WS-B land, the app consumes these mocks. The key point: the F4 safety
gate uses the FROZEN `SessionExtraction.has_unconfirmed_flags` method — the server
does NOT re-implement the flag check, so it cannot drift from the contract. WS-E
swaps `load_session`/`load_receipt` for calls into real WS-A/WS-B; nothing else moves.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.contracts import (
    ExtractionResult,
    Quality,
    Receipt,
    Reconciliation,
    SessionExtraction,
    TicketExtraction,
)

DEMO = Path(__file__).resolve().parent.parent / "demo_session"


def _fields(raw: list[dict]) -> list[ExtractionResult]:
    return [ExtractionResult.from_dict(f) for f in raw]


def load_session(session_id: str, variant: str = "ok") -> SessionExtraction:
    fixture = "session_mismatch.json" if variant == "mismatch" else "session.json"
    d = json.loads((DEMO / fixture).read_text())
    tickets = [
        TicketExtraction(
            image=t["image"], date=t["date"],
            quality=Quality(**t["quality"]),
            fields=_fields(t["fields"]),
            dedup_group=t.get("dedup_group"))
        for t in d["tickets"]
    ]
    return SessionExtraction(
        session_id=session_id,
        period_start=d["period_start"], period_end=d["period_end"],
        tickets=tickets,
        stub={"fields": _fields(d["stub"]["fields"])},
        reconciliation=Reconciliation(**d["reconciliation"]),
        missing_days=d.get("missing_days", []))


def load_receipt(session_id: str) -> Receipt:
    d = json.loads((DEMO / "receipt.json").read_text())
    d["session_id"] = session_id
    return Receipt.from_dict(d)


def build_receipt(se: SessionExtraction, confirmed: dict[str, str]) -> Receipt:
    """Adapter interface parity with app.real_contracts.build_receipt. The mock returns the
    prepared receipt fixture (the deterministic caught-error demo beat) with the F6 residual
    filled from the real error-budget derivation (not a hard-coded string)."""
    from core.error_budget import finalized_receipt_residual

    receipt = load_receipt(se.session_id)
    receipt.residual_error, _ = finalized_receipt_residual()
    return receipt
