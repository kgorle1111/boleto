"""Boleto HTR benchmark harness — measures Gemma 4 E4B field-extraction accuracy
on piece-rate punch tickets. This is smoke test #1's scaffolding.

WHY THIS METRIC: generic OCR/CER is the wrong measure. One misread digit in a
unit count or rate flips the legal conclusion. So we score PER-FIELD EXACT MATCH
on the numbers that feed the wage engine, and separately report the
"conclusion-critical error rate" — the fraction of tickets where any pay-bearing
number is wrong. That is the number that decides whether Boleto is safe to ship.

Run modes:
  python3 htr_harness.py gen 8         # generate 8 synthetic tickets + ground truth
  python3 htr_harness.py score          # score a mock prediction (proves the scorer)
  python3 htr_harness.py bench gemma     # run real model over the tickets (needs a
                                         # vision model in ollama; wire model_call below)

HONEST LIMIT: synthetic tickets use a printed font, so they validate the harness,
the extraction SCHEMA, and the scorer — NOT the handwriting floor. The real test
#1 requires ~20-30 photos of ACTUAL handwritten tickets (get them from a worker
center / CRLA under consent). Swap those into tickets/ and rerun `bench`.
  # ponytail: printed synthetic = plumbing only; real handwriting is the gate.
"""
from __future__ import annotations
import json, sys, subprocess, base64
from pathlib import Path

DIR = Path(__file__).parent
TICKETS = DIR / "tickets"
TICKETS.mkdir(exist_ok=True)

# ---- extraction schema: exactly the fields the wage engine consumes ----
# A ticket = one worker, one day, N rows of piece work + hour fields.
SCHEMA = {
    "worker_id": "str",
    "date": "str (YYYY-MM-DD)",
    "rows": "list of {crop: str, units: int, rate: float}",
    "productive_hours": "float",
    "nonproductive_hours": "float",
    "rest_hours": "float",
}
PROMPT = (
    "This is a farm piece-rate punch ticket. Extract STRICT JSON with keys: "
    "worker_id, date (YYYY-MM-DD), rows (list of {crop, units, rate}), "
    "productive_hours, nonproductive_hours, rest_hours. Numbers only for numeric "
    "fields. If a field is absent use 0. Output JSON only, no prose."
)

# ---- synthetic ticket generator (ground-truth known) ----
def gen(n: int):
    from PIL import Image, ImageDraw, ImageFont
    import random
    random.seed(42)  # deterministic: no Math.random equivalent needed, reproducible set
    crops = ["strawberry", "raspberry", "blackberry"]
    truth = []
    for i in range(n):
        rows = [{"crop": random.choice(crops),
                 "units": random.randint(20, 180),
                 "rate": round(random.choice([1.75, 2.00, 2.25, 2.50]), 2)}
                for _ in range(random.randint(1, 3))]
        rec = {
            "worker_id": f"W{100+i}",
            "date": f"2026-07-{10+i:02d}",
            "rows": rows,
            "productive_hours": round(random.choice([6, 7, 7.5, 8, 9, 10]), 2),
            "nonproductive_hours": round(random.choice([0, 0, 0.5, 1]), 2),
            "rest_hours": round(random.choice([0, 0.33, 0.5]), 2),
        }
        truth.append(rec)
        img = Image.new("RGB", (520, 380), "white")
        d = ImageDraw.Draw(img)
        f = ImageFont.load_default()
        y = 12
        d.text((12, y), f"RANCHO PUNCH TICKET   {rec['worker_id']}   {rec['date']}", fill="black", font=f); y += 26
        for r in rec["rows"]:
            d.text((20, y), f"{r['crop']:12s} units: {r['units']:4d}   rate: ${r['rate']:.2f}", fill="black", font=f); y += 24
        y += 8
        d.text((20, y), f"productive hrs: {rec['productive_hours']}", fill="black", font=f); y += 22
        d.text((20, y), f"nonproductive hrs: {rec['nonproductive_hours']}", fill="black", font=f); y += 22
        d.text((20, y), f"rest hrs: {rec['rest_hours']}", fill="black", font=f)
        img.save(TICKETS / f"ticket_{i:02d}.png")
    (TICKETS / "truth.json").write_text(json.dumps(truth, indent=2))
    print(f"generated {n} tickets + truth.json in {TICKETS}")

# ---- scorer: per-field exact match + conclusion-critical error rate ----
def _pay_numbers(rec: dict) -> tuple:
    """The numbers that change owed pay. Any mismatch here is conclusion-critical."""
    rows = tuple((r.get("units"), r.get("rate")) for r in rec.get("rows", []))
    return (rows, rec.get("productive_hours"), rec.get("nonproductive_hours"), rec.get("rest_hours"))

PAY_FIELDS = ("rows", "productive_hours", "nonproductive_hours", "rest_hours")

