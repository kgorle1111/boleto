# WS-F Code Review — Integrated Boleto (code-reviewer pass, §10.1 WS-F item 2)

Posture: assume bugs exist. Scope: `core/`, `extraction/`, `evals/`, `app/`, root `wage_engine.py`.
Baseline: 33/33 tests green (`app/test_f4_invariant.py`, `extraction/test_pipeline.py`, `core/wage_engine/test_ws_b.py`) — every finding below is LATENT, not a currently-failing test.

## Findings (most severe first)

### 1. HIGH — `extraction/pipeline.py:440` `extract_stub` does not schema-validate model output → crash on untrusted stub data
`extract_stub` flags a field ONLY on self-consistency disagreement (`flagged = not unanimous`, line 455). It never calls `_valid` (contrast `extract_ticket`, which validates at lines 334 and 365). So **unanimous garbage** from the paystub read passes through unflagged, then crashes downstream on unguarded coercion:
- `reconcile` → `float(stub_total)` (`pipeline.py:486`)
- `_parse_amount_paid` → `usd(_dec(value))` i.e. `Decimal(str(value))` (`session_audit.py:117-124`)

Reproduced (real WS-A shipping path, mock model returning valid-JSON-but-garbage `piece_total:"N/A"`, `amount_paid:null`):
```
stub flagged fields: []
CRASH in reconcile (WS-A shipping path): ValueError could not convert string to float: 'N/A'
```
`Decimal(str(None))`, `Decimal("N/A")`, `Decimal("1,000.00")` all raise `InvalidOperation`; `float("N/A")`/`float(None)` raise `ValueError`/`TypeError` — all verified.

Directly violates Ground Rule 6 ("schema-validate everything the model emits; malformed output routes to human review, never crashes") and the F9 "never a wrong receipt, never an unhandled exception" guarantee. Note: the F9 fault classifier that IS hardened (`evals/pipeline.py:classify_outcome`) is a *parallel eval-only* read path — it does not protect the shipping stub path.

Failure scenario: any real paystub the model reads imperfectly-but-unanimously (comma-formatted money, "N/A", null) → 500 / unhandled exception instead of a review flag.

Smallest fix: run `_valid`-style numeric checks on the stub fields in `extract_stub` (flag `piece_total`/`amount_paid`/`hours_paid` that aren't `>=0` numbers), and defensively guard the `float()`/`_dec()` call sites to route to review rather than raise.

### 2. MED — `app/server.py:166` `confirm_field` + `app/real_contracts.py:145` `_coerce`: human-confirmation value not schema-validated → crash reaching the Decimal engine
The confirm endpoint length-caps the value (`value = str(body["value"])[:40]`, line 175) but never checks it against the field's schema. `_coerce` returns the raw string on conversion failure (`real_contracts.py:150-157`), and `audit_session._parse_ticket` then calls `Decimal(str(value))` → `InvalidOperation`.

Reproduced (real adapter, flagged units field "confirmed" with text):
```
CRASH in build_receipt (real adapter, human-confirm boundary): InvalidOperation [ConversionSyntax]
```
The gate is fail-CLOSED (no wrong receipt is produced), so this is not an F4 bypass — but it is a 500 on untrusted API input, violating "never crashes." The `/api/session/{sid}/confirm` HTTP surface is the trust boundary, not the numeric keypad UI.

Smallest fix: validate the confirmed value against the field's expected type in `confirm_field` (reuse extraction `_valid`); return 422 on non-numeric confirmation of a numeric field.

### 3. LOW — `session_audit.py:117` `_parse_amount_paid` silent `$0` default overstates shortfall
When no `amount_paid|gross_pay|net_pay` field is present, the engine asserts `usd(0)` paid → shortfall becomes the FULL owed amount (an alarming, wrong legal conclusion) with no review flag. Mostly shadowed by Finding 1 (extract_stub always emits `amount_paid`, so the reachable case is a `None` value → crash, per #1). Documented as a `kn:` convention, but for a worker-advocacy tool the safe default is "unknown → lower_bound/flag," not "employer paid nothing." Consider flagging rather than defaulting.

### 4. LOW — float `ticket_piece_total` decides the F4 reconciliation gate
`ticket_piece_total` (`pipeline.py:395`) is float; it feeds the two-witness `reconcile` tolerance check (`pipeline.py:486`, `tol=0.02`) whose "mismatch" verdict is an F4 gate input. The receipt's owed money is correctly recomputed in `Decimal` by the engine, so no float money reaches the receipt — acceptable — but a money quantity gating a safety property is computed in float. Note only; keep tolerance comfortably above float noise.

## F4 invariant — second-bypass hunt: NONE FOUND (fix is complete)
The WS-E cross-ticket field-id collision fix (`real_contracts._prefix_ids`, `d{n}.`/`stub.` namespacing) is consistent across the real adapter, the mock fixtures, and the F4 test (all use the prefixed ids, e.g. `d3.rows[0].units`). Verified:
- `SessionExtraction.has_unconfirmed_flags` is the single frozen gate; the only receipt-producing path (`server.get_receipt`) routes through it via `gate_blocks()` and never re-implements it.
- `confirm_field` only accepts ids in the freshly-computed flagged set; confirmed ids are unique post-prefix (no ticket/ticket or ticket/stub id overlap).
- Reconciliation "mismatch" is fail-closed (no reset path), so the mismatch variant can never emit a receipt.
Finding 2 is a crash at the confirm boundary, not a receipt-past-the-gate bypass.

## Clean
- No secrets in tracked source (`app/core/extraction/evals/wage_engine.py/demo_session`).
- Engine money path (`wage_engine.py`, `session_audit.py`) is Decimal end-to-end — zero `float()` on owed-pay arithmetic; the `Decimal(str(x))` discipline at the extraction→engine boundary is correct (safe on model-supplied floats/ints).
- Path-traversal guard on `/crop` and `/ticket`, argv-based `say` TTS (no shell injection) — sound.
