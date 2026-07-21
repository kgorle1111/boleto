"""Boleto WS-D — FastAPI backend for the pay-period audit SPA.

Bound to 127.0.0.1 ONLY (trust surface #34: nothing leaves the machine). Serves a
vanilla-JS SPA and holds pay-period sessions in server-side state so the F4 state
machine (CAPTURED→EXTRACTED→REVIEWED→COMPUTED→RECEIPT) is enforced HERE, not in the
browser: no path reaches RECEIPT while any flagged field is unconfirmed. The gate is
the frozen `SessionExtraction.has_unconfirmed_flags` — server re-uses it, never re-implements.

Run:  .venv-gemma/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8010
   or .venv-gemma/bin/python app/server.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

# WS-E: real WS-A/WS-B adapter is selected by env; default stays mock so WS-D's fixtures +
# F4 tests run without a GPU. Both adapters expose load_session(sid, variant) + build_receipt.
if os.environ.get("BOLETO_ADAPTER") == "real":
    from app import real_contracts as adapter
else:
    from app import mock_contracts as adapter

from core.contracts import is_pay_field
from core.wage_engine.history import ReceiptHistory

ROOT = Path(__file__).resolve().parent.parent
APP = Path(__file__).resolve().parent
DEMO = ROOT / "demo_session"
CROPS = DEMO / "crops"

HOST, PORT = "127.0.0.1", 8010

# WS-E: durable, hash-chained receipt store (WS-B history.py) replaces the in-memory list —
# receipts survive restart and are tamper-evident (each embeds the previous receipt's hash).
HIST = ReceiptHistory(str(DEMO / "history.db"), check_same_thread=False)

# es-MX default per §10 language policy; en second. TTS voices for the demo (macOS say).
# ponytail: server-side `say` is the demo seam. On device this becomes the platform TTS
# (Android es-MX voice / AVSpeechSynthesizer) — same call site, different backend.
VOICES = {"es": "Paulina", "en": "Samantha"}
_HEX = re.compile(r"^[0-9a-f]{64}$")

app = FastAPI(title="Boleto WS-D")


# ── server-side session state (F4 lives here) ────────────────────────────────
class Session:
    """One pay period. Reveal is paced by SSE; the truth (all tickets) is here from t0,
    so a browser refresh restores full state from GET /api/session/{id}."""

    def __init__(self, sid: str, variant: str = "ok") -> None:
        self.id = sid
        self.variant = variant
        self.ext = adapter.load_session(sid, variant)   # SessionExtraction (mock or real)
        self.confirmed: dict[str, str] = {}   # field id -> human-confirmed value
        self.state = "CAPTURED"
        self.created = time.time()

    # F4 gate — delegates to the FROZEN contract method; do not re-implement here.
    def gate_blocks(self) -> bool:
        return self.ext.has_unconfirmed_flags(set(self.confirmed))

    def flagged_fields(self) -> list[dict]:
        out = []
        for t in self.ext.tickets:
            for f in t.fields:
                if f.flagged:
                    out.append({"ticket": t.image, "date": t.date, **f.to_dict(),
                                "confirmed": f.field in self.confirmed,
                                "confirmed_value": self.confirmed.get(f.field)})
        return out

    def snapshot(self) -> dict:
        d = self.ext.to_dict()
        d["state"] = self.state
        d["confirmed"] = self.confirmed
        d["gate_blocks"] = self.gate_blocks()
        d["flagged"] = self.flagged_fields()
        return d


SESSIONS: dict[str, Session] = {}


def _stored(sid: str) -> bool:
    return HIST.conn.execute(
        "SELECT 1 FROM receipts WHERE session_id = ? LIMIT 1", (sid,)).fetchone() is not None


def _get(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if not s:
        raise HTTPException(404, "unknown session")
    return s


# ── SPA + static ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (APP / "templates" / "index.html").read_text()


app.mount("/static", StaticFiles(directory=str(APP / "static")), name="static")


# ── session lifecycle ────────────────────────────────────────────────────────
@app.post("/api/session")
async def create_session(request: Request) -> dict:
    """Create a pay-period session. Optional body {"variant": "mismatch"} loads the
    two-witness-fail session (reconciliation demo) whose receipt the F4 gate blocks."""
    variant = "ok"
    try:
        body = await request.json()
        if isinstance(body, dict):
            variant = str(body.get("variant", "ok"))
    except Exception:
        pass
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = Session(sid, variant)
    return {"session_id": sid, "state": "CAPTURED", "variant": variant,
            "period_start": SESSIONS[sid].ext.period_start,
            "period_end": SESSIONS[sid].ext.period_end,
            "n_tickets": len(SESSIONS[sid].ext.tickets)}


@app.get("/api/session/{sid}")
def get_session(sid: str) -> dict:
    return _get(sid).snapshot()


@app.get("/api/session/{sid}/events")
async def stream_extraction(sid: str) -> StreamingResponse:
    """SSE: reveal one extracted ticket at a time so the UI builds line-by-line
    (never a bare spinner — §8b). Idempotent: re-connecting replays all tickets."""
    s = _get(sid)

    async def gen():
        total = len(s.ext.tickets)
        for i, t in enumerate(s.ext.tickets):
            await asyncio.sleep(0.6)          # simulate per-ticket extraction time
            payload = {"index": i, "total": total, "ticket": t.to_dict(),
                       "has_flag": any(f.flagged for f in t.fields)}
            yield f"event: ticket\ndata: {json.dumps(payload)}\n\n"
        s.state = "EXTRACTED"
        yield f"event: done\ndata: {json.dumps({'state': s.state})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/session/{sid}/confirm")
async def confirm_field(sid: str, request: Request) -> dict:
    """Record a human-confirmation event for ONE flagged field. Trust boundary:
    only flagged fields are confirmable; value is required and length-capped."""
    s = _get(sid)
    body = await request.json()
    field = str(body.get("field", ""))
    if "value" not in body:
        raise HTTPException(422, "value required")
    value = str(body["value"])[:40]
    flagged_ids = {f.field for t in s.ext.tickets for f in t.fields if f.flagged}
    flagged_ids |= {f.field for f in s.ext.stub.get("fields", []) if f.flagged}
    # duplicate-photo groups are resolvable via the "dedup:<gid>" acknowledgment token
    flagged_ids |= {f"dedup:{t.dedup_group}" for t in s.ext.tickets if t.dedup_group}
    if field not in flagged_ids:
        raise HTTPException(422, f"field '{field}' is not a flagged field")
    # trust boundary (WS-F finding 6): a pay-bearing field must get a numeric
    # confirmation — reject text with a 4xx instead of crashing Decimal() in the engine.
    if is_pay_field(field) and not field.startswith("dedup:"):
        try:
            float(value)
        except ValueError:
            raise HTTPException(
                422, f"field '{field}' is numeric — got {value!r}; enter a number")
    s.confirmed[field] = value
    if not s.gate_blocks():
        s.state = "REVIEWED"
    return {"confirmed": field, "value": value, "gate_blocks": s.gate_blocks(),
            "state": s.state}


@app.get("/api/session/{sid}/receipt")
def get_receipt(sid: str) -> JSONResponse:
    """THE F4 GATE. Refuses (409) while any flagged field is unconfirmed — this is a
    server-side invariant, not UI courtesy. No client can bypass it."""
    s = _get(sid)
    if s.gate_blocks():
        unconfirmed = [f["field"] for f in s.flagged_fields() if not f["confirmed"]]
        return JSONResponse(
            status_code=409,
            content={"error": "gate_blocked",
                     "reason": "unconfirmed flagged field(s) — human confirmation required",
                     "unconfirmed": unconfirmed,
                     "reconciliation": s.ext.reconciliation.status})
    s.state = "COMPUTED"
    # Real WS-B audit of the confirmed session (mock adapter returns the fixture). The
    # human-confirmed corrections are applied inside build_receipt before the arithmetic.
    try:
        receipt = adapter.build_receipt(s.ext, s.confirmed)
    except ValueError as e:
        # named state, not a 500 (WS-F finding 4): e.g. a 1-day session, which the
        # engine correctly refuses (§226.2 weekly-average math needs the whole week).
        s.state = "EXTRACTED"
        return JSONResponse(status_code=422,
                            content={"error": "audit_refused", "reason": str(e)})
    # Persist to the durable hash-chained store (sets receipt.chain_hash → tamper-evident).
    if not _stored(sid):
        HIST.store(receipt)
    rec = receipt.to_dict()
    rec["confirmed"] = s.confirmed
    s.state = "RECEIPT"
    rec["state"] = s.state
    return JSONResponse(rec)


# ── provenance: field id -> its crop + K reads (source pixels) ───────────────
@app.get("/api/session/{sid}/field/{field_id:path}")
def get_field(sid: str, field_id: str) -> dict:
    s = _get(sid)
    for t in s.ext.tickets:
        for f in t.fields:
            if f.field == field_id:
                return {**f.to_dict(), "ticket": t.image, "date": t.date}
    for f in s.ext.stub.get("fields", []):
        if f.field == field_id:
            return f.to_dict()
    raise HTTPException(404, "unknown field")


# ── content-addressed crop images ────────────────────────────────────────────
@app.get("/crop/sha256/{h}.png")
def crop(h: str) -> FileResponse:
    if not _HEX.match(h):                      # trust boundary: no path traversal
        raise HTTPException(400, "bad crop id")
    p = CROPS / f"{h}.png"
    if not p.exists():
        raise HTTPException(404, "no such crop")
    return FileResponse(p, media_type="image/png")


@app.get("/ticket/{name}")
def ticket_img(name: str) -> FileResponse:
    if not re.match(r"^(day_0[1-9]|stub)\.png$", name):
        raise HTTPException(400, "bad ticket name")
    return FileResponse(DEMO / ("tickets/" + name if name.startswith("day") else name),
                        media_type="image/png")


# ── TTS: spoken receipt (macOS say, per active language) ─────────────────────
@app.post("/api/tts")
async def tts(request: Request) -> FileResponse:
    body = await request.json()
    lang = body.get("lang", "es")
    text = str(body.get("text", ""))[:600]     # cap; passed as argv (no shell) → injection-safe
    voice = VOICES.get(lang, VOICES["es"])
    out = Path(tempfile.gettempdir()) / f"boleto_tts_{uuid.uuid4().hex[:8]}.wav"
    try:
        subprocess.run(
            ["say", "-v", voice, "--file-format=WAVE",
             "--data-format=LEI16@22050", "-o", str(out), text],
            check=True, capture_output=True, timeout=30)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        # honest failure: no audio rather than a fake success
        raise HTTPException(503, f"tts unavailable: {type(e).__name__}") from e
    return FileResponse(out, media_type="audio/wav", filename="receipt.wav")


# ── history + wipe ───────────────────────────────────────────────────────────
@app.get("/api/history")
def history() -> dict:
    """Past receipts + the pattern banner, read from the durable hash-chained store.
    `pattern` keeps WS-D's shape; `summary`/`chain_verified` expose the tamper-evidence."""
    ps = HIST.pattern_summary()
    receipts = [json.loads(r[0]) for r in
                HIST.conn.execute("SELECT receipt_json FROM receipts ORDER BY seq")]
    return {"receipts": receipts,
            "pattern": {"shorted": ps["shorted_periods"], "total": ps["total_periods"],
                        "amount": ps["cumulative_shortfall"]},
            "summary": ps["summary"],
            "chain_verified": HIST.verify_chain()}


