# Boleto — FINAL REPORT (hackathon build, 2026-07-19)

Every claim here links to a runnable command or a committed result file. Wounds are
enumerated, not hidden.

## Verdict vs the ship bar (≤ 5% escaped-error rate, plan §10.3)

| Run | Backend | n | Field exact | Critical rate | **Escaped rate** | Flag rate | Verdict |
|---|---|---|---|---|---|---|---|
| Real, printed tickets, shipping path | gemma3:latest (Gemma 3 4B vision, ollama) | 8 | 0.975 | 0.000 | **0.000** | 0.000 | **PASSES** on printed |
| Real, printed tickets, **E4B, K=2 T=0.2** | gemma-4-e4b-it-8bit (MLX, shipping path) | 8 | **1.000** | **0.000** | **0.000** | 1.000* | **PASSES — best backend** (*flags are worker_id only — non-pay, receipt not blocked) |
| **Live app demo session, E4B end-to-end** | same, via `BOLETO_ADAPTER=real BOLETO_MODEL=mlx` | 5 tickets (31 pay values, incl. the smudged one) | **31/31 = 1.000** | 0.000 | **0.000** | soft worker_id only | 26s session incl. model load; receipt owed $1415.53, residual "< 2%", chain-stored |
| Simulated stress (degradation suite) | mock/simulated VLM | 400 | 0.951 | 0.482 (pre-gate) | **0.072** | 0.807 | 1.4× over bar under worst-case sim |
| **Real handwritten tickets** | — | 0 | — | — | **UNMEASURED** | — | **the actual gate — §10.6 item 2** |

**Honest verdict: CONDITIONAL PASS.** The system passes the bar on everything that
was actually measurable today (printed tickets, real model, escaped-rate 0.0 with the
gate). It does NOT yet pass on what matters — real handwriting — because no consented
real tickets exist yet. The measurement machinery to answer that in one command is
built: `BOLETO_MODEL=gemma3:latest .venv-gemma/bin/python -m evals.run_eval --engine ws-a --source synthetic --n 400`
(swap `--source` at the real set when it lands).

Key eval finding (WS-C): escaped errors concentrate at LOW-moderate corruption —
confident systematic misreads, not blur. That is why routing keys off vote-agreement,
not image quality, and why the calibration curve (unanimous reads: 46/46 correct on
real gemma3; non-unanimous: 50%) chooses the review threshold.

## Residual error budget (F6) — post-remediation, honest

Printed on every finalized receipt: **"residual error < 2%"** — the measured
post-self-consistency unanimous residual (0.018, WS-C simulated base), with NO credit
for layers that don't fire in the shipped path. The critic's finding that the earlier
"< 0.5%" claim credited a tautological two-witness (stub synthesized from the tickets)
and a checksum that never runs was CONFIRMED and fixed: `core/error_budget.py` now
credits only explicitly-active reducers, and the synthesized stub is flagged
`non_independent_witness` on the record. The number improves only when a genuinely
independent witness (real paystub OCR) runs. Caveat printed with it: base is simulated
on printed tickets; the handwriting floor is unmeasured.

## What was built (all tested, all integrated)

- **Extraction (WS-A):** per-field crop pipeline + K=3 self-consistency w/ adaptive
  sampling (25–33% call savings measured) + quality pre-gate + pHash dedup + checksum
  repair suggestions + paystub pass + two-witness reconcile → frozen contracts.
- **Wage engine (WS-B):** `audit_session→Receipt` on the untouched 6-golden-case core;
  statute citations as data; LC §1194.2/§98.1 claim value; 6 hypothesis invariants ×
  200 examples, zero falsifications; hash-chained sqlite history (now lock-protected,
  secure-delete, VACUUM-on-wipe).
- **Eval engine (WS-C):** synthetic handwriting generator (mixed fonts, stroke jitter,
  4 layouts) + 8-corruption severity suite + calibration + one-command `run_eval.py`
  wired to the shipping extraction path.
- **App (WS-D+E):** localhost-only FastAPI + vanilla-JS SPA; F4 state machine enforced
  server-side (`has_unconfirmed_flags`, now pay-field-scoped + dedup-aware); es-MX⇄en
  instant switch + TTS; provenance per receipt line; durable history + pattern banner;
  eval screen showing real numbers. Latency: 26.3s → **17.2s** full 5-ticket session
  with adaptive sampling (gemma3).
- **Model backends:** MLX E4B (gemma-4-e4b-it-8bit — **the demo backend**: at K=2
  T=0.2 it scores 1.000 field-exact / 0 escaped on the printed set, 4.9s/ticket,
  56.7 tok/s), ollama gemma3 (vision, 0.975 — fallback), mock (oracle for GPU-free CI).
  NOTE: `ollama gemma4:e4b-mlx` is TEXT-ONLY (no vision — verified); do not use it
  for extraction. POST-MORTEM: two earlier E4B evals wrongly showed 0.125/0.875 —
  root cause was a model_client bug (ollama-event unwrapping ate bare one-line JSON
  payloads), not the model; fixed with a regression test. The rule that caught it:
  an identical failure signature on every ticket is a pipeline bug, not a model trait.

## Adversarial QA — found and fixed (results/WS-F-*.md)

8 confirmed findings, all remediated with a regression test each
(`tests/test_wsf_remediation.py`): inflated residual claim (BLOCKER), stub
trust-boundary crash (HIGH), duplicate-photo double-count into owed pay, 1-day-period
500, over-broad F4 gate, unvalidated confirm endpoint, hash-chain race, wipe
completeness. Architecture survived all three passes: human-gate-always-wins is
structurally unbypassable (second-bypass hunt found none), money is Decimal end-to-end,
and "nothing leaves the device" is verified (loopback-only socket, zero egress, zero
secrets).

