"""Boleto WS-C metrics — evolves htr_harness.py's conclusion-critical scorer.

KEPT verbatim from the seed (do not weaken): per-field exact match,
conclusion-critical error rate, caught/escaped gate split, flag rate. The scorer's
core claim is unchanged — a critical error on a FLAGGED pay field is *caught* (routed
to review, never reaches a receipt); on an UNFLAGGED field it *escapes*, and the
escaped rate is the ship-blocker.

ADDED for WS-C:
  • dollar-weighted escaped error — each escaped error weighted by the receipt-dollar
    impact of that misread (a $0.25 rate slip ≠ a 100-unit slip). Pluggable weight fn
    defaulting to a documented heuristic; WS-B supplies the real per-line sensitivity
    at integration.
  • per-corruption-type × severity breakdown — the robustness curve.
  • empirical calibration — vote-agreement vs. accuracy → reliability table; the
    review threshold is CHOSEN from it and recorded.
"""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Callable

from core.contracts import PAY_FIELDS

# Heuristic minimum wage for the DEFAULT dollar-weight. WS-B computes the real
# per-line dollar sensitivity from the actual receipt; this stand-in only needs to
# rank misreads sensibly so the metric is non-degenerate before integration.
# kn: MW hardcoded 16.00; WS-B's dollar_sensitivity replaces this whole fn at E-wire.
_MW = Decimal("16.00")


def _pay_numbers(rec: dict) -> tuple:
    """The numbers that change owed pay (seed semantics, unchanged)."""
    rows = tuple((r.get("units"), r.get("rate")) for r in rec.get("rows", []))
    return (rows, rec.get("productive_hours"), rec.get("nonproductive_hours"),
            rec.get("rest_hours"))


def _bad_pay_fields(p: dict, t: dict) -> list[str]:
    return [f for f in PAY_FIELDS
            if json.dumps(p.get(f), sort_keys=True) != json.dumps(t.get(f), sort_keys=True)]


def default_dollar_weight(field: str, pred: dict, truth: dict) -> Decimal:
    """Documented default: dollars a misread of `field` moves on the receipt.

      rows              → Σ |units_p·rate_p − units_t·rate_t|   (exact piece-earnings delta)
      productive_hours  → |Δh|·MW   (min-wage make-up floor moves ~ this)
      nonproductive_hours → |Δh|·MW (LC 226.2 pays nonproductive time at >= MW)
      rest_hours        → |Δh|·MW   (rest_rate >= MW; a conservative LOWER bound —
                                     the real weekly-average rate WS-B uses is >=)
    """
    if field == "rows":
        pr, tr = pred.get("rows", []), truth.get("rows", [])
        total = Decimal(0)
        for i in range(max(len(pr), len(tr))):
            rp = pr[i] if i < len(pr) else {"units": 0, "rate": 0}
            rt = tr[i] if i < len(tr) else {"units": 0, "rate": 0}
            ep = Decimal(str(rp.get("units", 0))) * Decimal(str(rp.get("rate", 0)))
            et = Decimal(str(rt.get("units", 0))) * Decimal(str(rt.get("rate", 0)))
            total += abs(ep - et)
        return total
    dh = abs(Decimal(str(pred.get(field, 0))) - Decimal(str(truth.get(field, 0))))
    return dh * _MW


def score(preds: list[dict], truth: list[dict],
          flags: list[list[str]] | None = None,
          corruption: list[dict] | None = None,
          weight_fn: Callable[[str, dict, dict], Decimal] = default_dollar_weight) -> dict:
    """Full scorecard. flags[i] = pay fields the ensemble flagged for review on ticket i.
    corruption[i] = {"type","severity"} tag for the per-corruption breakdown."""
    assert len(preds) == len(truth), "pred/truth length mismatch"
    n = len(truth)
    flags = flags or [[] for _ in truth]
    corruption = corruption or [{"type": "clean", "severity": 0.0} for _ in truth]

    field_hits = field_total = 0
    critical_bad = caught = escaped = 0
    dollar_escaped = Decimal(0)
    # per (type, severity) accumulators for the robustness curve
    per = defaultdict(lambda: {"n": 0, "critical": 0, "escaped": 0,
                               "field_hits": 0, "field_total": 0,
                               "dollar_escaped": Decimal(0)})

    for p, t, fl, cor in zip(preds, truth, flags, corruption):
        key = (cor["type"], cor["severity"])
        pe = per[key]
        pe["n"] += 1
        for k in ("worker_id", "date", "productive_hours", "nonproductive_hours", "rest_hours"):
            field_total += 1
            hit = int(str(p.get(k)) == str(t.get(k)))
            field_hits += hit
            pe["field_total"] += 1
            pe["field_hits"] += hit
        if _pay_numbers(p) != _pay_numbers(t):
            critical_bad += 1
            pe["critical"] += 1
            bad = _bad_pay_fields(p, t)
            if all(f in fl for f in bad):
                caught += 1
            else:
                escaped += 1
                pe["escaped"] += 1
                # weight ONLY the escaped fields not flagged (the ones that reach a receipt)
                d = sum((weight_fn(f, p, t) for f in bad if f not in fl), Decimal(0))
                dollar_escaped += d
                pe["dollar_escaped"] += d

    per_out = {}
    for (typ, sev), v in sorted(per.items()):
        per_out.setdefault(typ, {})[str(sev)] = {
            "n": v["n"],
            "field_exact_match": round(v["field_hits"] / v["field_total"], 3) if v["field_total"] else 0.0,
            "conclusion_critical_rate": round(v["critical"] / v["n"], 3) if v["n"] else 0.0,
            "escaped_rate": round(v["escaped"] / v["n"], 3) if v["n"] else 0.0,
            "dollar_escaped": str(v["dollar_escaped"]),
        }

    return {
        "tickets": n,
        "field_exact_match": round(field_hits / field_total, 3) if field_total else 0.0,
        "conclusion_critical_rate": round(critical_bad / n, 3) if n else 0.0,
        "critical_caught_by_gate": caught,
        "critical_escaped_gate": escaped,
        "escaped_rate": round(escaped / n, 3) if n else 0.0,
        "dollar_weighted_escaped": str(dollar_escaped),
        "flag_rate": round(sum(1 for fl in flags if fl) / n, 3) if n else 0.0,
        "per_corruption": per_out,
        "verdict_hint": "SHIP-CANDIDATE if escaped_rate<=0.05; NEEDS-WORK otherwise",
    }


