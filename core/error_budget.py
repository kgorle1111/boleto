"""F6 — end-to-end error budget (WS-E item 4).

Composes the independent catch rates of Boleto's verification layers into a single
stated residual probability that a *wrong receipt* reaches the worker. The number is
printed ON the receipt ("checked 5 ways; residual error < X%") and written into
Receipt.residual_error. No hackathon team states a composed failure probability — this
is the SRE move.

The five layers (a misread digit must defeat all of them to become a wrong receipt):
  1. capture gate (F3)        — classical-CV blur/glare pre-gate rejects the image
  2. self-consistency (E2)    — K reads must agree; any disagreement → human review
  3. cross-field checksum (E1)— units×rate must satisfy the row subtotal
  4. two-witness (#30)        — ticket Σ piece must reconcile with the stub piece line
  5. human review            — every flagged field is confirmed against its crop image

Inputs are LABELLED measured vs estimated. The one measured catch rate comes from
WS-C's benchmark (evals/results); the rest are conservative estimates until Kannishk's
real handwritten set lands (§10.6 item 2). The arithmetic is returned in full so the
claim is auditable, never a bare number.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Layer:
    name: str
    catch_rate: float   # P(a conclusion-critical error is flagged by THIS layer alone)
    source: str         # "measured — ..." | "estimated — ..."


# Automated layers that gate a receipt. catch_rate = P(this layer flags a given
# conclusion-critical error), treated as independent (conservative: real layers
# correlate somewhat, which only helps — a correlated miss is rarer than the product).
AUTOMATED_LAYERS: list[Layer] = [
    Layer("capture_gate", 0.30,
          "estimated — F3 blur/glare/exposure pre-gate rejects the worst images before inference"),
    Layer("self_consistency", 0.85,
          "measured — WS-C caught 164 of 193 conclusion-critical errors (evals/results, simulated n=400)"),
    Layer("cross_field_checksum", 0.50,
          "estimated — units×rate vs row subtotal; only fires when the ticket carries a subtotal line"),
    Layer("two_witness", 0.60,
          "estimated — ticket Σ piece total vs the stub's printed piece line"),
]
HUMAN_REVIEW = Layer("human_review", 0.98,
                     "estimated — human confirms every surfaced flagged field against its crop image")

# Base rate: P(a pay-bearing conclusion carries >=1 model misread) BEFORE any layer.
# PESSIMISTIC preset: WS-C simulated pre-gate conclusion_critical_rate on a high-severity
# corruption mix. FINALIZED preset: 1 - unanimous_precision — the residual error of a field
# that already PASSED self-consistency (the only class that can silently reach a receipt).
# kn: both are simulated (WS-C sim n=400). The real-printed run measured 0.0 critical (n=8);
#     the real handwriting floor is still unknown (§10.6 item 2) — these are upper bounds.
BASE_PESSIMISTIC = 0.482          # per-conclusion, pre-gate, worst-case mix
BASE_FINALIZED = 1.0 - 0.982      # = 0.018, post-self-consistency unanimous residual


def compose(base_rate: float, layers: list[Layer], human: Layer) -> dict:
    """Return the residual wrong-receipt probability and the full arithmetic.

    An error reaches the receipt iff it escapes every automated flag AND (if flagged)
    the human misses it. Escaped-unanimous errors are never surfaced, so human review
    cannot reduce them — that honesty is the whole point of stating the number.
    """
    escape_all = 1.0
    steps = []
    for L in layers:
        escape_all *= (1.0 - L.catch_rate)
        steps.append({"layer": L.name, "catch_rate": L.catch_rate,
                      "escapes_this_layer": round(1.0 - L.catch_rate, 4), "source": L.source})
    p_flagged = 1.0 - escape_all
    # flagged errors get one more gate (the human); unflagged ones never reach a human
    p_reach = escape_all + p_flagged * (1.0 - human.catch_rate)
    residual = base_rate * p_reach
    return {
        "base_rate": base_rate,
        "layers": steps,
        "human_review": {"catch_rate": human.catch_rate, "source": human.source},
        "escapes_all_automated": round(escape_all, 5),
        "p_flagged": round(p_flagged, 5),
        "p_reaches_receipt_per_error": round(p_reach, 5),
        "residual": residual,
        "n_ways_checked": len(layers) + 1,
        "arithmetic": (
            "residual = base_rate × [ Π(1−catch_i) + (1−Π(1−catch_i))·(1−human) ] = "
            + " × ".join(f"(1−{L.catch_rate})" for L in layers)
            + f" folded → escape_all={round(escape_all,5)}; "
            + f"p_reach={round(p_reach,5)}; residual={base_rate}×{round(p_reach,5)}={round(residual,5)}"
        ),
    }


def _bucket(residual: float) -> str:
    for thr in (0.001, 0.005, 0.01, 0.02, 0.05, 0.10):
        if residual <= thr:
            return f"< {thr * 100:g}%"
    return f"~ {residual * 100:.0f}%"


def finalized_receipt_residual(active_reducers: tuple[str, ...] = ()) -> tuple[str, dict]:
    """The number that prints on a receipt whose flags are all resolved. Base is the
    post-self-consistency unanimous residual (the only error class that can slip through);
    capture-gate + self-consistency are already reflected in that base.

    HONESTY RULE (WS-F critic finding 1): only layers that ACTUALLY FIRE in the shipped
    path may reduce the base. As shipped today, the cross-field checksum is not invoked in
    extract_session, and the demo's two-witness stub is synthesized FROM the tickets
    (non-independent — it cannot catch a unanimous misread), so the default is NO reducers:
    residual = the measured unanimous-residual base itself. Pass active_reducers (e.g.
    ("two_witness",)) only when a genuinely independent witness runs — a real OCR'd paystub.
    kn: base is WS-C simulated (n=400) on PRINTED tickets; the handwriting floor is
    unmeasured until §10.6 item 2 lands. This number is an upper-bound claim, not a boast."""
    reducers = [L for L in AUTOMATED_LAYERS if L.name in active_reducers]
    d = compose(BASE_FINALIZED, reducers, HUMAN_REVIEW)
    d["preset"] = "finalized_receipt"
    # ways that actually gate: capture + self-consistency (both folded into the base),
    # each active reducer, + human review of surfaced flags
    d["n_ways_checked"] = 3 + len(reducers)
    d["caveat"] = ("base rate is simulated on printed tickets; unanimous misreads are not "
                   "reduced by human review (they are never surfaced); real-handwriting "
                   "floor unmeasured")
    return _bucket(d["residual"]), d


def pessimistic_composition() -> tuple[str, dict]:
    """Pedagogical full-stack composition (pre-gate base through all 5 layers) — the
    'a misread digit must defeat five checks' arithmetic for the pitch / RESULTS."""
    d = compose(BASE_PESSIMISTIC, AUTOMATED_LAYERS, HUMAN_REVIEW)
    d["preset"] = "pessimistic_full_stack"
    return _bucket(d["residual"]), d


def demo() -> None:
    s_fin, d_fin = finalized_receipt_residual()
    s_pess, d_pess = pessimistic_composition()
    # HONEST default: no reducers fire in the shipped path → residual == base (0.018) → "< 2%"
    assert d_fin["residual"] == BASE_FINALIZED, d_fin
    assert s_fin == "< 2%", s_fin
    # with a real independent witness the number improves — but only when one runs
    s_wit, d_wit = finalized_receipt_residual(("two_witness",))
    assert d_wit["residual"] < d_fin["residual"], (d_wit["residual"], d_fin["residual"])
    # pessimistic must be strictly larger and still a stated inequality
    assert d_pess["residual"] > d_fin["residual"], (d_pess["residual"], d_fin["residual"])
    assert s_pess.startswith("<") or s_pess.startswith("~"), s_pess
    # every input is labelled measured or estimated
    for L in AUTOMATED_LAYERS + [HUMAN_REVIEW]:
        assert L.source.startswith(("measured", "estimated")), L
    print("error_budget.py: finalized residual", s_fin, "| pessimistic", s_pess)
    print("  finalized arithmetic:", d_fin["arithmetic"])
    print("  pessimistic arithmetic:", d_pess["arithmetic"])


if __name__ == "__main__":
    demo()