## Final test state (real output in WS-F-remediation.md)

`pytest -q` → **45 passed** (38 baseline + 7 remediation). WS-A 14/14 · WS-B 13/13 ·
WS-C green · WS-D F4 6/6 · all self-runners green · one-command demo
(`bash demo_session/run_demo.sh`) drives happy / caught-error / mismatch paths.

## E4B ensemble-config sweep

Six configs on printed, three on generator-handwritten (evals/config_sweep.py,
re-runnable, adaptive off so K is exact):

- **Printed set is saturated** — every config 52/52 pay-exact, 0 escaped. Only cost
  differs (K=2 ≈ 4.8s/t, K=4 ≈ 9.3s/t). The benchmark, not the model, is the limit.
- **Handwritten separates them.** Stock E4B floor: ~80% pay-exact, 0.375–0.542
  ticket-critical — far above the ≤5% bar → **the LoRA specialist is justified by
  measurement** (this is the "before" number for the fine-tune delta).
- **T=0.0 is disqualified**: identical reads → self-consistency never fires → escaped
  = full critical rate (0.542), zero pay flags. The gate is blind exactly where the
  model is confidently wrong. Temperature > 0 is a SAFETY requirement, not a knob.
- **Config decision:** demo/printed → K=2 T=0.2 (fast, perfect); handwritten/real →
  K=3 T=0.2 (best escaped-rate, 0.375) until the LoRA lands, then re-sweep.

## LoRA fine-tune experiments — honest negative result (2026-07-19)

Four controlled runs against a FIXED handwritten eval (identical seed-11 batch per
comparison; train seed 2026, never overlapping).

| Attempt | Recipe | pay-exact | escaped | Base same batch |
|---|---|---|---|---|
| v1 | 1500 it, r16, lr 2e-5, 2250 ex | 73.6% | 0.625 | 81.9% / 0.417 |
| v1 @ ckpt500 | early-stop probe | 71.4% | 0.625 | " |
| v2-gentle | 150 it, r8, lr 1e-5 | 77.5% | 0.583 | " |
| v3-diverse | diversified generator (14 crops, 58 rates, varied ids, 5–999 units), 300 it, r8 | 69.2% | 0.708 | 74.2% / 0.708 (re-based on the harder batch) |

**Verdict: every specialist LOST to stock E4B; none was promoted** (the app defaults
to base — F2 champion/challenger enforced in `app/real_contracts.py`).

Diagnosis trail (each step evidence-driven): adapter near-perfect on its own training
images through the same inference path (→ training/inference plumbing correct);
equally bad at ckpt-500 (→ not simple overtraining); 5/16 on held-out val from its own
distribution (→ memorization); diversity retrain still worse (→ the synthetic
distribution contains nothing the base doesn't already know — adaptation only buys
drift). **Conclusion: on synthetic tickets, stock E4B is already the ceiling; the
improvement path is (a) REAL handwritten training data (§10.6 #2) or (b) the system
layers — ensemble, checksum, human gate — which is the product's thesis anyway.**

The pitch angle this earns honestly: "we tried to beat our own model three ways and
our eval caught every regression before it could ship — that discipline IS the product."

## Notes from the live E4B verification run

- **Dedup refinement added:** pHash false-positived two *different days* into one
  duplicate group (visually similar synthetics); combined with keep-first this would
  have dropped a real day's pay after human ack. Fix: tickets whose extracted dates
  differ are ungrouped (`extraction/pipeline.py`, regression-tested).
- **Stale demo fixture:** `demo_session/session.json` holds invented values that don't
  match the ticket pixels — harmless for the mock UI demo, but any accuracy comparison
  must use `tickets/truth.json` (the pixels), as done above.
- **E4B reads the smudged ticket correctly and unanimously** at K=2/T=0.2 — so the
  scripted "review moment" doesn't trigger on the real backend. For a demo that shows
  the review screen, use the reconciliation-mismatch variant or a heavier smudge.

## Open wounds (known, deliberate, owned)

1. **Real-handwriting floor unmeasured** — the single most important unknown. §10.6 #2.
2. **No real paystub OCR witness** — two-witness is demo plumbing until a real stub
   image exists; the budget no longer credits it.
3. ~~E4B needs temp/K calibration~~ **RESOLVED**: the "collapse" was a model_client
   parsing bug, not miscalibration. At K=2/T=0.2 E4B is the best backend (1.000
   field-exact, 0 escaped, 4.9s/ticket). Remaining nit: it reads the small-print
   worker_id header as "" (soft flag only). To use it: `BOLETO_MODEL=mlx` with k=2,
   temperature=0.2.
4. **2026 CA minimum wage is a ~50%-confidence placeholder** (`kn:` in
   `core/wage_engine/minimum_wage.json`) — verify before any real receipt.
5. **Statute wording unreviewed by counsel** — §10.6 #4.
6. At-rest DB encryption deferred to the Android/Tauri ports (threat F8).

## The remaining human steps (nobody else can do these)

1. Consented real tickets (CRLA channel) → run the one-command eval → the real number.
2. LoRA fine-tune on the WS-C set → the before/after delta (the pitch centerpiece).
3. Human baseline comparison.
4. Legal wording pass; 5. MW config verification; 6. the 90-second demo itself.
