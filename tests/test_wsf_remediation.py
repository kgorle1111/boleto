"""WS-F remediation regression tests — one test per confirmed finding.

Each test fails without its fix (verified during remediation). Run:
    .venv-gemma/bin/python -m pytest tests/test_wsf_remediation.py -q
"""
from __future__ import annotations

import sys
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.contracts import (  # noqa: E402
    ExtractionResult,
    Quality,
    Reconciliation,
    SessionExtraction,
    TicketExtraction,
    is_pay_field,
)
from core.error_budget import BASE_FINALIZED, finalized_receipt_residual  # noqa: E402
from core.wage_engine.history import ReceiptHistory  # noqa: E402
from core.wage_engine.session_audit import audit_session  # noqa: E402
from extraction.pipeline import reconcile  # noqa: E402


def _er(field: str, value, flagged: bool = False) -> ExtractionResult:
    return ExtractionResult(field, value, [value, value], True, flagged)


def _ticket(image: str, the_date: str, units: int, rate: float, hours: float,
            group: str | None = None) -> TicketExtraction:
    fields = [
        _er("rows[0].units", units), _er("rows[0].rate", rate),
        _er("productive_hours", hours), _er("nonproductive_hours", 0),
        _er("rest_hours", 0),
    ]
    return TicketExtraction(image, the_date, Quality(0.9, "ok"), fields, group)


def _session(tickets, stub_fields=None, recon_status="ok") -> SessionExtraction:
    return SessionExtraction(
        "t", "2026-07-06", "2026-07-12", tickets,
        {"fields": stub_fields or []}, Reconciliation(recon_status, []), [])


# 1 — honest error budget: no reducers fire in the shipped path → residual == base
def test_residual_is_honest_default():
    label, d = finalized_receipt_residual()
    assert d["residual"] == BASE_FINALIZED
    assert label == "< 2%"
    assert "caveat" in d
    # a reducer is credited ONLY when explicitly declared active
    _, d2 = finalized_receipt_residual(("two_witness",))
    assert d2["residual"] < d["residual"]


# 2 — stub trust boundary: unanimous garbage flags, never crashes
def test_stub_garbage_routes_to_review_not_crash():
    t = _ticket("sha256/a.png", "2026-07-06", 100, 2.0, 8)
    stub = {"fields": [_er("piece_total", "N/A"), _er("amount_paid", "N/A")]}
    rec = reconcile([t], stub)                      # must not raise
    assert rec.status == "mismatch"
    assert rec.flags[0]["type"] == "unreadable_stub_piece_line"
    se = _session([t, _ticket("sha256/b.png", "2026-07-07", 100, 2.0, 8)],
                  stub_fields=[_er("amount_paid", "N/A")])
    receipt = audit_session(se, as_of=date(2026, 7, 20))  # must not raise
    assert receipt.amount_paid == "0.00"           # unparseable → treated absent, flagged upstream


# 3 — duplicate photos never double-count
def test_duplicate_photos_do_not_double_count():
    a = _ticket("sha256/a.png", "2026-07-06", 150, 2.0, 8, group="dup0")
    b = _ticket("sha256/b.png", "2026-07-06", 150, 2.0, 8, group="dup0")
    c = _ticket("sha256/c.png", "2026-07-07", 100, 2.0, 8)
    se = _session([a, b, c])
    # gate blocks until the duplicate group is acknowledged
    assert se.has_unconfirmed_flags() is True
    assert se.has_unconfirmed_flags({"dedup:dup0"}) is False
    receipt = audit_session(se, as_of=date(2026, 7, 20))
    piece = next(ln for ln in receipt.lines if ln.item == "piece_earnings")
    assert Decimal(piece.amount) == Decimal("500.00")   # 150×2 + 100×2, NOT 600


# 4 — 1-day session refused with a named error (server maps it to 422)
def test_single_ticket_refused_with_explanation():
    se = _session([_ticket("sha256/a.png", "2026-07-06", 100, 2.0, 8)])
    try:
        audit_session(se)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "§226.2" in str(e)


# 5 — F4 gate scoped to pay-bearing fields
def test_gate_ignores_non_pay_flags():
    t = _ticket("sha256/a.png", "2026-07-06", 100, 2.0, 8)
    t.fields.append(_er("worker_id", None, flagged=True))
    t.fields.append(_er("rows[0].crop", None, flagged=True))
    se = _session([t])
    assert se.has_unconfirmed_flags() is False      # only non-pay flags → no block
    t.fields.append(_er("rows[0].units", 99, flagged=True))
    assert se.has_unconfirmed_flags() is True       # a pay flag still blocks
    assert is_pay_field("d3.rows[0].units") and not is_pay_field("d3.worker_id")


# 7 — hash chain survives concurrent stores
def test_concurrent_stores_keep_chain_intact():
    from core.contracts import Receipt, ReceiptLine
    h = ReceiptHistory(":memory:", check_same_thread=False)

    def rc(i: int) -> Receipt:
        return Receipt(f"s{i}", [ReceiptLine("rest_pay", "1.00", "LC", "", [], {})],
                       "1.00", "0.00", "1.00", {}, False, [], "d")

    errs: list[Exception] = []

    def work(i: int) -> None:
        try:
            h.store(rc(i))
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs, errs
    n = h.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    assert n == 40, f"lost receipts: {n}/40"
    assert h.verify_chain() is True


# 8 — wipe is real: rows gone, file vacuumed, secure_delete on
def test_wipe_completeness(tmp_path):
    from core.contracts import Receipt, ReceiptLine
    db = tmp_path / "h.db"
    h = ReceiptHistory(str(db))
    assert h.conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
    h.store(Receipt("s1", [ReceiptLine("rest_pay", "1.00", "LC", "", [], {})],
                    "1.00", "0.00", "1.00", {}, False, [], "d"))
    assert h.wipe() == 1
    assert h.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


# 3b — dedup refinement: same-looking tickets with DIFFERENT dates are not duplicates
def test_dedup_ungroups_different_dates():
    from extraction.pipeline import extract_session
    import tempfile
    crops = tempfile.mkdtemp()
    paths = [f"tickets/ticket_{i:02d}.png" for i in range(3)]
    se, _ = extract_session(paths, None, crops, model="mock")
    # mock reads distinct dates (07-10..12); any pHash false-positive must be ungrouped
    for t in se.tickets:
        assert t.dedup_group is None, (t.image, t.dedup_group)