@app.post("/api/wipe")
def wipe() -> dict:
    """One-tap wipe-everything: clears in-flight sessions AND the durable receipt store."""
    n = len(SESSIONS)
    SESSIONS.clear()
    n += HIST.wipe()   # secure_delete + VACUUM — nothing lingers in freelist pages
    return {"wiped": True, "cleared": n}


# ── trust surface (#34) + eval screen (§8.2) ─────────────────────────────────
@app.get("/api/trust")
def trust() -> dict:
    return {"bind": f"{HOST}:{PORT}",
            "loopback_only": HOST in ("127.0.0.1", "localhost"),
            "verify_cmd": f"lsof -nP -iTCP:{PORT} -sTCP:LISTEN   # shows {HOST} only",
            "note": "No outbound sockets in the runtime dependency graph. "
                    "Turn Wi-Fi off and reload — it still works."}


@app.get("/api/evals")
def evals() -> dict:
    """Serve the latest WS-C eval report, normalized to the flat EvalReport shape.
    WS-C wraps the report under a `report` key and keeps calibration at top level;
    unwrap both so the SPA sees one flat object whether or not WS-C has landed."""
    results = ROOT / "evals" / "results"
    reports = sorted(results.glob("*.json")) if results.exists() else []
    real = [r for r in reports if "real" in r.name]   # prefer the real-model run for the demo
    pick = (real or reports)[-1] if reports else None
    if pick:
        raw = json.loads(pick.read_text())
        rep = dict(raw.get("report", raw))
        rep["calibration"] = rep.get("calibration") or raw.get("calibration") or []
        rep["_source"] = pick.name
        return rep
    return {**json.loads((APP / "mock_eval_report.json").read_text()), "_source": "mock"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