def score(preds: list[dict], truth: list[dict], flags: list[list[str]] | None = None) -> dict:
    """flags[i] = fields the ensemble flagged for human review on ticket i.
    A critical error on a flagged pay field is CAUGHT (goes to review, never
    reaches the receipt); on unflagged fields it ESCAPES — that's the ship-blocker."""
    assert len(preds) == len(truth), "pred/truth length mismatch"
    flags = flags or [[] for _ in truth]
    field_hits = field_total = 0
    critical_bad = caught = escaped = 0
    for p, t, fl in zip(preds, truth, flags):
        for k in ("worker_id", "date", "productive_hours", "nonproductive_hours", "rest_hours"):
            field_total += 1
            field_hits += int(str(p.get(k)) == str(t.get(k)))
        if _pay_numbers(p) != _pay_numbers(t):
            critical_bad += 1
            bad_fields = [f for f in PAY_FIELDS
                          if json.dumps(p.get(f), sort_keys=True) != json.dumps(t.get(f), sort_keys=True)]
            if all(f in fl for f in bad_fields):
                caught += 1
            else:
                escaped += 1
    n = len(truth)
    return {
        "tickets": n,
        "field_exact_match": round(field_hits / field_total, 3),
        "conclusion_critical_error_rate": round(critical_bad / n, 3),
        "critical_caught_by_gate": caught,
        "critical_escaped_gate": escaped,
        "escaped_error_rate": round(escaped / n, 3),
        "review_flag_rate": round(sum(1 for fl in flags if fl) / n, 3),
        "verdict_hint": ("SHIP-CANDIDATE if escaped_error_rate<=0.05; "
                         "NEEDS-WORK otherwise"),
    }

# ---- pluggable model call (wire to E4B here) ----
def model_call(image_path: Path, model: str) -> dict:
    """Return parsed JSON prediction for one ticket. Team wires E4B via ollama/MLX.
    Expects an ollama vision model tag (e.g. 'gemma3' once pulled)."""
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    payload = {"model": model, "prompt": PROMPT, "images": [b64], "stream": False}
    out = subprocess.run(["ollama", "generate", "--json"], input=json.dumps(payload),
                         capture_output=True, text=True)  # adapt to your ollama API
    txt = out.stdout.strip()
    start = txt.find("{"); end = txt.rfind("}")
    return json.loads(txt[start:end+1]) if start >= 0 else {}

# ---- self-consistency ensemble: K reads, per-field majority vote ----
# VLMs emit no usable confidence, so we manufacture one by sampling: read the
# ticket K times (temperature > 0), vote per field. Unanimous field -> trusted;
# any disagreement -> flagged for human review. The gate's job is to catch the
# confident-wrong-digit failure mode before it becomes a legal conclusion.
FIELDS = ("worker_id", "date", "rows", "productive_hours", "nonproductive_hours", "rest_hours")

def ensemble_call(image_path: Path, model: str, k: int = 3) -> tuple[dict, list[str]]:
    """K independent reads -> (majority-vote prediction, fields flagged for review)."""
    from collections import Counter
    samples = []
    for _ in range(k):
        try:
            samples.append(model_call(image_path, model))
        except Exception as e:
            print(f"{image_path.name}: sample error {e}")
    if not samples:
        return {}, list(FIELDS)
    pred, flagged = {}, []
    for f in FIELDS:
        # ponytail: rows voted as one field; per-row/per-cell voting if flag rate is too high
        votes = Counter(json.dumps(s.get(f), sort_keys=True) for s in samples)
        top, n = votes.most_common(1)[0]
        pred[f] = json.loads(top)
        if n < len(samples):  # any disagreement = no unanimity = don't trust it
            flagged.append(f)
    return pred, flagged

def bench(model: str, k: int = 3):
    truth = json.loads((TICKETS / "truth.json").read_text())
    preds, flags = [], []
    for i in range(len(truth)):
        p, fl = ensemble_call(TICKETS / f"ticket_{i:02d}.png", model, k)
        preds.append(p); flags.append(fl)
    print(json.dumps(score(preds, truth, flags), indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gen"
    if cmd == "gen":
        gen(int(sys.argv[2]) if len(sys.argv) > 2 else 8)
    elif cmd == "score":
        # prove scorer + gate: two injected digit errors, one flagged (caught),
        # one unflagged (escaped) — the report must separate them.
        truth = json.loads((TICKETS / "truth.json").read_text())
        preds = json.loads(json.dumps(truth))  # deep copy
        preds[0]["rows"][0]["units"] += 1       # error the ensemble flagged
        preds[1]["rows"][0]["units"] += 1       # error that slipped past unanimously
        flags = [[] for _ in truth]
        flags[0] = ["rows"]
        rep = score(preds, truth, flags)
        assert rep["critical_caught_by_gate"] == 1 and rep["critical_escaped_gate"] == 1
        print(json.dumps(rep, indent=2))
    elif cmd == "bench":
        bench(sys.argv[2] if len(sys.argv) > 2 else "gemma3")
