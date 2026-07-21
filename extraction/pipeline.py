"""Boleto WS-A extraction pipeline — photo(s) → SessionExtraction (§10.2 contract).

The LLM does extraction ONLY. Every mechanism here is deterministic guard code:
per-field crops, self-consistency voting, a quality pre-gate, perceptual-hash
dedup, a cross-field checksum with a single-digit-repair *suggestion*, and a
two-witness ticket↔stub reconciliation. Money-adjacent values are extracted, never
computed — the wage engine (WS-B) does all arithmetic.

Trust boundary: image bytes and model output are UNTRUSTED. Every field the model
emits is schema-validated; malformed output is flagged for human review — the
pipeline never crashes, never guesses, never silently drops evidence.

Model access goes through core.model_client.read_json ONLY (never a raw ollama
call). Run everything with BOLETO_MODEL=mock (whole-ticket oracle, no GPU); one
small real smoke run proves the gemma3 path.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.contracts import (
    ExtractionResult,
    Quality,
    Reconciliation,
    SessionExtraction,
    TicketExtraction,
)
from core.model_client import DEFAULT_MODEL, read_json

_ROOT = Path(__file__).resolve().parent.parent
_FORMAT_0 = _ROOT / "core" / "extraction" / "formats" / "format_0.json"
_TICKET_PROMPT = (_ROOT / "core" / "extraction" / "prompts" / "ticket_field.txt")
_TICKET_FIELD_VERSION = _TICKET_PROMPT.read_text().splitlines()[0].strip()  # "ticket_field@1"
# Whole-ticket fallback prompt (WS-C's versioned file). The per-field-crop flow above is
# the SHIPPING design for real handwritten tickets with proper cell layout; but on the
# PRINTED synthetic set gemma3 returns {} on 26px crops (WS-A wound), while a whole-ticket
# read scores field-exact 0.975 (WS-C). So whole_ticket=True is a graceful mode that reads
# the whole image and slices fields out of it — same downstream voting/validation/flagging.
_WHOLE_PROMPT_FILE = _ROOT / "evals" / "prompts" / "ticket_whole.txt"
_WHOLE_VERSION = _WHOLE_PROMPT_FILE.read_text().splitlines()[0].strip()  # "ticket_whole@1"
_WHOLE_PROMPT = _WHOLE_PROMPT_FILE.read_text().split("---", 1)[1].strip()

# Quality pre-gate thresholds. kn: hand-set against the printed synthetic set;
# recalibrate on WS-C's parametric degradation suite (the true blur/glare floor).
_BLUR_MIN_VAR = 12.0          # Laplacian variance below this ⇒ too blurry
_CLIP_MAX_FRAC = 0.35         # >35% of pixels pinned at 0/255 ⇒ over/under-exposed
# Perceptual-hash Hamming distance at/under which two photos are near-duplicates.
_DEDUP_MAX_HAMMING = 6


# ── layout: field boxes from format_0.json ──────────────────────────────────
def _load_format() -> dict:
    return json.loads(_FORMAT_0.read_text())


def _field_boxes(fmt: dict) -> list[tuple[str, tuple[int, int, int, int]]]:
    """(field_path, pixel box) for every crop we transcribe, from the layout spec.
    Rows are templated up to max_rows; absent rows read null and are dropped."""
    boxes: list[tuple[str, tuple[int, int, int, int]]] = []
    hx0, hy0, hx1, hy1 = fmt["header"]["box"]
    boxes.append(("worker_id", (hx0, hy0, hx1, hy1)))
    boxes.append(("date", (hx0, hy0, hx1, hy1)))  # same header crop, two values
    r = fmt["rows"]
    for i in range(r["max_rows"]):
        top = r["start_y"] + i * r["stride_y"]
        for cell in ("crop", "units", "rate"):
            cx0, cy0, cx1, cy1 = r["cell_boxes"][cell]
            boxes.append((f"rows[{i}].{cell}", (cx0, top + cy0, cx1, top + cy1)))
    for hf in ("productive_hours", "nonproductive_hours", "rest_hours"):
        boxes.append((hf, tuple(fmt[hf]["box"])))
    return boxes


# ── image quality pre-gate (#11) ────────────────────────────────────────────
def _grayscale(path: Path):
    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(path).convert("L"), dtype=float)


def quality_gate(path: str | Path) -> Quality:
    """Classical-CV retake gate BEFORE inference: Laplacian-variance blur + a
    histogram-clipping exposure check. Pushes errors to the cheapest layer —
    a rejected photo costs 2s, a misread digit costs the pipeline."""
    import numpy as np

    g = _grayscale(Path(path))
    # discrete Laplacian via 4-neighbour shifts (no scipy dependency)
    lap = (-4 * g
           + np.roll(g, 1, 0) + np.roll(g, -1, 0)
           + np.roll(g, 1, 1) + np.roll(g, -1, 1))
    var = float(lap[1:-1, 1:-1].var())
    dark = float((g <= 2).mean())    # pinned-black fraction (underexposed / shadow)
    bright = float((g >= 253).mean())  # pinned-white fraction
    blur_norm = round(min(1.0, var / (_BLUR_MIN_VAR * 8)), 3)  # 0..1 display score
    if var < _BLUR_MIN_VAR:
        return Quality(blur_norm, "retake", f"too blurry (lap var {var:.1f} < {_BLUR_MIN_VAR})")
    # A document is legitimately mostly white; only real over/under-exposure retakes:
    # too much crushed-black (shadow/underexposure), or blown out with no ink to read.
    if dark > _CLIP_MAX_FRAC:
        return Quality(blur_norm, "retake", f"bad exposure ({dark:.0%} crushed to black)")
    if bright > 0.995 and dark < 0.003:
        return Quality(blur_norm, "retake", "blown out — no readable ink")
    return Quality(blur_norm, "ok", None)


# ── perceptual-hash dedup (#10) ─────────────────────────────────────────────
def dedup_groups(paths: list[str | Path]) -> list[str | None]:
    """Cluster near-duplicate photos by pHash Hamming distance. Duplicates are
    FLAGGED (shared group id), never silently dropped — a double-photographed day
    must not double-count, but the human decides which to keep."""
    import imagehash
    from PIL import Image

    hashes = [imagehash.phash(Image.open(p)) for p in paths]
    group_of: list[str | None] = [None] * len(paths)
    next_gid = 0
    for i in range(len(paths)):
        for j in range(i):
            if hashes[i] - hashes[j] <= _DEDUP_MAX_HAMMING:
                gid = group_of[j] or f"dup{next_gid}"
                if group_of[j] is None:
                    group_of[j] = gid
                    next_gid += 1
                group_of[i] = gid
                break
    return group_of


# ── content-addressed crop store ────────────────────────────────────────────
def _save_crop(path: Path, box: tuple[int, int, int, int], crops_dir: Path) -> str:
    from PIL import Image

    crops_dir.mkdir(parents=True, exist_ok=True)
    im = Image.open(path).convert("RGB").crop(box)
    data = im.tobytes()
    h = hashlib.sha256(data).hexdigest()[:16]
    out = crops_dir / f"{h}.png"
    if not out.exists():
        im.save(out)
    return f"sha256/{h}.png"


# ── one raw field read (the ONLY place mock vs real diverge) ─────────────────
def _navigate(obj: dict, field_path: str) -> Any:
    """Pull field_path (e.g. 'rows[0].units') out of a whole-ticket dict."""
    cur: Any = obj
    for part in field_path.replace("]", "").split("."):
        if "[" in part:
            name, idx = part.split("[")
            cur = cur.get(name) if isinstance(cur, dict) else None
            if not isinstance(cur, list) or int(idx) >= len(cur):
                return None
            cur = cur[int(idx)]
        else:
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


def _field_prompt(field_path: str) -> str:
    name = field_path.split(".")[-1].split("[")[0]
    body = _TICKET_PROMPT.read_text().split("---", 1)[1].strip()
    return body.replace("{field_name}", name)


def _read_once(source_image: Path, field_path: str, crop_path: Path | None,
               model: str, temperature: float) -> tuple[Any, dict]:
    """One model read of one field. Returns (raw_value, meta)."""
    if model == "mock":
        # kn: the mock is a WHOLE-TICKET ORACLE (reads truth.json by index, ignores
        # pixels), so a per-field read slices the field out of the oracle rather than
        # reading the crop. The real path below reads the crop pixels. Everything
        # downstream (voting/validation/flagging) is identical on both paths.
        obj, meta = read_json(source_image, "", model="mock", temperature=temperature)
        return _navigate(obj, field_path), meta
    obj, meta = read_json(crop_path or source_image, _field_prompt(field_path),
                          model=model, temperature=temperature)
    return (obj.get("value") if isinstance(obj, dict) else None), meta


# ── schema validation (trust boundary) ──────────────────────────────────────
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid(field_path: str, value: Any) -> tuple[bool, str | None]:
    name = field_path.split(".")[-1].split("[")[0]
    if value is None:
        return False, "unreadable"
    try:
        if name == "units":
            return (int(value) == value and int(value) >= 0), "bad_units"
        if name == "rate":
            return (float(value) >= 0), "bad_rate"
        if name.endswith("_hours"):
            return (float(value) >= 0), "bad_hours"
        if name == "date":
            return bool(_DATE_RE.match(str(value))), "bad_date"
        if name in ("worker_id", "crop"):
            return (isinstance(value, str) and len(value) > 0), "bad_string"
        # stub money/hour fields (WS-F finding 2): must be numeric — "N/A", "1,234.00",
        # bools, lists are all rejected here instead of crashing reconcile/audit later.
        if name in ("piece_total", "hours_paid", "amount_paid"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False, "bad_number"
            return (float(value) >= 0), "bad_number"
        if name in ("period_start", "period_end"):
            return bool(_DATE_RE.match(str(value))), "bad_date"
    except (TypeError, ValueError):
        return False, "schema_invalid"
    return True, None


# ── self-consistency ensemble, per field (#2, #6) with adaptive sampling ─────
def _key(v: Any) -> str:
    return json.dumps(v, sort_keys=True)


@dataclass
class _EnsembleStats:
    calls: int = 0
    fields: int = 0


def ensemble_field(field_path: str, read_once: Callable[[], Any], k: int = 3,
                   tta_variants: list[Callable[[], Any]] | None = None,
                   stats: _EnsembleStats | None = None) -> tuple[list[Any], Any, bool]:
    """K reads with ADAPTIVE SAMPLING: 2 reads, a 3rd only on disagreement.
    Optional test-time augmentation (#9) adds one read per variant to the vote
    pool. Returns (all_reads, majority_value, unanimous)."""
    reads = [read_once(), read_once()]
    if stats:
        stats.calls += 2
        stats.fields += 1
    if _key(reads[0]) != _key(reads[1]) and k >= 3:
        reads.append(read_once())
        if stats:
            stats.calls += 1
    if tta_variants:
        for v in tta_variants:
            reads.append(v())
            if stats:
                stats.calls += 1
    votes = Counter(_key(r) for r in reads)
    top_key, top_n = votes.most_common(1)[0]
    value = json.loads(top_key)
    unanimous = top_n == len(reads)
    return reads, value, unanimous


def _tta_reads(source_image: Path, field_path: str, crop_path: Path | None,
               model: str) -> list[Callable[[], Any]]:
    """Test-time augmentation variants (#9): re-read mild rotation/contrast of the
    crop. On mock (pixel-blind oracle) these are no-ops; kept so the flag composes
    with the ensemble on the real path. Measure-and-keep is WS-C's call."""
    if model == "mock" or crop_path is None:
        return [lambda: _read_once(source_image, field_path, crop_path, model, 0.7)[0]]
    from PIL import Image, ImageEnhance

    variants: list[Callable[[], Any]] = []
    for angle, contrast in ((2, 1.0), (-2, 1.15)):
        def make(a: float, c: float) -> Callable[[], Any]:
            def run() -> Any:
                im = Image.open(crop_path).rotate(a, expand=False, fillcolor="white")
                im = ImageEnhance.Contrast(im).enhance(c)
                tmp = crop_path.with_name(f"aug_{a}_{c}_{crop_path.name}")
                im.save(tmp)
                return _read_once(source_image, field_path, tmp, model, 0.7)[0]
            return run
        variants.append(make(angle, contrast))
    return variants


# ── whole-ticket fallback (graceful mode; #1 note) ──────────────────────────
def _whole_ticket_reads(path: Path, model: str, k: int, temperature: float,
                        stats: _EnsembleStats | None, adaptive: bool = True) -> list[dict]:
    """K whole-image reads. ADAPTIVE (the §8b lever): 2 reads, a 3rd only if the two whole
    dicts disagree anywhere; non-adaptive reads K every time. Model output is UNTRUSTED — a
    non-dict read becomes {} (every field then navigates to None → flagged, never a crash)."""
    def one() -> dict:
        obj, _m = read_json(path, _WHOLE_PROMPT, model=model, temperature=temperature)
        if stats:
            stats.calls += 1
        return obj if isinstance(obj, dict) else {}

    if not adaptive:
        return [one() for _ in range(k)]
    reads = [one(), one()]
    if k >= 3 and _key(reads[0]) != _key(reads[1]):
        reads.append(one())
    return reads


# ── one ticket → TicketExtraction ───────────────────────────────────────────
def extract_ticket(path: str | Path, crops_dir: Path, model: str | None = None,
                   k: int = 3, temperature: float = 0.7, tta: bool = False,
                   dedup_group: str | None = None, whole_ticket: bool = False,
                   adaptive: bool = True,
                   stats: _EnsembleStats | None = None) -> TicketExtraction:
    model = model or DEFAULT_MODEL
    path = Path(path)
    quality = quality_gate(path)
    fmt = _load_format()
    results: list[ExtractionResult] = []
    ticket_hash = f"sha256/{hashlib.sha256(path.read_bytes()).hexdigest()[:16]}.png"
    date_value: str | None = None

    if quality.verdict == "retake":
        # Don't infer on a photo the gate rejected — cheapest layer wins.
        return TicketExtraction(ticket_hash, None, quality, [], dedup_group)

    # Graceful whole-ticket mode: read the whole image K times, slice each field out of
    # the K dicts, vote per field. Crops are still saved so the review screen keeps its
    # provenance images. Everything downstream (validate/flag) is identical to per-field.
    if whole_ticket and model != "mock":
        wholes = _whole_ticket_reads(path, model, k, temperature, stats, adaptive)
        row_seen = -1
        for field_path, box in _field_boxes(fmt):
            m = re.match(r"rows\[(\d+)\]", field_path)
            if m and int(m.group(1)) > row_seen + 1:
                continue
            crop = _save_crop(path, box, crops_dir)
            reads = [_navigate(w, field_path) for w in wholes]
            if stats:
                stats.fields += 1
            votes = Counter(_key(r) for r in reads)
            top_key, top_n = votes.most_common(1)[0]
            value = json.loads(top_key)
            unanimous = top_n == len(reads)
            if m and value is None:
                continue  # absent row cell
            if m:
                row_seen = max(row_seen, int(m.group(1)))
            ok, reason = _valid(field_path, value)
            flagged = (not unanimous) or (not ok)
            flag_reason = (f"schema:{reason}" if not ok
                           else "self_consistency_disagreement" if not unanimous else None)
            if field_path == "date" and ok:
                date_value = value
            results.append(ExtractionResult(
                field=field_path, value=value, reads=reads, unanimous=unanimous,
                flagged=flagged, flag_reason=flag_reason, crop_image=crop,
                prompt_version=_WHOLE_VERSION))
        return TicketExtraction(ticket_hash, date_value, quality, results, dedup_group)

    row_seen = -1
    for field_path, box in _field_boxes(fmt):
        # stop reading a row once an earlier row came back fully absent
        m = re.match(r"rows\[(\d+)\]", field_path)
        if m and int(m.group(1)) > row_seen + 1:
            continue
        crop = _save_crop(path, box, crops_dir)
        crop_file = crops_dir / crop.split("/")[-1]

        def read_once(fp=field_path, cf=crop_file) -> Any:
            return _read_once(path, fp, cf, model, temperature)[0]

        variants = _tta_reads(path, field_path, crop_file, model) if tta else None
        # first cheap probe: absent whole row → skip its cells
        reads, value, unanimous = ensemble_field(field_path, read_once, k, variants, stats)
        if m and value is None:
            continue  # absent row cell
        if m:
            row_seen = max(row_seen, int(m.group(1)))
        ok, reason = _valid(field_path, value)
        flagged = (not unanimous) or (not ok)
        flag_reason = None
        if not ok:
            flag_reason = f"schema:{reason}"
        elif not unanimous:
            flag_reason = "self_consistency_disagreement"
        if field_path == "date" and ok:
            date_value = value
        results.append(ExtractionResult(
            field=field_path, value=value, reads=reads, unanimous=unanimous,
            flagged=flagged, flag_reason=flag_reason, crop_image=crop,
            prompt_version=_TICKET_FIELD_VERSION))
    return TicketExtraction(ticket_hash, date_value, quality, results, dedup_group)


# ── reassemble a whole-ticket dict from field results (for checksum/recon) ───
def ticket_values(t: TicketExtraction) -> dict:
    out: dict[str, Any] = {"rows": []}
    rows: dict[int, dict] = {}
    for f in t.fields:
        m = re.match(r"rows\[(\d+)\]\.(\w+)", f.field)
        if m:
            rows.setdefault(int(m.group(1)), {})[m.group(2)] = f.value
        else:
            out[f.field] = f.value
    out["rows"] = [rows[i] for i in sorted(rows)]
    return out


def ticket_piece_total(vals: dict) -> float:
    return round(sum((r.get("units") or 0) * (r.get("rate") or 0)
                     for r in vals.get("rows", [])), 2)


# ── cross-field checksum + single-digit repair SUGGESTION (#15 / E1) ─────────
def suggest_single_digit_repair(vals: dict, target_total: float) -> list[dict]:
    """When Σ(units×rate) ≠ a known piece total, find the minimal ONE-DIGIT edit to
    a units field that restores arithmetic consistency. Returned as a SUGGESTION
    with evidence — NEVER auto-applied (the human decides). 'A misread digit has to
    defeat the ticket's own arithmetic.'"""
    if abs(ticket_piece_total(vals) - target_total) < 0.005:
        return []
    suggestions: list[dict] = []
    for ri, row in enumerate(vals.get("rows", [])):
        units, rate = row.get("units"), row.get("rate")
        if not isinstance(units, int) or not isinstance(rate, (int, float)):
            continue
        s = str(units)
        for pos in range(len(s)):
            for d in "0123456789":
                if d == s[pos]:
                    continue
                cand_s = s[:pos] + d + s[pos + 1:]
                cand = int(cand_s)
                trial = [dict(r) for r in vals["rows"]]
                trial[ri]["units"] = cand
                if abs(ticket_piece_total({"rows": trial}) - target_total) < 0.005:
                    suggestions.append({
                        "field": f"rows[{ri}].units", "from": units, "to": cand,
                        "digit_pos": pos,
                        "evidence": f"Σ(units×rate) {ticket_piece_total(vals)} → "
                                    f"{target_total} if {units}→{cand}",
                        "note": "SUGGESTION ONLY — never auto-applied",
                    })
    # minimal edit = closest replacement value first
    suggestions.sort(key=lambda x: abs(x["to"] - x["from"]))
    return suggestions


# ── paystub pass ────────────────────────────────────────────────────────────
_STUB_FIELDS = ("worker_id", "period_start", "period_end", "piece_total",
                "hours_paid", "amount_paid")


def extract_stub(path: str | Path, model: str | None = None, k: int = 3,
                 temperature: float = 0.7,
                 stats: _EnsembleStats | None = None) -> dict:
    """Printed-stub extraction via the same ensemble machinery → {'fields': [...]}."""
    model = model or DEFAULT_MODEL
    path = Path(path)
    results: list[ExtractionResult] = []
    for fp in _STUB_FIELDS:
        def read_once(f=fp) -> Any:
            if model == "mock":
                obj, _ = read_json(path, "", model="mock")
                return obj.get(f)
            obj, _ = read_json(path, "paystub", model=model, temperature=temperature)
            return obj.get(f) if isinstance(obj, dict) else None
        reads, value, unanimous = ensemble_field(fp, read_once, k, None, stats)
        # WS-F finding 2: stub fields get the SAME trust-boundary validation as ticket
        # fields — unanimous garbage ("N/A", comma-money, null) flags for review instead
        # of crashing reconcile/_parse_amount_paid downstream.
        ok, reason = _valid(fp, value)
        flagged = (not unanimous) or (not ok)
        flag_reason = (f"schema:{reason}" if not ok
                       else "self_consistency_disagreement" if not unanimous else None)
        results.append(ExtractionResult(
            field=fp, value=value, reads=reads, unanimous=unanimous,
            flagged=flagged, flag_reason=flag_reason,
            crop_image=f"sha256/{hashlib.sha256(path.read_bytes()).hexdigest()[:16]}.png",
            prompt_version="paystub@1"))
    return {"fields": results}


def _stub_value(stub: dict, name: str) -> Any:
    for f in stub.get("fields", []):
        if f.field == name:
            return f.value
    return None


# ── two-witness reconciliation (#30) ────────────────────────────────────────
def reconcile(tickets: list[TicketExtraction], stub: dict,
              tol: float = 0.02) -> Reconciliation:
    """Ticket piece totals (witness 1) vs the stub's piece line (witness 2). Any
    unresolved mismatch, orphan day, or orphan stub line is a distinct flag; the
    contract's has_unconfirmed_flags() then blocks the receipt (F4)."""
    flags: list[dict] = []
    ticket_total = round(sum(ticket_piece_total(ticket_values(t))
                             for t in tickets if t.fields), 2)
    stub_total = _stub_value(stub, "piece_total")
    if stub_total is None:
        return Reconciliation("not_applicable",
                              [{"type": "no_stub_piece_line",
                                "detail": "stub has no piece_total to cross-check"}])
    # trust boundary: model output may be non-numeric even when unanimous — a garbage
    # piece line is a MISMATCH (routes to review), never a crash (WS-F finding 2).
    if isinstance(stub_total, bool) or not isinstance(stub_total, (int, float)):
        return Reconciliation("mismatch",
                              [{"type": "unreadable_stub_piece_line",
                                "detail": f"stub piece_total is not numeric: {stub_total!r}"}])
    if abs(ticket_total - float(stub_total)) > tol:
        flags.append({
            "type": "amount_mismatch",
            "detail": f"ticket Σ piece = {ticket_total}, stub piece line = {stub_total}",
            "ticket_total": ticket_total, "stub_total": float(stub_total),
        })
    # orphan detection against the stub's declared period
    ps, pe = _stub_value(stub, "period_start"), _stub_value(stub, "period_end")
    if ps and pe:
        for t in tickets:
            if t.date and not (ps <= t.date <= pe):
                flags.append({"type": "orphan_day", "date": t.date,
                              "detail": f"ticket dated {t.date} outside stub period {ps}..{pe}"})
    status = "mismatch" if flags else "ok"
    return Reconciliation(status, flags)


def _missing_days(tickets: list[TicketExtraction], stub: dict) -> list[str]:
    ps, pe = _stub_value(stub, "period_start"), _stub_value(stub, "period_end")
    if not (ps and pe):
        return []
    from datetime import date, timedelta

    y0, m0, d0 = (int(x) for x in ps.split("-"))
    y1, m1, d1 = (int(x) for x in pe.split("-"))
    have = {t.date for t in tickets if t.date}
    out, cur, end = [], date(y0, m0, d0), date(y1, m1, d1)
    while cur <= end:
        iso = cur.isoformat()
        if iso not in have:
            out.append(iso)
        cur += timedelta(days=1)
    return out


# ── whole session ───────────────────────────────────────────────────────────
def extract_session(ticket_paths: list[str | Path], stub_path: str | Path | None,
                    crops_dir: str | Path, session_id: str = "s1",
                    model: str | None = None, k: int = 3, tta: bool = False,
                    whole_ticket: bool = False, adaptive: bool = True,
                    period_start: str = "", period_end: str = "",
                    temperature: float = 0.7,
                    ) -> tuple[SessionExtraction, _EnsembleStats]:
    """Full pay-period extraction: N tickets + 1 stub → SessionExtraction (#26 shape).
    Returns the contract object plus ensemble stats (for the adaptive-savings metric)."""
    model = model or DEFAULT_MODEL
    crops_dir = Path(crops_dir)
    stats = _EnsembleStats()
    groups = dedup_groups(list(ticket_paths)) if len(ticket_paths) > 1 else [None] * len(ticket_paths)
    tickets = [extract_ticket(p, crops_dir, model, k, temperature=temperature,
                              tta=tta, dedup_group=g,
                              whole_ticket=whole_ticket, adaptive=adaptive, stats=stats)
               for p, g in zip(ticket_paths, groups)]
    # dedup refinement: pHash groups visually-similar tickets, but two tickets whose
    # EXTRACTED DATES differ are different days, not duplicate photos — ungroup them.
    # (False-positive dedup + the keep-first audit rule would silently drop a real
    # day's pay; the date is the cheapest independent disambiguator.)
    by_group: dict[str, list[TicketExtraction]] = {}
    for t in tickets:
        if t.dedup_group:
            by_group.setdefault(t.dedup_group, []).append(t)
    for members in by_group.values():
        dates = {t.date for t in members if t.date}
        if len(dates) > 1:
            for t in members:
                t.dedup_group = None
    stub = extract_stub(stub_path, model, k, temperature=temperature,
                        stats=stats) if stub_path else {"fields": []}
    recon = reconcile(tickets, stub) if stub_path else Reconciliation("not_applicable", [])
    missing = _missing_days(tickets, stub) if stub_path else []
    dates = [t.date for t in tickets if t.date]
    se = SessionExtraction(
        session_id=session_id,
        period_start=period_start or (min(dates) if dates else ""),
        period_end=period_end or (max(dates) if dates else ""),
        tickets=tickets, stub=stub, reconciliation=recon, missing_days=missing)
    return se, stats


def flag_rate(se: SessionExtraction) -> float:
    total = flagged = 0
    for t in se.tickets:
        for f in t.fields:
            total += 1
            flagged += int(f.flagged)
    for f in se.stub.get("fields", []):
        total += 1
        flagged += int(f.flagged)
    return round(flagged / total, 3) if total else 0.0
