"""WS-B legal layer: SessionExtraction -> Receipt (the SOLE audit entry point).

Boundaries (house rule: the LLM extracts; the engine decides):
  * audit_session is the ONLY way to produce a Receipt. There is no per-ticket
    audit — §226.2 rest pay uses the WORKWEEK-average hourly rate, so auditing a
    single ticket in isolation is mathematically wrong. Single-ticket sessions are
    refused with an explanatory error.
  * An incomplete week (session.missing_days non-empty) never silently computes on
    partial data: the receipt is marked lower_bound and every aggregate line's
    arithmetic is prefixed as a lower bound.
  * Statute citations live in a data table (CITATIONS), not prose.
  * Claim value (LC §1194.2 liquidated damages + LC §98.1 10%/yr simple interest)
    is labeled "potential — an advocate confirms what applies".
  * Per-line dollar-sensitivity (#43 engine half): the dollar delta a 1-unit misread
    of each source field would cause, by finite difference on the canonical engine.

reconstruct() runs ONCE for the canonical numbers; sensitivity uses extra throwaway
reconstructs on perturbed copies (not the answer, just the derivative).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from core.contracts import ExtractionResult, Receipt, ReceiptLine, SessionExtraction
from core.wage_engine.config import minimum_wage_for
from core.wage_engine.wage_engine import Day, Workweek, hrs, reconstruct, usd

# ── statute citation table (data, not prose) — kn: exact subsections are Kannishk's
# legal-wording pass, §10.6 item 4. Reasonable-but-unconfirmed cites for the demo. ──
CITATIONS: dict[str, str] = {
    "piece_earnings": "Labor Code §200; IWC Wage Order 14 §4",
    "mw_makeup": "Labor Code §1197; IWC Wage Order 14 §4",
    "nonproductive_pay": "Labor Code §226.2(a)(1)",
    "rest_pay": "Labor Code §226.2(a)(3)(A)",
    "ot_premium": "Labor Code §510; IWC Wage Order 14 §3",
}

# receipt line order + human label per engine key
LINE_SPECS: list[tuple[str, str]] = [
    ("piece_earnings", "piece_earnings"),
    ("mw_makeup", "mw_makeup"),
    ("nonproductive_pay", "nonproductive_pay"),
    ("rest_pay", "rest_pay"),
    ("ot_premium", "ot_premium"),
]

DISCLAIMER = (
    "Estimación para discutir con un defensor — no es asesoría legal. "
    "Comuníquese con CRLA (California Rural Legal Assistance). / "
    "Estimate for discussion with an advocate — not legal advice. "
    "Contact CRLA (California Rural Legal Assistance)."
)

CLAIM_NOTE = "potential claim value — an advocate confirms what applies"

_ROW_RE = re.compile(r"^rows\[(\d+)\]\.(units|rate)$")
# Field ids may arrive session-unique-prefixed (WS-E adapter namespaces each ticket's
# fields "d{n}." and the stub's "stub." so the F4 gate can't collide across tickets).
# Strip that prefix before matching; bare ids (WS-B's own build_session) are unaffected.
_PREFIX_RE = re.compile(r"^(?:d\d+|stub)\.")


def _strip_prefix(field_id: str) -> str:
    return _PREFIX_RE.sub("", field_id)


# ── parsing SessionExtraction -> engine inputs ──────────────────────────────
@dataclass
class _ParsedDay:
    date: str | None
    rows: dict[int, dict[str, Decimal]] = field(default_factory=dict)
    productive_hours: Decimal = field(default_factory=lambda: hrs(0))
    nonproductive_hours: Decimal = field(default_factory=lambda: hrs(0))
    rest_hours: Decimal = field(default_factory=lambda: hrs(0))

    def piece(self) -> Decimal:
        total = Decimal(0)
        for cell in self.rows.values():
            total += cell.get("units", Decimal(0)) * cell.get("rate", Decimal(0))
        return usd(total)

    def to_day(self) -> Day:
        return Day(
            piece_earnings=self.piece(),
            productive_hours=self.productive_hours,
            nonproductive_hours=self.nonproductive_hours,
            rest_hours=self.rest_hours,
        )


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _parse_ticket(fields: list[ExtractionResult], the_date: str | None) -> _ParsedDay:
    """Untrusted model output: coerce known fields, ignore unknown ones (never crash)."""
    pd = _ParsedDay(date=the_date)
    for f in fields:
        name = _strip_prefix(f.field)
        m = _ROW_RE.match(name)
        if m:
            idx, kind = int(m.group(1)), m.group(2)
            pd.rows.setdefault(idx, {})[kind] = _dec(f.value)
        elif name == "productive_hours":
            pd.productive_hours = _dec(f.value)
        elif name == "nonproductive_hours":
            pd.nonproductive_hours = _dec(f.value)
        elif name == "rest_hours":
            pd.rest_hours = _dec(f.value)
        # unknown fields (worker_id, date, etc.) are not pay-bearing → ignored
    return pd


def _parse_amount_paid(stub: dict) -> Decimal:
    """Employer-reported pay for the period. kn: convention = a stub field named
    amount_paid | gross_pay | net_pay; if absent, treat as $0 (fully unpaid) —
    the receipt then shows the FULL owed amount as the shortfall, labeled."""
    by_name = {_strip_prefix(f.field): f for f in stub.get("fields", [])}
    for name in ("amount_paid", "gross_pay", "net_pay"):
        if name in by_name:
            # trust boundary (WS-F finding 2): model output may be non-numeric even
            # when unanimous. Unparseable → treat as absent ($0), never crash — the
            # stub field is already flagged for review by extract_stub's validation.
            try:
                return usd(_dec(by_name[name].value))
            except (InvalidOperation, ValueError, TypeError):
                continue
    return usd(0)


# ── dollar sensitivity (#43): finite difference on the canonical engine ─────
def _bump_delta(kind: str) -> Decimal:
    return Decimal(1)  # +1 unit / +$1 rate / +1 hour — labeled by kind below


def _suffix(kind: str) -> str:
    return {"units": "/unit", "rate": "/$1rate"}.get(kind, "/h")


def _rebuild_days(parsed: list[_ParsedDay]) -> list[Day]:
    return [p.to_day() for p in parsed]


def _perturb(parsed: list[_ParsedDay], ti: int, field_id: str) -> list[_ParsedDay]:
    """Return a copy of parsed with the one field bumped by its natural unit."""
    import copy

    dup = copy.deepcopy(parsed)
    m = _ROW_RE.match(field_id)
    if m:
        idx, kind = int(m.group(1)), m.group(2)
        dup[ti].rows[idx][kind] = dup[ti].rows[idx].get(kind, Decimal(0)) + _bump_delta(kind)
    else:
        kind = "h"
        cur = getattr(dup[ti], field_id)
        setattr(dup[ti], field_id, cur + _bump_delta(kind))
    return dup


def _field_ids(pd: _ParsedDay) -> list[tuple[str, str]]:
    ids: list[tuple[str, str]] = []
    for idx, cell in sorted(pd.rows.items()):
        for kind in ("units", "rate"):
            if kind in cell:
                ids.append((f"rows[{idx}].{kind}", kind))
    for name in ("productive_hours", "nonproductive_hours", "rest_hours"):
        if getattr(pd, name) != 0:
            ids.append((name, "h"))
    return ids


def _sensitivity(parsed: list[_ParsedDay], mw: Decimal) -> dict[str, dict[str, str]]:
    """{engine_line_key: {source_field_id: 'delta/unit'}} — only nonzero deltas."""
    base = reconstruct(Workweek(_rebuild_days(parsed), mw))
    sens: dict[str, dict[str, str]] = {key: {} for key, _ in LINE_SPECS}
    for ti, pd in enumerate(parsed):
        for field_id, kind in _field_ids(pd):
            bumped = reconstruct(Workweek(_rebuild_days(_perturb(parsed, ti, field_id)), mw))
            for key, _ in LINE_SPECS:
                delta = abs(bumped[key] - base[key])
                if delta != 0:
                    # tag with day index when >1 day so identical field ids don't collide
                    tag = field_id if len(parsed) == 1 else f"day{ti}.{field_id}"
                    sens[key][tag] = f"{usd(delta)}{_suffix(kind)}"
    return sens


# ── claim value (#21) ───────────────────────────────────────────────────────
def _claim_value(shortfall: Decimal, period_end: str, as_of: date) -> dict:
    """LC §1194.2 liquidated damages + LC §98.1 10%/yr simple prejudgment interest.

    kn: §1194.2 liquidated damages strictly double only the sub-MINIMUM-WAGE portion
    of the shortfall; here we conservatively use the whole shortfall as the base and
    flag it 'potential'. Refine the base to the MW-violation slice in the legal pass.
    """
    y, m, d = (int(x) for x in period_end.split("-"))
    days_elapsed = max(0, (as_of - date(y, m, d)).days)
    interest = usd(shortfall * Decimal("0.10") * Decimal(days_elapsed) / Decimal(365))
    liquidated = usd(shortfall)
    return {
        "liquidated_damages": str(liquidated),
        "interest": str(interest),
        "citation": "LC §1194.2, §98.1",
        "interest_basis": f"10%/yr simple on ${shortfall} for {days_elapsed} days (period end→as_of)",
        "note": CLAIM_NOTE,
    }


# ── arithmetic strings ──────────────────────────────────────────────────────
def _arith(key: str, r: dict, agg: dict, mw: Decimal) -> str:
    if key == "piece_earnings":
        return f"Σ(units × rate) across all rows = ${r['piece_earnings']}"
    if key == "mw_makeup":
        return (
            f"productive floor {agg['prod_h']}h × ${mw} = ${agg['prod_h'] * mw} "
            f"exceeds piece ${r['piece_earnings']} → make-up ${r['mw_makeup']}"
        )
    if key == "nonproductive_pay":
        return f"{agg['nonprod_h']}h nonproductive × ${mw} (LC 226.2 min) = ${r['nonproductive_pay']}"
    if key == "rest_pay":
        return (
            f"{agg['rest_h']}h rest × ${r['rest_rate']} "
            f"(weekly avg rate vs MW ${mw}, higher wins) = ${r['rest_pay']}"
        )
    if key == "ot_premium":
        return (
            f"{r['ot_hours']} OT hours × 0.5 × reg rate ${r['regular_rate']} "
            f"(half-time method) = ${r['ot_premium']}"
        )
    return ""


# ── the entry point ─────────────────────────────────────────────────────────
def audit_session(session: SessionExtraction, as_of: date | None = None) -> Receipt:
    """SessionExtraction -> Receipt. The SOLE audit path. Runs reconstruct() ONCE."""
    if len(session.tickets) < 2:
        raise ValueError(
            "audit_session requires a full pay period (≥2 day-tickets). §226.2 rest pay "
            "uses the WORKWEEK-average hourly rate, so auditing a single ticket in "
            "isolation is mathematically wrong — provide the whole week's tickets. "
            "kn: a legitimately 1-day pay period is an unhandled edge; revisit if a real stub needs it."
        )
    as_of = as_of or date.today()

    # WS-F finding 3: near-duplicate photos (shared dedup_group) must not double-count
    # into owed pay. The F4 gate already requires human acknowledgment of the group
    # ("dedup:<gid>"); here the engine sums only the FIRST ticket of each group.
    # kn: keep-first policy; a picker UI for "which photo is the real one" if a
    # session ever needs to keep the second copy instead.
    seen_groups: set[str] = set()
    audited_tickets = []
    for t in session.tickets:
        if t.dedup_group is not None:
            if t.dedup_group in seen_groups:
                continue
            seen_groups.add(t.dedup_group)
        audited_tickets.append(t)

    parsed = [_parse_ticket(t.fields, t.date) for t in audited_tickets]
    mw = minimum_wage_for(session.period_start)
    week = Workweek(_rebuild_days(parsed), mw)

    r = reconstruct(week)  # ← canonical numbers, ONCE
    sens = _sensitivity(parsed, mw)  # ← auxiliary derivative (throwaway reconstructs)

    agg = {
        "prod_h": sum((p.productive_hours for p in parsed), Decimal(0)),
        "nonprod_h": sum((p.nonproductive_hours for p in parsed), Decimal(0)),
        "rest_h": sum((p.rest_hours for p in parsed), Decimal(0)),
    }

    lower_bound = bool(session.missing_days)
    lb_prefix = (
        f"[LOWER BOUND — missing days {session.missing_days}; totals sum only observed tickets] "
        if lower_bound
        else ""
    )

    lines: list[ReceiptLine] = []
    for key, item in LINE_SPECS:
        amount = r[key]
        if amount == 0 and key != "piece_earnings":
            continue  # don't clutter the receipt with $0 non-piece lines
        line_sens = sens.get(key, {})
        lines.append(
            ReceiptLine(
                item=item,
                amount=str(amount),
                citation=CITATIONS[key],
                arithmetic=lb_prefix + _arith(key, r, agg, mw),
                sources=list(line_sens.keys()),
                dollar_sensitivity=line_sens,
            )
        )

    total_owed = r["total_owed"]
    amount_paid = _parse_amount_paid(session.stub)
    shortfall = usd(total_owed - amount_paid)

    return Receipt(
        session_id=session.session_id,
        lines=lines,
        total_owed=str(total_owed),
        amount_paid=str(amount_paid),
        shortfall=str(shortfall),
        claim_value=_claim_value(max(Decimal(0), shortfall), session.period_end, as_of),
        lower_bound=lower_bound,
        missing_days=list(session.missing_days),
        disclaimer=DISCLAIMER,
        chain_hash=None,  # set on persistence (history.store)
        residual_error=None,  # filled by WS-E error budget
    )


# ── convenience builder (shared by tests, WS-D mocks, the worked-receipt demo) ─
def build_session(
    session_id: str,
    period_start: str,
    period_end: str,
    days: list[dict],
    amount_paid,
    missing_days: list[str] | None = None,
    reconciliation_status: str = "ok",
) -> SessionExtraction:
    """days: [{date, rows:[{units,rate}], productive_hours, nonproductive_hours, rest_hours}].

    Emits unflagged/unanimous ExtractionResults in the canonical field schema that
    audit_session parses, so builder and parser can never drift apart.
    """
    from core.contracts import (
        Quality,
        Reconciliation,
        TicketExtraction,
    )

    def _er(field_id, value) -> ExtractionResult:
        return ExtractionResult(field_id, value, [value], True, False)

    tickets: list[TicketExtraction] = []
    for day in days:
        fields: list[ExtractionResult] = []
        for i, row in enumerate(day.get("rows", [])):
            fields.append(_er(f"rows[{i}].units", row["units"]))
            fields.append(_er(f"rows[{i}].rate", str(row["rate"])))
        for name in ("productive_hours", "nonproductive_hours", "rest_hours"):
            if name in day:
                fields.append(_er(name, str(day[name])))
        tickets.append(
            TicketExtraction(
                image=f"sha256/{session_id}-{day.get('date')}.png",
                date=day.get("date"),
                quality=Quality(0.95, "ok"),
                fields=fields,
            )
        )
    stub = {"fields": [_er("amount_paid", str(amount_paid))]}
    return SessionExtraction(
        session_id=session_id,
        period_start=period_start,
        period_end=period_end,
        tickets=tickets,
        stub=stub,
        reconciliation=Reconciliation(reconciliation_status, []),
        missing_days=missing_days or [],
    )


def demo() -> None:
    """Worked receipt: a 5-day week, one day below MW, rest periods, employer underpays."""
    import json

    days = [
        {"date": "2026-07-06", "rows": [{"units": 150, "rate": "2.00"}],
         "productive_hours": "7.5", "rest_hours": "0.5"},
        {"date": "2026-07-07", "rows": [{"units": 160, "rate": "2.00"}],
         "productive_hours": "7.5", "rest_hours": "0.5"},
        {"date": "2026-07-08", "rows": [{"units": 140, "rate": "2.00"}],
         "productive_hours": "7.5", "rest_hours": "0.5"},
        {"date": "2026-07-09", "rows": [{"units": 20, "rate": "2.00"}],
         "productive_hours": "7.5", "rest_hours": "0.5"},  # $40 over 7.5h → below MW
        {"date": "2026-07-10", "rows": [{"units": 155, "rate": "2.00"}],
         "productive_hours": "7.5", "rest_hours": "0.5"},
    ]
    session = build_session("demo-week", "2026-07-06", "2026-07-12", days, "1000.00")
    receipt = audit_session(session, as_of=date(2026, 7, 19))
    print("all pieces:", [l.item for l in receipt.lines])
    print(json.dumps(receipt.to_dict(), indent=2))
    assert Decimal(receipt.shortfall) > 0, "employer underpaid → positive shortfall expected"
    assert any(l.dollar_sensitivity for l in receipt.lines), "expected per-line dollar sensitivity"
    print("session_audit.py: worked-receipt self-check passed")


if __name__ == "__main__":
    demo()
