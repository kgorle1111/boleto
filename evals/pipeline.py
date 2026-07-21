"""Boleto WS-C read path + fault classifier — the code the eval measures.

`read_ticket` is the self-consistency ensemble the seed's `ensemble_call` grew into:
K reads per ticket with ADAPTIVE sampling (2 reads; a 3rd only when they disagree —
§8b lever 1, ~30% fewer calls at zero accuracy cost), then per-field / per-ROW-CELL
majority vote. Any non-unanimous field is flagged (all raw reads preserved). Rows are
voted cell-by-cell but flagged as the single field "rows" so the scorer's caught/escaped
split (which keys off PAY_FIELDS) stays exactly as the seed defined it.

Two backends:
  • real model tag ("gemma3:latest", ...) → core.model_client.read_json, K times.
  • "mock" → a SIMULATED noisy reader. The core mock returns ground truth perfectly,
    which is right for a plumbing check but produces a degenerate report (flat 100%
    curves). So the eval's mock backend instead SIMULATES a VLM whose per-digit error
    rate rises with corruption severity — this exercises the whole analysis pipeline
    (robustness curve, calibration, caught/escaped, dollar-weight) without a GPU.
    kn: SIMULATED error model — NOT a Gemma measurement. Only BOLETO_MODEL=gemma3
    numbers are real. Tunable knobs below are the calibration point (§ hardware rule):
    a real VLM's curve replaces them the moment the gemma run lands.

`classify_outcome` is the fault-injection contract: any input (corrupt image, garbage
or empty model output, valid read) must land in ONE named state — retake / review /
refused / ok — never a wrong receipt, never an unhandled exception.
"""
from __future__ import annotations

import json
import random
import time
from collections import Counter
from pathlib import Path

from core.contracts import PAY_FIELDS
from core import model_client

FIELDS = ("worker_id", "date", "rows", "productive_hours",
          "nonproductive_hours", "rest_hours")

# whole-ticket extraction prompt (versioned file, not an inline string — ground rule 5)
_PROMPT = (Path(__file__).parent / "prompts" / "ticket_whole.txt").read_text().split("---", 1)[1].strip()

# ── simulated-VLM error model (mock backend only) ─────────────────────────────
# TWO error components — the distinction is the whole point of the escaped metric:
#   • NOISY  (independent per read): a digit is misread differently each read →
#     the reads disagree → the ensemble FLAGS it → caught. Rate rises with severity.
#   • SYSTEMATIC (fixed per ticket, shared across ALL reads): a hard smudge makes
#     every read see the SAME wrong digit → unanimous-WRONG → no disagreement to
#     flag → it ESCAPES to a receipt. This is the failure mode the gate cannot catch
#     and the ship-blocker the dollar-weighted-escaped metric exists to surface.
# kn: independent+systematic split is the calibration knob; the real gemma3 curve
#     replaces these four constants. Real VLMs have MORE correlation than this.
_NOISE_BASE, _NOISE_SLOPE = 0.04, 0.34   # P(independent misread)
_SYS_BASE, _SYS_SLOPE = 0.010, 0.10      # P(systematic misread — escapes the ensemble)


