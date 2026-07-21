"""Boleto §10.2 frozen interface contracts — the data types every workstream shares.

FROZEN: changing a field here is a cross-workstream change — update every consumer
in the same edit. WS-A produces ExtractionResult/SessionExtraction; WS-B produces
Receipt; WS-C produces EvalReport. WS-D/WS-E consume them.

Money is ALWAYS a str of a Decimal on any legal-arithmetic path (never float).
These dataclasses are plain stdlib — no pydantic — with to_dict/from_dict so any
workstream can serialize to the JSON shapes in plan §10.2 without a dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ── WS-A: extraction ────────────────────────────────────────────────────────
@dataclass
class ExtractionResult:
    """One extracted field with its self-consistency evidence."""
    field: str                       # e.g. "rows[0].units", "productive_hours"
    value: Any                       # the voted value (int/float/str/list)
    reads: list[Any]                 # every raw model read for this field (K samples)
    unanimous: bool
    flagged: bool                    # True => must be human-confirmed before receipt
    flag_reason: str | None = None   # "self_consistency_disagreement" | "checksum" | ...
    crop_image: str | None = None    # content-addressed path: "sha256/<hash>.png"
    prompt_version: str | None = None  # "ticket_field@3"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ExtractionResult":
        return ExtractionResult(**d)


@dataclass
class Quality:
    blur: float                      # Laplacian variance score (normalized 0..1)
    verdict: str                     # "ok" | "retake"
    reason: str | None = None        # why a retake was asked for


@dataclass
class TicketExtraction:
    image: str                       # "sha256/<hash>.png"
    date: str | None                 # YYYY-MM-DD or None if unread
    quality: Quality
    fields: list[ExtractionResult]
    dedup_group: str | None = None   # perceptual-hash cluster id, None if unique

    def to_dict(self) -> dict:
        return {
            "image": self.image,
            "date": self.date,
            "quality": asdict(self.quality),
            "fields": [f.to_dict() for f in self.fields],
            "dedup_group": self.dedup_group,
        }


@dataclass
class Reconciliation:
    status: str                      # "ok" | "mismatch" | "not_applicable"
    flags: list[dict] = field(default_factory=list)  # [{"type": "orphan_day", ...}]


@dataclass
class SessionExtraction:
    """A whole pay period: N day-tickets + one stub. WS-A → WS-B/WS-D."""
    session_id: str
    period_start: str                # YYYY-MM-DD
    period_end: str
    tickets: list[TicketExtraction]
    stub: dict                       # {"fields": [ExtractionResult, ...]}
    reconciliation: Reconciliation
    missing_days: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "tickets": [t.to_dict() for t in self.tickets],
            "stub": {"fields": [f.to_dict() for f in self.stub.get("fields", [])]},
            "reconciliation": {"status": self.reconciliation.status,
                               "flags": self.reconciliation.flags},
            "missing_days": self.missing_days,
        }

    def has_unconfirmed_flags(self, confirmed: set[str] | None = None) -> bool:
        """F4 safety helper: the HARD receipt gate.

        Blocks on (WS-F remediation, findings 3+5):
          * any flagged PAY-BEARING field not yet human-confirmed — non-pay flags
            (worker_id, date, row crop names) surface as soft review items but have
            zero receipt impact, so they must not hold the receipt hostage;
          * an unresolved reconciliation mismatch;
          * an unresolved duplicate-photo group: two tickets sharing a dedup_group
            would double-count into owed pay, so the group must be human-acknowledged
            via the confirmation token "dedup:<group_id>" before any receipt.
        """
        confirmed = confirmed or set()
        for t in self.tickets:
            for f in t.fields:
                if f.flagged and is_pay_field(f.field) and f.field not in confirmed:
                    return True
        for f in self.stub.get("fields", []):
            if f.flagged and is_pay_field(f.field) and f.field not in confirmed:
                return True
        if self.reconciliation.status == "mismatch":
            return True
        seen_groups: set[str] = set()
        for t in self.tickets:
            g = t.dedup_group
            if g is not None:
                if g in seen_groups and f"dedup:{g}" not in confirmed:
                    return True
                seen_groups.add(g)
        return False


# ── WS-B: receipt ───────────────────────────────────────────────────────────
@dataclass
class ReceiptLine:
    item: str                        # "rest_pay", "mw_makeup", "ot_premium", ...
    amount: str                      # str(Decimal), cents-exact
    citation: str                    # "LC §226.2(a)(3)"
    arithmetic: str                  # human-readable derivation
    sources: list[str] = field(default_factory=list)          # ExtractionResult.field ids
    dollar_sensitivity: dict = field(default_factory=dict)    # {"rows[0].units": "0.07/unit"}


@dataclass
class Receipt:
    """WS-B → WS-D. All money fields are str(Decimal)."""
    session_id: str
    lines: list[ReceiptLine]
    total_owed: str
    amount_paid: str
    shortfall: str
    claim_value: dict                # {"liquidated_damages","interest","citation"}
    lower_bound: bool                # True when the week is incomplete
    missing_days: list[str]
    disclaimer: str                  # es + en advocate line
    chain_hash: str | None = None    # hashlib chain to the previous receipt (E5)
    residual_error: str | None = None  # WS-E error-budget string, e.g. "< 0.1%"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Receipt":
        lines = [ReceiptLine(**ln) for ln in d.get("lines", [])]
        rest = {k: v for k, v in d.items() if k != "lines"}
        return Receipt(lines=lines, **rest)


# ── WS-C: eval report ───────────────────────────────────────────────────────
@dataclass
class EvalReport:
    """WS-C, appended to evals/results/. Money as str; rates as float 0..1."""
    date: str
    model: str                       # "gemma3:latest" | "mock" | "gemma-4-e4b-8bit"
    n: int
    field_exact_match: float
    conclusion_critical_rate: float
    escaped_rate: float
    dollar_weighted_escaped: str
    flag_rate: float
    per_severity: dict = field(default_factory=dict)
    calibration: list = field(default_factory=list)
    latency: dict = field(default_factory=dict)  # {"s_per_ticket","tokens_per_s","peak_mem_gb"}

    def to_dict(self) -> dict:
        return asdict(self)


# The pay-bearing fields — any mismatch here is conclusion-critical. Kept in one
# place so WS-A (flagging), WS-B (audit entry), and WS-C (scoring) agree exactly.
PAY_FIELDS = ("rows", "productive_hours", "nonproductive_hours", "rest_hours")

# Stub money fields that also carry the legal conclusion (paid vs owed).
_STUB_PAY_NAMES = ("piece_total", "amount_paid", "gross_pay", "net_pay", "hours_paid")


def is_pay_field(field_id: str) -> bool:
    """True iff a misread of this field changes owed pay. Accepts bare ids
    ('rows[0].units') and session-prefixed ids ('d3.rows[0].units', 'stub.amount_paid').
    Row 'crop' names, worker_id, and dates are NOT pay-bearing."""
    name = field_id.rsplit(".", 1)[-1]
    if "rows[" in field_id:
        return name in ("units", "rate")
    return name in PAY_FIELDS[1:] + _STUB_PAY_NAMES


def demo() -> None:
    """Self-check: round-trip every contract through to_dict/from_dict."""
    er = ExtractionResult("rows[0].units", 293, [293, 293, 298], False, True,
                          "self_consistency_disagreement", "sha256/ab.png", "ticket_field@3")
    assert ExtractionResult.from_dict(er.to_dict()) == er

    se = SessionExtraction(
        "s1", "2026-07-06", "2026-07-12",
        [TicketExtraction("sha256/a.png", "2026-07-06", Quality(0.91, "ok"), [er])],
        {"fields": []}, Reconciliation("ok", []), [])
    assert se.to_dict()["tickets"][0]["fields"][0]["value"] == 293
    assert se.has_unconfirmed_flags() is True                       # er is flagged
    assert se.has_unconfirmed_flags({"rows[0].units"}) is False     # ...until confirmed

    # pay-field routing (WS-F finding 5): non-pay flags do NOT hard-block
    assert is_pay_field("rows[0].units") and is_pay_field("d3.rows[1].rate")
    assert is_pay_field("productive_hours") and is_pay_field("stub.amount_paid")
    assert not is_pay_field("worker_id") and not is_pay_field("rows[0].crop")
    soft = ExtractionResult("worker_id", None, [None, None], True, True, "schema:unreadable")
    se_soft = SessionExtraction(
        "s2", "2026-07-06", "2026-07-12",
        [TicketExtraction("sha256/b.png", "2026-07-06", Quality(0.9, "ok"), [soft])],
        {"fields": []}, Reconciliation("ok", []), [])
    assert se_soft.has_unconfirmed_flags() is False   # worker_id flag is soft

    # duplicate-photo gate (WS-F finding 3): shared dedup_group blocks until acknowledged
    t1 = TicketExtraction("sha256/c.png", "2026-07-07", Quality(0.9, "ok"), [], "dup0")
    t2 = TicketExtraction("sha256/d.png", "2026-07-07", Quality(0.9, "ok"), [], "dup0")
    se_dup = SessionExtraction("s3", "2026-07-06", "2026-07-12", [t1, t2],
                               {"fields": []}, Reconciliation("ok", []), [])
    assert se_dup.has_unconfirmed_flags() is True
    assert se_dup.has_unconfirmed_flags({"dedup:dup0"}) is False

    rc = Receipt("s1", [ReceiptLine("rest_pay", "20.00", "LC §226.2(a)(3)",
                 "0.5h × $40", ["rows[0].units"], {"rows[0].units": "0.07/unit"})],
                 "320.00", "300.00", "20.00",
                 {"liquidated_damages": "0.00", "interest": "0.00", "citation": "LC §1194.2"},
                 False, [], "es+en", None)
    assert Receipt.from_dict(rc.to_dict()).total_owed == "320.00"

    ev = EvalReport("2026-07-19", "mock", 8, 0.9, 0.12, 0.02, "1.25", 0.25)
    assert ev.to_dict()["escaped_rate"] == 0.02
    print("contracts.py: all round-trip self-checks passed")


if __name__ == "__main__":
    demo()
