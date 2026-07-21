"""WS-C runnable checks. Aggregates each module's self-check + the cross-module
fault contract, so `pytest evals/test_evals.py` is the one gate that can fail.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import model_client
from evals import generator, metrics, pipeline, human_baseline


def test_module_self_checks():
    # each module's demo() is an assert-based self-check; a regression raises here
    generator.demo()
    metrics.demo()
    pipeline.demo()
    human_baseline.demo()


def test_model_client_mock_plumbing():
    # proves the REAL read path is wired: core mock reads ground truth deterministically
    img = Path(__file__).resolve().parent.parent / "tickets" / "ticket_00.png"
    if img.exists():
        obj, meta = model_client.read_json(img, "extract", model="mock")
        assert obj.get("worker_id") == "W100"
        assert meta["model"] == "mock"


def test_fault_injection_never_produces_receipt():
    """F9: corrupt image bytes, truncated/garbage/empty output → a NAMED safe state,
    never 'ok'. This is the ship-critical invariant."""
    bad_inputs = [
        {}, None, "", "not json", "{truncated", [1, 2, 3],
        {"rows": []}, {"rows": "nope"}, {"worker_id": "W1"},  # missing pay fields
    ]
    for bad in bad_inputs:
        assert pipeline.classify_outcome(bad, quality_ok=True) != "ok", bad
    # bad image path must not crash the reader (trust boundary), routes to review
    out = pipeline.classify_outcome(model_client._extract_json("garbage {"), quality_ok=True)
    assert out == "review"


def test_robustness_curve_rises_with_severity():
    """Simulated backend: the PRE-GATE conclusion-critical rate (raw model error before
    the ensemble gate) must rise from low to high corruption severity — this is the
    robustness curve. (Post-gate escaped_rate is deliberately NOT asserted monotonic:
    at high severity heavy per-read noise flags almost everything, so escaped errors
    concentrate at low-moderate severity — a real finding, reported in RESULTS.)"""
    manifest = generator.gen_dataset(400, seed=42)
    truth = [m["truth"] for m in manifest]
    imgs = [generator.OUT / m["image"] for m in manifest]
    cor = [m["corruption"] for m in manifest]
    lo_bad = lo_n = hi_bad = hi_n = 0
    for img, t, c in zip(imgs, truth, cor):
        p, _, _ = pipeline.read_ticket(Path(img), t, "mock", k=3, severity=c["severity"])
        bad = int(metrics._pay_numbers(p) != metrics._pay_numbers(t))
        if c["severity"] <= 0.25:
            lo_bad += bad; lo_n += 1
        elif c["severity"] >= 0.75:
            hi_bad += bad; hi_n += 1
    lo, hi = lo_bad / lo_n, hi_bad / hi_n
    assert hi > lo, f"robustness curve not rising: low-sev crit={lo:.3f} high-sev crit={hi:.3f}"


def test_eval_report_contract_shape():
    from evals.run_eval import run_benchmark, to_eval_report
    res = run_benchmark("mock", "synthetic", n=48)
    rep = to_eval_report("mock", res)
    d = rep.to_dict()
    for key in ("date", "model", "n", "field_exact_match", "conclusion_critical_rate",
                "escaped_rate", "dollar_weighted_escaped", "flag_rate",
                "per_severity", "calibration", "latency"):
        assert key in d, key
    assert isinstance(d["dollar_weighted_escaped"], str)  # money is str per contract


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} ✓")
