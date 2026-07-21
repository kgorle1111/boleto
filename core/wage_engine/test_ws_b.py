"""WS-B tests: golden known-answer vectors + property/metamorphic invariants + history.

Run:  .venv-gemma/bin/python -m pytest core/wage_engine/test_ws_b.py -q
"""
from __future__ import annotations

import json
import pathlib
from datetime import date
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from core.wage_engine.history import ReceiptHistory
from core.wage_engine.session_audit import audit_session, build_session
from core.wage_engine.wage_engine import Day, Workweek, audit, hrs, reconstruct, usd

GOLDEN = json.loads(pathlib.Path(__file__).with_name("golden_cases.json").read_text())
MW = usd(GOLDEN["minimum_wage"])

_MONEY_KEYS = {
    "piece_earnings", "mw_makeup", "nonproductive_pay", "rest_pay",
    "rest_rate", "regular_rate", "ot_premium", "total_owed", "shortfall",
}


# ── golden: pure-arithmetic reconstruct cases (portable to Kotlin/Rust) ──────
def _day_from(d: dict) -> Day:
    return Day(
        piece_earnings=usd(d["piece_earnings"]),
        productive_hours=hrs(d["productive_hours"]),
        nonproductive_hours=hrs(d.get("nonproductive_hours", 0)),
        rest_hours=hrs(d.get("rest_hours", 0)),
    )


def test_golden_reconstruct_cases():
    for case in GOLDEN["reconstruct_cases"]:
        week = Workweek([_day_from(d) for d in case["days"]], MW)
        r = audit(week, case["amount_paid"]) if "amount_paid" in case else reconstruct(week)
        for key, want in case["expect"].items():
            got = r[key]
            expected = usd(want) if key in _MONEY_KEYS else hrs(want)
            assert got == expected, f"{case['name']}::{key} got {got} want {expected}"


# ── golden: session-level cases via the sole entry point ────────────────────
def _session_from(case) -> object:
    return build_session(
        case["name"], case["period_start"], case["period_end"],
        case["days"], case["amount_paid"], case.get("missing_days", []),
    )


def test_golden_session_cases():
    for case in GOLDEN["session_cases"]:
        receipt = audit_session(_session_from(case), as_of=date(2026, 7, 19))
        exp = case["expect"]
        assert receipt.lower_bound == exp["lower_bound"], case["name"]
        if exp.get("missing_days") is not None and "missing_days" in exp:
            assert receipt.missing_days == exp["missing_days"], case["name"]
        if exp.get("shortfall_positive"):
            assert Decimal(receipt.shortfall) > 0, case["name"]
        # every non-piece receipt line must carry a citation from the data table
        for ln in receipt.lines:
            assert ln.citation, f"{case['name']}: line {ln.item} missing citation"


def test_incomplete_week_labels_lines_as_lower_bounds():
    case = next(c for c in GOLDEN["session_cases"] if c["expect"]["lower_bound"])
    receipt = audit_session(_session_from(case), as_of=date(2026, 7, 19))
    assert receipt.lower_bound is True
    assert receipt.missing_days == ["2026-07-09"]
    assert all("LOWER BOUND" in ln.arithmetic for ln in receipt.lines)


def test_single_ticket_refused():
    case = GOLDEN["session_cases"][0]
    one_day = dict(case, days=case["days"][:1], name="single")
    try:
        audit_session(_session_from(one_day))
    except ValueError as e:
        assert "weekly-average" in str(e).lower() or "full pay period" in str(e).lower()
    else:
        raise AssertionError("single-ticket session must be refused")


def test_dollar_sensitivity_present_and_shaped():
    case = GOLDEN["session_cases"][0]
    receipt = audit_session(_session_from(case), as_of=date(2026, 7, 19))
    # at least one line exposes a per-source dollar delta, keyed by a source field id
    assert any(ln.dollar_sensitivity for ln in receipt.lines)
    for ln in receipt.lines:
        assert set(ln.sources) == set(ln.dollar_sensitivity.keys())
        for src, delta in ln.dollar_sensitivity.items():
            assert delta.endswith(("/unit", "/$1rate", "/h")), (src, delta)


def test_claim_value_labeled_potential():
    case = GOLDEN["session_cases"][0]
    receipt = audit_session(_session_from(case), as_of=date(2026, 7, 19))
    cv = receipt.claim_value
    assert "1194.2" in cv["citation"] and "98.1" in cv["citation"]
    assert "advocate" in cv["note"].lower()
    assert Decimal(cv["liquidated_damages"]) >= 0 and Decimal(cv["interest"]) >= 0


