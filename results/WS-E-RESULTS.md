# WS-E — Integration & end-to-end — RESULTS

Status: **DONE, all green.** The four isolated workstreams are now one system. Every
number below is pasted real command output. Model backend for all real numbers here is
**`gemma3:latest` via ollama** (the local E4B/MLX weights were still downloading — see the
backend note); the orchestrator will run the definitive E4B/MLX pass. No E4B number is
claimed that wasn't measured.

**Backend selection order** (used everywhere a real model runs): `available('mlx')` →
`BOLETO_MODEL=mlx`; else `available('gemma3:latest')` → `gemma3:latest`; else `mock`.
At build time: `mlx=False` (download resuming), `gemma3=True` → all real numbers are gemma3.

---

## What I wired (the four seams)

| Seam | Before | After |
|---|---|---|
| **App ← extraction+audit** | WS-D read `demo_session/*.json` fixtures via `app/mock_contracts.py` | `app/real_contracts.py` runs `extraction.extract_session` → `wage_engine.audit_session` for real. Server picks adapter by env `BOLETO_ADAPTER` (default **mock**, so WS-D's tests stay GPU-free). |
| **Eval ← shipping code** | `evals/run_eval.py` measured `evals/pipeline.py` (a parallel ensemble) | added `--engine ws-a` driving `extraction.extract_ticket` — the eval now measures the code that ships. WS-C's metrics/generator/calibration untouched. |
| **App history ← durable store** | in-memory `HISTORY` list (lost on restart) | wired WS-B's `core/wage_engine/history.py` (sqlite, SHA-256 hash-chained, tamper-evident) into the server. `/api/history` serves `pattern_summary()` + `chain_verified`. |
| **Receipt residual (F6)** | hard-coded `"< 0.5%"` string in the fixture | `core/error_budget.py` composes the five layer catch-rates → residual, filled into `Receipt.residual_error` by both adapters and printed on the receipt. |

New files: `app/real_contracts.py`, `core/error_budget.py`.
Edited across workstreams (integration phase): `app/server.py`, `app/mock_contracts.py`,
`app/static/strings.json` ("Checked 4 ways"→"5"), `extraction/pipeline.py` (whole-ticket
fallback + `adaptive` param), `core/wage_engine/session_audit.py` (field-id prefix strip),
`core/wage_engine/history.py` (`check_same_thread`), `evals/run_eval.py` (`--engine ws-a`),
`demo_session/build.py` (mismatch fixture), `demo_session/run_demo.sh`.

---

## 1. One-command demo boot — both paths + the mismatch path (real output)

```
$ bash demo_session/run_demo.sh
== building demo fixtures (incl. the reconciliation-mismatch variant) ==
demo_session built:
  tickets: 5 (day_03 smudged, day3.units flagged)
  crops:   3
  gate:    has_unconfirmed_flags() = True
  self-check: gate opens only after d3.rows[0].units is confirmed  OK
== booting server (127.0.0.1 only, mock adapter) ==

== 1. create pay-period session ==
{"session_id":"fbdf661d5983","state":"CAPTURED","variant":"ok","period_start":"2026-07-06","period_end":"2026-07-12","n_tickets":5}
== 2. stream per-ticket extraction (SSE — receipt builds line-by-line) ==
"index": 0	"has_flag": false
"index": 1	"has_flag": false
"index": 2	"has_flag": true
"index": 3	"has_flag": false
"index": 4	"has_flag": false

== 3. CAUGHT-ERROR PATH: ask for receipt while the smudged field is unconfirmed ==
{"error":"gate_blocked","reason":"unconfirmed flagged field(s) — human confirmation required","unconfirmed":["d3.rows[0].units"],"reconciliation":"ok"}  -> HTTP 409

== 4. human confirms the flagged field (293 vs 298 -> worker picks 298) ==
{"confirmed":"d3.rows[0].units","value":"298","gate_blocks":false,"state":"REVIEWED"}
  -> HTTP 200

== 5. HAPPY PATH: receipt now prints (gate open), residual error from WS-B budget ==
  total_owed 320.00 paid 300.00 shortfall 20.00 | residual_error < 0.5% | chain_hash 613f013e223c | state RECEIPT

== 6. spoken receipt (macOS say -> WAV) ==
  tts -> HTTP 200 102256 bytes

== 7. history + pattern banner (durable, hash-chained sqlite; tamper-evident) ==
  pattern: {'shorted': 1, 'total': 1, 'amount': '20.00'} | chain_verified: True | shorted 1 of last 1 periods, $20.00 cumulative

== 8. RECONCILIATION-MISMATCH PATH: two-witness fails -> gate blocks, no field to confirm ==
{"error":"gate_blocked","reason":"unconfirmed flagged field(s) — human confirmation required","unconfirmed":[],"reconciliation":"mismatch"}  -> HTTP 409
  (an unresolved ticket-vs-stub mismatch NEVER prints a receipt — the safety property)
```

- **Happy path**: gate opens only after the human-confirmation event; receipt prints with
  the WS-B-derived `residual_error` and a populated `chain_hash` (durable, tamper-evident).
- **Caught-error path**: `GET /receipt` → **409** while `d3.rows[0].units` is unconfirmed.
- **NEW reconciliation-MISMATCH path** (the flow WS-D flagged as missing): a two-witness
  fail (ticket Σ piece ≠ stub piece line) → **409 with `reconciliation:"mismatch"` and an
  empty `unconfirmed` list** — there is no field to confirm, so the receipt is blocked
  until the mismatch itself is resolved. An unresolved two-witness mismatch never prints.

Screenshots of every SPA screen live in `results/WS-D-RESULTS.md` §Screenshots (7 screens,
375×812; the SPA is JS-rendered so they're live captures). The backend flow above
reproduces deterministically for re-verification.

---

## 2. Real-model run — the app served by REAL WS-A + WS-B (gemma3, through the server)

```
$ BOLETO_ADAPTER=real BOLETO_MODEL=gemma3:latest .venv-gemma/bin/python -c "<TestClient drive>"
session created: 26538f4e2c03
receipt before confirm -> HTTP 409 | unconfirmed: ['d3.worker_id', 'd3.rows[0].crop'] | recon: ok
receipt after confirm -> HTTP 200
  REAL numbers: total_owed 1415.53 paid 1000.00 shortfall 415.53 | residual < 0.5% | chain d979bfa7f7cd
  lines: [('piece_earnings','1271.50'),('nonproductive_pay','50.70'),('rest_pay','31.11'),('ot_premium','62.22')]
```

The full **photo → gemma3 extraction → deterministic WS-B audit → gated receipt** path runs
on real model output. Two things worth noting:
- **The smudge produced a REAL self-consistency disagreement.** gemma3 read `day_03`'s
  `worker_id` and `rows[0].crop` differently across the K reads → both flagged → the F4 gate
  blocked the receipt (409) until confirmed. The pay-bearing digits read unanimously, so the
  *deterministic* engine still computed on clean numbers — exactly the designed behaviour.
- **Real numbers, not the mock story.** The receipt shows real computed pay ($1415.53 owed).
  `amount_paid=$1000` is a **provided demo constant** (`BOLETO_DEMO_PAID`), not OCR — the
  demo set has no genuine paystub scan (`demo_session/stub.png` is another ticket image). The
  model work that is real is the 5-ticket extraction; the paystub OCR seam is marked
  `ponytail:` in `real_contracts.py`. Reproduce:
  `BOLETO_ADAPTER=real BOLETO_MODEL=gemma3:latest .venv-gemma/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8010`

Whole-ticket read is the real backend (WS-A's per-field-crop wound: gemma3 returns `{}` on
26px crops; whole-ticket scores 0.975). `extract_ticket(..., whole_ticket=True)` is the
graceful mode added for this — **the per-field-crop design is preserved** (default path,
documented for real handwritten tickets with proper cell layout), the whole-ticket read is
opt-in and clearly commented.

---

## 3. True time-to-receipt + the adaptive-sampling lever (task 3, both numbers)

Full 5-ticket session, real gemma3, whole-ticket extraction + WS-B audit, same process A/B:

```
NO lever (fixed K=3)  :  26.3s  model_calls=15  total_owed=$1415.53
WITH adaptive lever   :  17.2s  model_calls=10  total_owed=$1415.53
```

- **Without the §8b lever (fixed K=3): 26.3 s** (15 model calls).
- **With adaptive sampling (2 reads, a 3rd only on disagreement): 17.2 s** (10 calls) —
  **≈35% faster, 33% fewer model calls, IDENTICAL `total_owed`** (zero accuracy cost, since
  the dropped reads were on already-unanimous tickets).
- Per-ticket that's ~3.4 s adaptive — comfortably inside the §6 budget (< 20 s ticket→receipt
  on mid-range). Extraction dominates; the WS-B audit + error budget are sub-millisecond.
- The shipping eval (`--engine ws-a`, §4) independently measured **4.9 s/ticket, 25% reads
  saved** on the 8 printed tickets.

`kn:` tokens/s is still `null` on the ollama CLI path (frozen `model_client` doesn't request
`eval_count`); the orchestrator's `BOLETO_MODEL=mlx` backend returns real tps.

---

## 4. Eval wired to the SHIPPING extraction path (task 2)

`run_eval.py --engine ws-a` drives `extraction.extract_ticket` (whole-ticket on real) and
adapts its `TicketExtraction` to WS-C's scorer contract. WS-C's metrics/generator/calibration
are untouched — the eval now measures the code the app runs, not a parallel reimplementation.

```
$ BOLETO_MODEL=gemma3:latest .venv-gemma/bin/python -m evals.run_eval --source real --engine ws-a
  "model": "gemma3:latest",  "n": 8,
  "field_exact_match": 0.975,
  "conclusion_critical_rate": 0.0,
  "escaped_rate": 0.0,
  "dollar_weighted_escaped": "0",
  "flag_rate": 0.0,
  "s_per_ticket": 4.918,  "adaptive_reads": 18 (vs 24), "reads_saved_pct": 25.0
appended → evals/results/eval_2026-07-19_gemma3-latest_real_ws-a.json
```

Shipping path on the 8 printed tickets: **field-exact 0.975, zero conclusion-critical, zero
escaped** — matches WS-C's parallel-engine number (0.975), confirming the two paths agree on
the printed set. Honest limit (unchanged from WS-C): printed synthetic validates the
harness+schema+scorer, **not the handwriting floor** — that needs Kannishk's real set
(§10.6 #2) fed to the same command. One command for the full synthetic robustness curve:
`BOLETO_MODEL=gemma3:latest .venv-gemma/bin/python -m evals.run_eval --source synthetic --n 400 --engine ws-a`

---

## 5. F6 end-to-end error budget (task 4) — the arithmetic, with measured inputs

`core/error_budget.py`. A misread digit must defeat **five** checks to become a wrong
receipt. Each layer's catch-rate is LABELLED measured (WS-C) vs estimated.

### Layer catch rates
| # | layer | catch rate | source |
|---|---|---|---|
| 1 | capture gate (F3) | 0.30 | estimated — blur/glare/exposure pre-gate rejects the worst images |
| 2 | self-consistency (E2) | **0.85** | **measured** — WS-C caught 164 of 193 conclusion-critical errors (sim n=400) |
| 3 | cross-field checksum (E1) | 0.50 | estimated — units×rate vs row subtotal (fires when a subtotal exists) |
| 4 | two-witness (#30) | 0.60 | estimated — ticket Σ piece vs stub piece line |
| 5 | human review | 0.98 | estimated — human confirms every surfaced flagged field vs its crop |

### Composition (per conclusion-critical error introduced)
`residual = base_rate × [ Π(1−catch_i) + (1−Π(1−catch_i))·(1−human) ]`
(an error reaches the receipt iff it escapes every automated flag, OR is flagged but the
human misses it. Unanimous-and-consistent escapes are never surfaced — so human review
cannot reduce them, and that honesty is the whole point of stating the number.)

**Finalized-receipt residual (what prints on a passed receipt) → `< 0.5%`:**
base = 1 − unanimous_precision = 1 − 0.982 = **0.018** (measured, WS-C sim; the only error
class that can silently reach a receipt is a *unanimous-wrong* field). Checksum + two-witness
reduce it further:
```
escape_all = (1−0.5)(1−0.6) = 0.20
p_reach    = 0.20 + (1−0.20)·(1−0.98) = 0.216
residual   = 0.018 × 0.216 = 0.00389  →  < 0.5%
```

**Pessimistic full-stack composition (the pitch's "defeat five checks" number) → `< 2%`:**
base = 0.482 (WS-C simulated pre-gate conclusion_critical_rate, high-severity mix):
```
escape_all = (1−0.30)(1−0.85)(1−0.50)(1−0.60) = 0.021
p_reach    = 0.021 + (1−0.021)·(1−0.98) = 0.04058
residual   = 0.482 × 0.04058 = 0.01956  →  < 2%
```

**Reality check on the base rate:** the real-printed run measured **0.0** conclusion-critical
(n=8) → the real residual on printed tickets is ~0. The simulated bases above are deliberate
UPPER bounds; the real handwriting floor is still unknown (§10.6 #2). The receipt prints the
conservative finalized number **"Checked 5 ways; residual error < 0.5%"** (strings.json,
es+en), now DERIVED, not hard-coded.

---

## 6. All prior workstream tests still pass (re-run after my changes)

```
=== WS-A (14) ===  14 passed, 0 failed, 14 total
=== WS-B (13) ===  13/13 passed — all green
=== WS-C evals ==  test_robustness_curve_rises_with_severity ✓  (all module self-checks ✓)
=== WS-D F4 (6) =  6/6 passed
=== new: error_budget ==  finalized residual < 0.5% | pessimistic < 2%
contracts.py: all round-trip self-checks passed
model_client.py: _extract_json trust-boundary checks passed
session_audit.py: worked-receipt self-check passed
history.py: hash-chain + pattern_summary self-checks passed
config.py: minimum_wage_for self-checks passed

$ .venv-gemma/bin/python -m pytest -q extraction/test_pipeline.py app/test_f4_invariant.py core/wage_engine/test_ws_b.py
33 passed, 1 warning in 2.56s
```

- **The F4 gate stays intact and unbypassable** on both mock AND real output (real run §2
  shows gemma3's smudge flags blocking the receipt).
- WS-C's original `--engine sim` default path still produces non-degenerate numbers
  (verified: field-exact 0.99, escaped 0.05 on mock synthetic n=20).

---

## Integration frictions found & fixed

1. **Field-id collision across tickets (F4 safety).** WS-A emits *bare* per-ticket ids
   (`rows[0].units`); WS-B parses them bare per ticket; but the frozen
   `has_unconfirmed_flags(confirmed: set[str])` keys on a *flat* set — two tickets with the
   same flagged field name would let one confirmation clear both (a gate bypass). Fix: the
   adapter namespaces ids session-unique (`d{n}.` per ticket, `stub.` for the stub — WS-D's
   existing convention), and WS-B's parser strips that prefix (`_strip_prefix`), so bare ids
   (WS-B's own tests) and prefixed ids (the app) both parse. Root-caused, not patched per-path.
2. **gemma3 returns `{}` on 26px per-field crops** (WS-A's documented wound). Fix: added
   `whole_ticket=True` graceful mode to `extract_ticket` — reads the whole image K times and
   slices fields out, still saving crops for the review screen's provenance. Per-field design
   preserved as the default.
3. **sqlite across FastAPI's threadpool** → `ProgrammingError` (connection thread affinity).
   Fix: `ReceiptHistory(check_same_thread=False)`; `ponytail:` no write lock (localhost demo,
   ~no concurrency).
4. **Receipt didn't reflect corrections.** The mock returned a static receipt ignoring the
   human's fix. The real adapter's `build_receipt` overlays confirmed values onto the session
   *before* auditing, so the arithmetic uses the corrected number (the human decides).

---

## Remaining wounds for WS-F

- **Paystub OCR is not exercised on real data.** The demo has no genuine paystub image, so
  `amount_paid` is a provided constant (`ponytail:` in `real_contracts.py`). Real stub OCR
  needs a real paystub + `core/extraction/prompts/paystub.txt`. Critic should confirm the
  no-stub path (`amount_paid=$0`, full shortfall) and the two-witness path both behave.
- **Error-budget inputs are mostly estimates.** Only self-consistency (0.85) is measured, and
  from the *simulated* WS-C backend. The base rate is a simulated upper bound; real
  handwriting is unmeasured. The number is a labelled framework, not a validated 0.5%.
- **F4 gate blocks on ANY flagged field, incl. non-pay** (real run flagged `worker_id`/`crop`).
  Conservative and safe, but means the human confirms non-pay fields too. Design choice in the
  frozen contract; flag for the critic to accept or route only pay-bearing flags to a hard block.
- **Escaped-error class is un-surfaced by construction** — the honest SRE caveat: a unanimous
  wrong-and-arithmetically-consistent digit reaches the receipt uncaught. That IS the residual.
  WS-F's "valid-JSON-wrong-numbers unanimously" attack tests exactly this; the two-witness gate
  is the last automated catch (demonstrated in §1 step 8).

## BLOCKED
- **None.** `mlx` backend was still downloading at build time, so real numbers are `gemma3:latest`
  (labelled). The orchestrator runs the definitive E4B/MLX eval + demo; nothing here blocks it —
  every path is backend-agnostic (`BOLETO_MODEL` selects; `whole_ticket` triggers on any non-mock).
