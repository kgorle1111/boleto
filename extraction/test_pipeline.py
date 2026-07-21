"""WS-A pipeline tests — every non-trivial mechanism gets a check that can fail.

pytest is NOT installed in .venv-gemma (and pyproject is frozen for WS-A), so these
are plain assert-based tests with a stdlib runner — no framework. They still fail
loudly. Run:
    BOLETO_MODEL=mock .venv-gemma/bin/python -m extraction.test_pipeline
(A later `pip install pytest` would also collect every test_* below unchanged.)
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("BOLETO_MODEL", "mock")

from PIL import Image

from core.contracts import SessionExtraction
from extraction import pipeline as P

ROOT = Path(__file__).resolve().parent.parent
TICKETS = ROOT / "tickets"


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="wsa_"))


# ── quality pre-gate ────────────────────────────────────────────────────────
def test_quality_gate_passes_sharp_ticket():
    q = P.quality_gate(TICKETS / "ticket_00.png")
    assert q.verdict == "ok", q


def test_quality_gate_rejects_blank_image():
    d = _tmp()
    blank = d / "blank.png"
    Image.new("RGB", (520, 380), "white").save(blank)  # zero Laplacian variance
    q = P.quality_gate(blank)
    assert q.verdict == "retake" and "blur" in (q.reason or ""), q


# ── perceptual-hash dedup ───────────────────────────────────────────────────
def test_dedup_flags_duplicate_not_distinct():
    d = _tmp()
    a, b = d / "a.png", d / "b.png"
    Image.open(TICKETS / "ticket_00.png").save(a)
    Image.open(TICKETS / "ticket_00.png").save(b)  # exact dup
    groups = P.dedup_groups([TICKETS / "ticket_00.png", a, TICKETS / "ticket_03.png", b])
    assert groups[0] is not None and groups[0] == groups[1] == groups[3], groups
    assert groups[2] is None, groups  # a genuinely different ticket is not grouped


# ── ensemble adaptive sampling ──────────────────────────────────────────────
def test_ensemble_agreement_stops_at_two_reads():
    stats = P._EnsembleStats()
    reads, val, unan = P.ensemble_field("x", lambda: 77, stats=stats)
    assert unan and val == 77 and stats.calls == 2, (reads, stats)  # no 3rd read


def test_ensemble_disagreement_triggers_third_read_and_flags():
    seq = iter([293, 298, 293])
    stats = P._EnsembleStats()
    reads, val, unan = P.ensemble_field("x", lambda: next(seq), stats=stats)
    assert stats.calls == 3 and not unan and val == 293 and reads == [293, 298, 293]


# ── schema validation (trust boundary) ──────────────────────────────────────
def test_schema_validators():
    cases = [
        ("rows[0].units", 77, True),
        ("rows[0].units", -3, False),
        ("rows[0].units", 7.5, False),   # units must be integer
        ("rows[0].rate", 2.25, True),
        ("date", "2026-07-10", True),
        ("date", "07/10/2026", False),
        ("worker_id", "", False),
        ("productive_hours", None, False),
    ]
    for field, value, ok in cases:
        assert P._valid(field, value)[0] is ok, (field, value)


def test_garbage_model_value_flags_never_crashes():
    reads, val, unan = P.ensemble_field("rows[0].units", lambda: "NaNsense")
    ok, _ = P._valid("rows[0].units", val)
    assert not ok  # routes to review instead of crashing


# ── cross-field checksum + single-digit repair ──────────────────────────────
def test_single_digit_repair_finds_minimal_fix():
    vals = {"rows": [{"units": 71, "rate": 2.0}, {"units": 30, "rate": 2.0}]}
    target = 77 * 2.0 + 30 * 2.0  # 214
    sugg = P.suggest_single_digit_repair(vals, target)
    assert sugg and sugg[0]["field"] == "rows[0].units", sugg
    assert sugg[0]["from"] == 71 and sugg[0]["to"] == 77, sugg
    assert "never auto-applied" in sugg[0]["note"].lower()


def test_repair_empty_when_consistent():
    vals = {"rows": [{"units": 100, "rate": 2.0}]}
    assert P.suggest_single_digit_repair(vals, 200.0) == []


# ── two-witness reconciliation ──────────────────────────────────────────────
def _stub(d: Path, **fields) -> Path:
    img = d / "stub.png"
    Image.new("RGB", (400, 200), "white").save(img)
    (d / "stub.png.truth.json").write_text(json.dumps(fields))
    return img


def test_reconcile_ok_when_totals_match():
    d = _tmp()
    t0 = P.extract_ticket(TICKETS / "ticket_01.png", d / "crops")
    total = P.ticket_piece_total(P.ticket_values(t0))
    stub = P.extract_stub(_stub(d, worker_id="W101", piece_total=total,
                                period_start="2026-07-11", period_end="2026-07-11",
                                hours_paid=9, amount_paid=total))
    assert P.reconcile([t0], stub).status == "ok"


def test_reconcile_mismatch_blocks_receipt():
    d = _tmp()
    t0 = P.extract_ticket(TICKETS / "ticket_01.png", d / "crops")
    total = P.ticket_piece_total(P.ticket_values(t0))
    stub = P.extract_stub(_stub(d, worker_id="W101", piece_total=total + 25,
                                period_start="2026-07-11", period_end="2026-07-11",
                                hours_paid=9, amount_paid=total))
    r = P.reconcile([t0], stub)
    assert r.status == "mismatch" and any(f["type"] == "amount_mismatch" for f in r.flags)
    se = SessionExtraction("s", "", "", [t0], stub, r, [])
    assert se.has_unconfirmed_flags() is True  # F4: open mismatch blocks the receipt


def test_reconcile_orphan_day():
    d = _tmp()
    t0 = P.extract_ticket(TICKETS / "ticket_01.png", d / "crops")  # dated 2026-07-11
    total = P.ticket_piece_total(P.ticket_values(t0))
    stub = P.extract_stub(_stub(d, worker_id="W101", piece_total=total,
                                period_start="2026-07-20", period_end="2026-07-26",
                                hours_paid=9, amount_paid=total))
    assert any(f["type"] == "orphan_day" for f in P.reconcile([t0], stub).flags)


# ── ticket extraction end-to-end on the mock oracle ─────────────────────────
def test_extract_ticket_matches_truth_on_mock():
    d = _tmp()
    t = P.extract_ticket(TICKETS / "ticket_00.png", d / "crops")
    vals = P.ticket_values(t)
    assert vals["worker_id"] == "W100" and vals["date"] == "2026-07-10", vals
    assert [r["units"] for r in vals["rows"]] == [26, 77, 46], vals
    assert all(not f.flagged for f in t.fields)  # noiseless oracle ⇒ unanimous


def test_extract_session_over_eight_tickets():
    d = _tmp()
    paths = [TICKETS / f"ticket_{i:02d}.png" for i in range(8)]
    se, stats = P.extract_session(paths, None, d / "crops")
    assert isinstance(se, SessionExtraction) and len(se.tickets) == 8
    assert stats.fields > 0 and stats.calls == stats.fields * 2  # unanimous ⇒ 2 reads each


def _run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # a crash IS a failure — never hidden
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
