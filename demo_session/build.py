"""Build the prepared demo pay-period session for WS-D.

Produces, under demo_session/:
  tickets/day_01..05.png   5 day-tickets (day_03 deliberately SMUDGED → triggers review)
  stub.png                 the printed paystub
  crops/<sha>.png          content-addressed field crops referenced by the fixture
  session.json             a SessionExtraction.to_dict() matching core/contracts.py (FROZEN)
  receipt.json             a Receipt.to_dict() — what WS-B would return once the gate passes

Everything here is MOCK data shaped to the frozen §10.2 contracts. WS-E swaps
session.json/receipt.json for the real WS-A/WS-B output; the server code does not change.

Run:  .venv-gemma/bin/python demo_session/build.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.contracts import (  # noqa: E402  (path insert above)
    ExtractionResult,
    Quality,
    Receipt,
    ReceiptLine,
    Reconciliation,
    SessionExtraction,
    TicketExtraction,
)

DEMO = ROOT / "demo_session"
TICKETS_SRC = ROOT / "tickets"
OUT_TICKETS = DEMO / "tickets"
OUT_CROPS = DEMO / "crops"


def _sha_png(img: Image.Image) -> tuple[str, bytes]:
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    return hashlib.sha256(data).hexdigest(), data


def _save_crop(img: Image.Image) -> str:
    """Save a crop content-addressed; return its 'sha256/<hash>.png' id."""
    h, data = _sha_png(img)
    (OUT_CROPS / f"{h}.png").write_bytes(data)
    return f"sha256/{h}.png"


def _band(src: Image.Image, top_frac: float, bot_frac: float) -> Image.Image:
    w, h = src.size
    return src.crop((0, int(h * top_frac), w, int(h * bot_frac)))


def _smudge(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    # a grey smear across the middle-right — where the units digit lives
    d.ellipse((int(w * 0.55), int(h * 0.30), int(w * 0.95), int(h * 0.75)),
              fill=(90, 80, 70, 130))
    d.line((int(w * 0.55), int(h * 0.6), int(w * 0.95), int(h * 0.45)),
           fill=(60, 55, 50, 160), width=6)
    return img


def build() -> None:
    OUT_TICKETS.mkdir(parents=True, exist_ok=True)
    OUT_CROPS.mkdir(parents=True, exist_ok=True)
    for p in OUT_CROPS.glob("*.png"):
        p.unlink()

    srcs = sorted(TICKETS_SRC.glob("ticket_0*.png"))[:6]
    if len(srcs) < 6:
        raise SystemExit("need >=6 tickets/ticket_0*.png to build the demo session")

    days = srcs[:5]
    stub_src = srcs[5]

    # ---- write the 5 day-ticket images; day_03 is smudged ----
    for i, s in enumerate(days, start=1):
        img = Image.open(s).convert("RGB")
        if i == 3:
            img = _smudge(img)
        img.save(OUT_TICKETS / f"day_{i:02d}.png")
    shutil.copy(stub_src, DEMO / "stub.png")

    # ---- crops for the 3 fields that appear in review / provenance ----
    day3 = Image.open(OUT_TICKETS / "day_03.png").convert("RGB")
    day2 = Image.open(OUT_TICKETS / "day_02.png").convert("RGB")
    crop_units = _save_crop(_band(day3, 0.30, 0.55))   # the smudged units cell
    crop_rate = _save_crop(_band(day3, 0.55, 0.72))
    crop_rest = _save_crop(_band(day2, 0.72, 0.92))

    # ---- fields, per ticket. Only day-3 units is flagged (self-consistency split). ----
    dates = ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10"]
    units = [212, 180, 293, 240, 205]
    rates = [2.25, 2.25, 2.00, 2.50, 2.25]
    prod = [8, 8, 7, 8, 6]
    rest = [0.5, 0.5, 0.5, 0.5, 0.5]

    def ok(field: str, value, crop=None) -> ExtractionResult:
        return ExtractionResult(field=field, value=value, reads=[value, value],
                                unanimous=True, flagged=False, crop_image=crop,
                                prompt_version="ticket_field@1")

    tickets: list[TicketExtraction] = []
    for i in range(5):
        d = i + 1
        fields = [
            ok(f"d{d}.date", dates[i]),
            ok(f"d{d}.rows[0].units", units[i],
               crop_units if d == 3 else None),
            ok(f"d{d}.rows[0].rate", rates[i], crop_rate if d == 3 else None),
            ok(f"d{d}.productive_hours", prod[i]),
            ok(f"d{d}.rest_hours", rest[i], crop_rest if d == 2 else None),
        ]
        if d == 3:
            # THE money moment: two reads say 293, one says 298 → refuses to conclude
            fields[1] = ExtractionResult(
                field="d3.rows[0].units", value=293, reads=[293, 293, 298],
                unanimous=False, flagged=True,
                flag_reason="self_consistency_disagreement",
                crop_image=crop_units, prompt_version="ticket_field@1")
        tickets.append(TicketExtraction(
            image=f"local/day_{d:02d}.png", date=dates[i],
            quality=Quality(blur=0.34 if d == 3 else 0.88,
                            verdict="ok",
                            reason="smudge over units cell" if d == 3 else None),
            fields=fields, dedup_group=None))

    stub = {"fields": [ok("stub.gross_pay", "300.00"),
                       ok("stub.piece_units", 1130)]}

    session = SessionExtraction(
        session_id="demo",  # server assigns a real id on load; placeholder
        period_start="2026-07-06", period_end="2026-07-12",
        tickets=tickets, stub=stub,
        reconciliation=Reconciliation(status="ok", flags=[]),
        missing_days=[])

    (DEMO / "session.json").write_text(json.dumps(session.to_dict(), indent=2))

    # ---- the reconciliation-MISMATCH variant (WS-E: the two-witness-fail demo) ----
    # Same week, but NO per-field flag (day-3 units reads cleanly) — so the ONLY thing
    # blocking the receipt is the two-witness reconcile: ticket Σ piece disagrees with the
    # stub's printed piece line. has_unconfirmed_flags() → True via reconciliation.status,
    # and there is no field to "confirm" → an unresolved mismatch NEVER prints a receipt.
    mm_tickets: list[TicketExtraction] = []
    for i in range(5):
        d = i + 1
        fields = [
            ok(f"d{d}.date", dates[i]),
            ok(f"d{d}.rows[0].units", units[i], crop_units if d == 3 else None),
            ok(f"d{d}.rows[0].rate", rates[i], crop_rate if d == 3 else None),
            ok(f"d{d}.productive_hours", prod[i]),
            ok(f"d{d}.rest_hours", rest[i], crop_rest if d == 2 else None),
        ]
        mm_tickets.append(TicketExtraction(
            image=f"local/day_{d:02d}.png", date=dates[i],
            quality=Quality(blur=0.88, verdict="ok"), fields=fields, dedup_group=None))
    ticket_piece = sum(units[i] * rates[i] for i in range(5))
    stub_piece = round(ticket_piece + 137.0, 2)  # employer's stub claims $137 more units
    mm_stub = {"fields": [ok("stub.gross_pay", "300.00"),
                          ok("stub.piece_total", stub_piece)]}
    mismatch = SessionExtraction(
        session_id="demo-mismatch", period_start="2026-07-06", period_end="2026-07-12",
        tickets=mm_tickets, stub=mm_stub,
        reconciliation=Reconciliation(status="mismatch", flags=[{
            "type": "amount_mismatch",
            "detail": f"ticket Σ piece = {round(ticket_piece, 2)}, stub piece line = {stub_piece}",
            "ticket_total": round(ticket_piece, 2), "stub_total": stub_piece}]),
        missing_days=[])
    (DEMO / "session_mismatch.json").write_text(json.dumps(mismatch.to_dict(), indent=2))
    assert mismatch.has_unconfirmed_flags() is True, "mismatch must block the receipt"
    assert not any(f.flagged for t in mismatch.tickets for f in t.fields), \
        "mismatch demo isolates the two-witness gate: no per-field flags"

    # ---- the receipt WS-B would return once every flag is human-confirmed ----
    receipt = Receipt(
        session_id="demo",
        lines=[
            ReceiptLine(
                item="piece_earnings", amount="300.00", citation="LC §226.2(a)",
                arithmetic="Σ(units × rate) across 5 days = 300.00",
                sources=["d3.rows[0].units", "d3.rows[0].rate"],
                dollar_sensitivity={"d3.rows[0].units": "2.00/unit",
                                    "d3.rows[0].rate": "293.00/$1"}),
            ReceiptLine(
                item="rest_pay", amount="20.00", citation="LC §226.2(a)(3)",
                arithmetic="0.5h × $40.00/h (weekly-average rate, above minimum wage)",
                sources=["d2.rest_hours"],
                dollar_sensitivity={"d2.rest_hours": "40.00/h"}),
        ],
        total_owed="320.00", amount_paid="300.00", shortfall="20.00",
        claim_value={"liquidated_damages": "20.00", "interest": "2.00",
                     "citation": "LC §1194.2, §98.1"},
        lower_bound=False, missing_days=[],
        disclaimer="__localized__",  # WS-D renders es/en from strings.json
        chain_hash=None, residual_error="< 0.5%")

    (DEMO / "receipt.json").write_text(json.dumps(receipt.to_dict(), indent=2))

    print("demo_session built:")
    print(f"  tickets: {len(tickets)} (day_03 smudged, day3.units flagged)")
    print(f"  crops:   {len(list(OUT_CROPS.glob('*.png')))}")
    print(f"  gate:    has_unconfirmed_flags() = {session.has_unconfirmed_flags()}")
    assert session.has_unconfirmed_flags() is True
    assert session.has_unconfirmed_flags({"d3.rows[0].units"}) is False
    print("  self-check: gate opens only after d3.rows[0].units is confirmed  OK")


if __name__ == "__main__":
    build()