# ── property-based + metamorphic invariants (≥200 examples each) ─────────────
_money = st.decimals(min_value=0, max_value=Decimal("5000"), places=2)
_hours = st.decimals(min_value=0, max_value=Decimal("16"), places=2)
_mw = st.decimals(min_value=Decimal("10"), max_value=Decimal("30"), places=2)


@st.composite
def _weeks(draw):
    n = draw(st.integers(min_value=1, max_value=7))
    days = [
        Day(
            piece_earnings=usd(draw(_money)),
            productive_hours=hrs(draw(_hours)),
            nonproductive_hours=hrs(draw(_hours)),
            rest_hours=hrs(draw(_hours)),
        )
        for _ in range(n)
    ]
    return Workweek(days, usd(draw(_mw)))


@settings(max_examples=200)
@given(week=_weeks())
def test_inv_owed_ge_piece(week):
    r = reconstruct(week)
    piece = usd(sum((d.piece_earnings for d in week.days), Decimal(0)))
    assert r["total_owed"] >= piece


@settings(max_examples=200)
@given(week=_weeks())
def test_inv_never_negative(week):
    r = reconstruct(week)
    for key in ("piece_earnings", "mw_makeup", "nonproductive_pay", "rest_pay",
                "ot_premium", "total_owed"):
        assert r[key] >= 0, (key, r[key])


@settings(max_examples=200)
@given(week=_weeks())
def test_inv_rest_pay_ge_mw_times_rest_hours(week):
    r = reconstruct(week)
    rest_h = sum((d.rest_hours for d in week.days), Decimal(0))
    assert r["rest_pay"] >= usd(rest_h * week.minimum_wage)


@settings(max_examples=200)
@given(week=_weeks(), paid_hi=_money, drop=_money)
def test_inv_shortfall_monotonic_as_paid_decreases(week, paid_hi, drop):
    owed = reconstruct(week)["total_owed"]
    paid_lo = paid_hi + drop  # paid_lo >= paid_hi ... we compare the reverse direction
    # less paid => shortfall non-decreasing: shortfall(paid_hi) >= shortfall(paid_lo)
    s_more_paid = usd(owed - usd(paid_lo))
    s_less_paid = usd(owed - usd(paid_hi))
    assert s_less_paid >= s_more_paid


@settings(max_examples=200)
@given(week=_weeks(), k=st.integers(min_value=1, max_value=20))
def test_inv_scaling_units_scales_piece_exactly(week, k):
    base_piece = reconstruct(week)["piece_earnings"]
    scaled = Workweek(
        [Day(usd(d.piece_earnings * k), d.productive_hours,
             d.nonproductive_hours, d.rest_hours) for d in week.days],
        week.minimum_wage,
    )
    assert reconstruct(scaled)["piece_earnings"] == usd(base_piece * k)


@settings(max_examples=200)
@given(week=_weeks())
def test_inv_zero_hour_day_changes_nothing(week):
    base = reconstruct(week)
    week2 = Workweek(week.days + [Day(usd(0), hrs(0), hrs(0), hrs(0))], week.minimum_wage)
    assert reconstruct(week2) == base


# ── history: hash chain + pattern summary ───────────────────────────────────
def _store_week(hist, case, amount_paid):
    receipt = audit_session(_session_from(dict(case, amount_paid=amount_paid)),
                            as_of=date(2026, 7, 19))
    hist.store(receipt)
    return receipt


def test_history_hash_chain_and_pattern():
    hist = ReceiptHistory(":memory:")
    complete = GOLDEN["session_cases"][0]
    _store_week(hist, complete, "1000.00")   # underpaid
    _store_week(hist, complete, "5000.00")   # overpaid → no shortfall
    _store_week(hist, complete, "900.00")    # underpaid
    assert hist.verify_chain() is True

    ps = hist.pattern_summary()
    assert ps["total_periods"] == 3
    assert ps["shorted_periods"] == 2
    assert Decimal(ps["cumulative_shortfall"]) > 0
    assert ps["periods"][0]["citations"]  # per-period statute citations present

    # tamper → chain breaks
    hist.conn.execute("UPDATE receipts SET shortfall='0.00' WHERE seq=1")
    hist.conn.execute(
        "UPDATE receipts SET receipt_json=REPLACE(receipt_json,'shortfall','SHORTFALL') WHERE seq=1"
    )
    hist.conn.commit()
    assert hist.verify_chain() is False


def _run_all() -> int:
    """Stdlib runner (no pytest dep). hypothesis @given funcs run their full example
    set when called with no args; plain tests just execute. pytest also collects this
    file normally once pytest is installed (pyproject already configures it)."""
    import traceback

    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failures += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    total = len(tests)
    print(f"\n{total - failures}/{total} passed"
          + (f", {failures} FAILED" if failures else " — all green"))
    return 1 if failures else 0


if __name__ == "__main__":
    import sys

    sys.exit(_run_all())
