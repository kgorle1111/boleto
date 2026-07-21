"""WS-E real adapter — the app's contracts, produced by REAL WS-A + WS-B (no mocks).

Swaps app.mock_contracts: instead of reading demo_session/session.json fixtures, this
runs the shipping code paths:
    extraction.extract_session(...)  →  SessionExtraction   (WS-A)
    wage_engine.audit_session(...)   →  Receipt             (WS-B)
    error_budget.finalized_receipt_residual() → residual on the receipt (WS-E F6)

Two seams, both stated honestly (not hidden):
  * PAYSTUB: the demo set has no genuine paystub scan (demo_session/stub.png is another
    ticket image), so amount_paid is a PROVIDED demo constant, not an OCR result. The
    model work that IS real is the 5-ticket extraction. ponytail: real stub OCR needs a
    real paystub image + core/extraction/prompts/paystub.txt; wire when one exists.
  * FIELD IDS are namespaced per ticket ("d{n}.") and for the stub ("stub.") so the F4
    gate (a flat set of confirmed ids) can't collide across tickets. WS-B strips the
    prefix; WS-D's gate/UI already expect it.

Selected by env BOLETO_ADAPTER=real (default is mock, so WS-D's tests stay on fixtures).
Model by env BOLETO_MODEL (gemma3:latest for the real run; mock reads truth.json).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from core.contracts import ExtractionResult, Receipt, SessionExtraction
from core.error_budget import finalized_receipt_residual
from core.wage_engine.session_audit import audit_session
from extraction.pipeline import (
    extract_session,
    reconcile,
    ticket_piece_total,
    ticket_values,
)

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo_session"
CROPS = DEMO / "crops"
CACHE = DEMO / "real_session.json"

# Employer-reported pay for the demo period. See PAYSTUB seam above — provided, not OCR'd.
DEMO_AMOUNT_PAID = os.environ.get("BOLETO_DEMO_PAID", "1000.00")


def _prefix_ids(se: SessionExtraction) -> None:
    """Make field ids session-unique in place: ticket i → 'd{i+1}.', stub → 'stub.'."""
    for i, t in enumerate(se.tickets):
        for f in t.fields:
            if not f.field.startswith(f"d{i + 1}."):
                f.field = f"d{i + 1}.{f.field}"
    for f in se.stub.get("fields", []):
        if not f.field.startswith("stub."):
            f.field = f"stub.{f.field}"


def _inject_stub(se: SessionExtraction, tampered: bool = False) -> None:
    """Attach the provided paystub (amount_paid + a piece_total witness) and run the REAL
    two-witness reconcile. tampered=True corrupts the stub piece line to force a mismatch
    that the F4 gate must block (the reconciliation-MISMATCH demo)."""
    piece_sum = round(sum(ticket_piece_total(ticket_values(t)) for t in se.tickets if t.fields), 2)
    stub_piece = piece_sum + (25.0 if tampered else 0.0)  # +$25 phantom → amount_mismatch

    def er(field_id: str, value) -> ExtractionResult:
        return ExtractionResult(field_id, value, [value], True, False,
                                prompt_version="paystub@1")

    fields = [
        er("amount_paid", DEMO_AMOUNT_PAID),
        er("piece_total", stub_piece),
        er("period_start", se.period_start),
        er("period_end", se.period_end),
    ]
    se.stub = {"fields": fields}
    # reconcile expects BARE stub ids; run it now, THEN prefix everything.
    se.reconciliation = reconcile(se.tickets, se.stub)
    # WS-F critic finding 1: this stub's piece_total is DERIVED from the tickets, so the
    # reconcile above is demo plumbing, NOT an independent witness — it cannot catch a
    # unanimous misread. Say so on the record; the error budget must not credit it.
    se.reconciliation.flags.append({
        "type": "non_independent_witness",
        "detail": "stub piece_total synthesized from ticket sums (no real paystub OCR); "
                  "two-witness check is not counted in the residual-error budget",
    })


_ADAPTER = ROOT / "evals" / "adapters" / "boleto-v1"


def _default_model() -> str:
    """E4B via MLX is THE backend (user decision). BASE model, deliberately:
    the LoRA specialists (v1/v2/v3, evals/adapters/) all LOST to base on the fixed
    handwritten eval (see FINAL-REPORT fine-tune section) — a challenger is promoted
    only when it WINS on the eval (F2 champion/challenger, enforced). Opt into an
    adapter explicitly via BOLETO_MODEL="mlx:@evals/adapters/<name>"."""
    return "mlx"


def _build(session_id: str, tampered: bool = False) -> SessionExtraction:
    model = os.environ.get("BOLETO_MODEL") or _default_model()
    tickets = sorted((DEMO / "tickets").glob("day_*.png"))
    if not tickets:
        raise RuntimeError(
            f"no demo tickets under {DEMO / 'tickets'}; run demo_session/build.py first")
    se, _stats = extract_session(
        [str(p) for p in tickets], stub_path=None, crops_dir=str(CROPS),
        session_id=session_id, model=model,
        k=(2 if model != "mock" else 3), temperature=0.2,
        whole_ticket=(model != "mock"))  # whole-ticket read is the real backend (WS-A wound)
    _inject_stub(se, tampered=tampered)
    _prefix_ids(se)
    return se


def load_session(session_id: str, variant: str = "ok") -> SessionExtraction:
    """Real SessionExtraction for the demo pay period. The 'ok' variant is cached to
    demo_session/real_session.json so the server boots fast; 'mismatch' is built live
    (it only tweaks the stub, no extra model calls)."""
    if variant == "mismatch":
        se = _build(session_id, tampered=True)
        se.session_id = session_id
        return se
    if CACHE.exists():
        se = SessionExtraction(**_from_cache(session_id))
        return se
    se = _build(session_id)
    CACHE.write_text(json.dumps(se.to_dict(), indent=2))
    return se


def _from_cache(session_id: str) -> dict:
    from core.contracts import Quality, Reconciliation, TicketExtraction
    d = json.loads(CACHE.read_text())
    tickets = [
        TicketExtraction(
            image=t["image"], date=t["date"], quality=Quality(**t["quality"]),
            fields=[ExtractionResult.from_dict(f) for f in t["fields"]],
            dedup_group=t.get("dedup_group"))
        for t in d["tickets"]
    ]
    return {
        "session_id": session_id,
        "period_start": d["period_start"], "period_end": d["period_end"],
        "tickets": tickets,
        "stub": {"fields": [ExtractionResult.from_dict(f) for f in d["stub"]["fields"]]},
        "reconciliation": Reconciliation(**d["reconciliation"]),
        "missing_days": d.get("missing_days", []),
    }


def _apply_confirmed(se: SessionExtraction, confirmed: dict[str, str]) -> None:
    """Overlay human-confirmed values onto the flagged fields so the audit computes on the
    corrected numbers (the human decides — house rule). Coerce to int/float where the raw
    read was numeric; leave strings alone."""
    for t in se.tickets:
        for f in t.fields:
            if f.field in confirmed:
                f.value = _coerce(confirmed[f.field], f.value)
                f.flagged = False
    for f in se.stub.get("fields", []):
        if f.field in confirmed:
            f.value = _coerce(confirmed[f.field], f.value)
            f.flagged = False


def _coerce(new: str, old):
    if isinstance(old, bool):
        return old
    if isinstance(old, int):
        try:
            return int(new)
        except ValueError:
            return new
    if isinstance(old, float):
        try:
            return float(new)
        except ValueError:
            return new
    return new


def build_receipt(se: SessionExtraction, confirmed: dict[str, str]) -> Receipt:
    """Real WS-B audit of the (confirmed) session → Receipt, with the F6 residual filled."""
    _apply_confirmed(se, confirmed)
    receipt = audit_session(se)
    residual, _derivation = finalized_receipt_residual()
    receipt.residual_error = residual
    return receipt