def calibration_table(agreements: list[tuple[float, bool]]) -> dict:
    """Reliability table: bucket (vote_agreement, was_correct) samples → empirical
    accuracy per agreement level, and RECORD the review threshold chosen from it.

    agreements = per field-read-group: (top_votes/k, field_read_was_correct).
    The routing rule Boleto ships: route any NON-unanimous field (agreement < 1.0) to
    human review. This table is the evidence for that threshold — it reports the
    measured precision at unanimity vs below, so the choice is data-driven, not by feel.
    """
    buckets: dict[float, list[bool]] = defaultdict(list)
    for agr, correct in agreements:
        buckets[round(agr, 3)].append(correct)
    table = []
    for agr in sorted(buckets):
        b = buckets[agr]
        table.append({"vote_agreement": agr, "n": len(b),
                      "empirical_accuracy": round(sum(b) / len(b), 3)})
    unan = [c for a, c in agreements if a >= 0.999]
    nonunan = [c for a, c in agreements if a < 0.999]
    return {
        "table": table,
        "unanimous_precision": round(sum(unan) / len(unan), 3) if unan else None,
        "nonunanimous_precision": round(sum(nonunan) / len(nonunan), 3) if nonunan else None,
        "chosen_threshold": "route to review when vote_agreement < 1.0 (non-unanimous)",
        "threshold_justification": (
            f"unanimous reads measured {round(sum(unan)/len(unan),3) if unan else 'n/a'} precision; "
            f"non-unanimous {round(sum(nonunan)/len(nonunan),3) if nonunan else 'n/a'} — "
            "so non-unanimous fields route to a human."),
    }


def demo() -> None:
    """Self-check: seed caught/escaped semantics + dollar weighting direction."""
    truth = [
        {"worker_id": "W1", "date": "2026-07-01",
         "rows": [{"crop": "s", "units": 100, "rate": 2.0}],
         "productive_hours": 8, "nonproductive_hours": 0, "rest_hours": 0.5},
        {"worker_id": "W2", "date": "2026-07-02",
         "rows": [{"crop": "s", "units": 50, "rate": 2.5}],
         "productive_hours": 8, "nonproductive_hours": 0, "rest_hours": 0.5},
    ]
    preds = json.loads(json.dumps(truth))
    preds[0]["rows"][0]["units"] = 101   # flagged → caught
    preds[1]["rows"][0]["units"] = 60    # unflagged → escaped, $ impact = 10 units * $2.5 = 25
    flags = [["rows"], []]
    cor = [{"type": "smudge", "severity": 0.5}, {"type": "clean", "severity": 0.0}]
    r = score(preds, truth, flags, cor)
    assert r["critical_caught_by_gate"] == 1 and r["critical_escaped_gate"] == 1, r
    assert r["dollar_weighted_escaped"] == "25.0", r["dollar_weighted_escaped"]
    assert r["per_corruption"]["clean"]["0.0"]["escaped_rate"] == 1.0, r["per_corruption"]

    # dollar weight: rate misread on a 100-unit row moves 100*|Δrate|
    w = default_dollar_weight("rows",
                              {"rows": [{"units": 100, "rate": 2.25}]},
                              {"rows": [{"units": 100, "rate": 2.00}]})
    assert w == Decimal("25.00"), w

    cal = calibration_table([(1.0, True), (1.0, True), (1.0, False),
                             (0.667, False), (0.667, True)])
    assert cal["unanimous_precision"] == round(2 / 3, 3), cal
    print("metrics.py: caught/escaped ✓  dollar-weight ✓  calibration ✓")


if __name__ == "__main__":
    demo()