def _perturb_digits(value, rng: random.Random) -> object:
    """Return a plausibly-misread version of a numeric field (one digit off / swap)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        s = list(str(abs(value)))
        i = rng.randrange(len(s))
        s[i] = str((int(s[i]) + rng.choice([-1, 1])) % 10)
        return int("".join(s)) * (1 if value >= 0 else -1)
    if isinstance(value, float):
        cents = int(round(value * 100))
        return _perturb_digits(cents, rng) / 100.0
    return value


def _sim_bias(truth: dict, severity: float, rng: random.Random) -> dict:
    """Per-TICKET systematic misreads, shared across every read (→ unanimous-wrong →
    escapes). Computed once per image, seeded by the image only."""
    p_sys = min(0.6, _SYS_BASE + _SYS_SLOPE * severity)
    bias: dict = {"rows": [], "hours": {}}
    for row in truth.get("rows", []):
        rb = {}
        if rng.random() < p_sys:
            rb["units"] = _perturb_digits(row["units"], rng)
        if rng.random() < p_sys * 0.6:
            rb["rate"] = _perturb_digits(row["rate"], rng)
        bias["rows"].append(rb)
    for f in ("productive_hours", "nonproductive_hours", "rest_hours"):
        if rng.random() < p_sys * 0.7:
            bias["hours"][f] = _perturb_digits(truth[f], rng)
    return bias


def _sim_read(truth: dict, severity: float, bias: dict, rng: random.Random) -> dict:
    """One simulated VLM read: apply the ticket's systematic bias (fixed), then add
    independent noise (varies per read)."""
    p_noise = min(0.95, _NOISE_BASE + _NOISE_SLOPE * severity)
    out = json.loads(json.dumps(truth))
    for i, row in enumerate(out.get("rows", [])):
        rb = bias["rows"][i] if i < len(bias["rows"]) else {}
        row["units"] = rb.get("units", row["units"])
        row["rate"] = rb.get("rate", row["rate"])
        if rng.random() < p_noise:  # independent noise on top → disagreement → flagged
            row["units"] = _perturb_digits(row["units"], rng)
        if rng.random() < p_noise * 0.6:
            row["rate"] = _perturb_digits(row["rate"], rng)
    for f in ("productive_hours", "nonproductive_hours", "rest_hours"):
        out[f] = bias["hours"].get(f, out[f])
        if rng.random() < p_noise * 0.7:
            out[f] = _perturb_digits(out[f], rng)
    return out


def _vote_rows(samples: list[dict]) -> tuple[list, bool]:
    """Per-CELL majority vote over row data. Returns (voted_rows, unanimous)."""
    n_rows = Counter(len(s.get("rows", [])) for s in samples).most_common(1)[0][0]
    voted, unanimous = [], True
    for i in range(n_rows):
        cell = {}
        for key in ("crop", "units", "rate"):
            votes = Counter(
                json.dumps(s["rows"][i].get(key), sort_keys=True)
                for s in samples if i < len(s.get("rows", []))
            )
            if not votes:
                unanimous = False
                cell[key] = None
                continue
            top, c = votes.most_common(1)[0]
            cell[key] = json.loads(top)
            if c < len(samples):
                unanimous = False
        voted.append(cell)
    return voted, unanimous


def read_ticket(image_path: Path, truth: dict | None, model: str,
                k: int = 3, adaptive: bool = True, severity: float = 0.0
                ) -> tuple[dict, list[str], dict]:
    """Ensemble read → (prediction, flagged_pay_fields, meta).

    meta carries: reads (call count), seconds, and per-field vote agreement (top/k)
    for the calibration table. `truth`/`severity` are only used by the simulated mock
    backend (severity drives its noisy+systematic error rates).
    """
    t0 = time.perf_counter()
    samples: list[dict] = []
    reads = 0
    # systematic bias fixed per ticket (seeded by image only → shared across reads)
    bias = _sim_bias(truth or {}, severity,
                     random.Random(hash(str(image_path)) & 0xFFFFFFFF)) if model == "mock" else {}

    def one_read() -> dict:
        nonlocal reads
        reads += 1
        if model == "mock":
            rng = random.Random(hash((str(image_path), reads)) & 0xFFFFFFFF)
            return _sim_read(truth or {}, severity, bias, rng)
        obj, _ = model_client.read_json(image_path, _PROMPT, model=model)
        return obj

    # adaptive: 2 reads first, add a 3rd only if any field disagrees
    n_first = 2 if adaptive else k
    samples = [one_read() for _ in range(min(n_first, k))]
    if adaptive and k > 2:
        disagree = any(
            json.dumps(samples[0].get(f), sort_keys=True) !=
            json.dumps(samples[1].get(f), sort_keys=True) for f in FIELDS
        )
        if disagree:
            while reads < k:
                samples.append(one_read())

    # per-field vote + agreement
    pred: dict = {}
    flagged: list[str] = []
    agreements: list[tuple[float, bool]] = []  # (agreement, correct) vs truth if known
    for f in FIELDS:
        if f == "rows":
            voted, unanimous = _vote_rows(samples)
            pred["rows"] = voted
            agr = 1.0 if unanimous else 0.5
            if not unanimous and "rows" in PAY_FIELDS:
                flagged.append("rows")
        else:
            votes = Counter(json.dumps(s.get(f), sort_keys=True) for s in samples)
            top, c = votes.most_common(1)[0]
            pred[f] = json.loads(top)
            agr = c / len(samples)
            if c < len(samples) and f in PAY_FIELDS:
                flagged.append(f)
        if truth is not None:
            correct = json.dumps(pred[f], sort_keys=True) == json.dumps(truth.get(f), sort_keys=True)
            agreements.append((round(agr, 3), correct))

    meta = {"reads": reads, "seconds": time.perf_counter() - t0,
            "agreements": agreements, "samples": samples}
    return pred, flagged, meta


# ── fault injection contract (F9) ─────────────────────────────────────────────
_REQUIRED = set(PAY_FIELDS)


def classify_outcome(model_output, quality_ok: bool = True,
                     session_complete: bool = True) -> str:
    """Map any read result to ONE named state. NEVER returns a receipt on bad input.

      retake   → image failed the quality pre-gate (blur/exposure) before inference
      refused  → structural refusal (e.g. incomplete pay-period session)
      review   → model output empty / not a dict / missing required pay fields /
                 present-but-flagged  (routes to a human, never a receipt)
      ok       → valid, schema-complete, unanimous → may proceed to the engine
    """
    if not quality_ok:
        return "retake"
    if not session_complete:
        return "refused"
    if not isinstance(model_output, dict) or not model_output:
        return "review"
    if not _REQUIRED.issubset(model_output.keys()):
        return "review"
    if not isinstance(model_output.get("rows"), list):
        return "review"
    return "ok"


def demo() -> None:
    """Self-check: adaptive read on mock + every fault lands in a NAMED safe state."""
    truth = {"worker_id": "W1", "date": "2026-07-01",
             "rows": [{"crop": "s", "units": 100, "rate": 2.0}],
             "productive_hours": 8, "nonproductive_hours": 0, "rest_hours": 0.5}
    pred, flags, meta = read_ticket(Path("t_sim.png"), truth, "mock", k=3, severity=0.8)
    assert set(pred.keys()) == set(FIELDS), pred
    assert meta["reads"] in (2, 3), meta["reads"]  # adaptive: 2 or 3

    # fault matrix — none may yield "ok"
    assert classify_outcome({}, quality_ok=True) == "review"
    assert classify_outcome("garbage truncated {", quality_ok=True) == "review"
    assert classify_outcome({"rows": []}, quality_ok=True) == "review"  # missing pay fields
    assert classify_outcome(truth, quality_ok=False) == "retake"
    assert classify_outcome(truth, session_complete=False) == "refused"
    assert classify_outcome(truth) == "ok"
    for bad in ({}, None, "", "not json", [1, 2, 3], {"rows": "nope"}):
        assert classify_outcome(bad) != "ok", bad
    print(f"pipeline.py: adaptive reads={meta['reads']} ✓  fault states all safe ✓")


if __name__ == "__main__":
    demo()
