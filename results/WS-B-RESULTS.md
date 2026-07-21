# WS-B — Wage engine, legal layer & history — RESULTS

Status: **DONE, all green.** Every number below is pasted real command output.

Environment note (honest): `pytest` and `ruff` are NOT installed in `.venv-gemma`
(hypothesis IS). pytest is not a project dependency (`pyproject.toml` ground-rule-3
pre-approves hypothesis, not pytest), and I was told not to touch `pyproject.toml`.
So the test module is **self-running via a stdlib runner** and stays pytest-collectable
for CI once pytest lands. Commands to reproduce use the venv python directly.

## Files created / changed

| File | What |
|---|---|
| `core/wage_engine/session_audit.py` | `audit_session(SessionExtraction) -> Receipt` — the SOLE entry point. Single-ticket refusal; incomplete-week lower bounds; statute citations (data table); claim-value calc; per-line dollar-sensitivity (#43). Plus `build_session()` shared builder. |
| `core/wage_engine/history.py` | stdlib `sqlite3` receipt history, SHA-256 hash-chained (E5), `pattern_summary()` with per-period citations, `verify_chain()`. |
| `core/wage_engine/config.py` | Date-selected minimum-wage config loader (state + local-ordinance placeholder). |
| `core/wage_engine/minimum_wage.json` | Versioned, effective-dated CA MW values. **`kn:` VERIFY** flag (§10.6 item 5). |
| `core/wage_engine/golden_cases.json` | Shared KAT vectors: 6 portable `reconstruct_cases` + 2 `session_cases` (complete + incomplete). |
| `core/wage_engine/wage_engine.py` | Was a byte-identical copy of root; **replaced with a re-export shim** so the root file stays the single canonical arithmetic core (portable to Kotlin/Rust ports). |
| `core/wage_engine/test_ws_b.py` | Golden + 6 property/metamorphic invariants + history tests + stdlib runner. |
| `core/wage_engine/__init__.py` | Package exports. |

Root `wage_engine.py` was **NOT modified** — its 6 golden cases stay green untouched.
The legal layer (Python dataclasses `SessionExtraction`/`Receipt`) deliberately lives
in `core/`, not in the root reference engine, so the thing being ported stays pure
arithmetic.

---

## 1. Original 6 golden cases — GREEN (root engine untouched)

```
$ .venv-gemma/bin/python wage_engine.py
all 6 known-answer cases passed
sample receipt (case 2): {'piece_earnings': Decimal('300.00'), 'mw_makeup': Decimal('0.00'), 'nonproductive_pay': Decimal('0.00'), 'rest_pay': Decimal('20.00'), 'rest_rate': Decimal('40.00'), 'regular_rate': Decimal('40.00'), 'ot_hours': Decimal('0'), 'ot_premium': Decimal('0.00'), 'total_owed': Decimal('320.00'), 'amount_paid': Decimal('300.00'), 'shortfall': Decimal('20.00')}
```

The same 6 cases are also encoded as portable data in `golden_cases.json`
(`reconstruct_cases`) and re-checked by `test_golden_reconstruct_cases` below —
this is the shared vector file every future port (Kotlin/Rust) must pass.

---

## 2. Full WS-B test suite — 13/13 GREEN

```
$ .venv-gemma/bin/python -m core.wage_engine.test_ws_b
PASS  test_claim_value_labeled_potential
PASS  test_dollar_sensitivity_present_and_shaped
PASS  test_golden_reconstruct_cases
PASS  test_golden_session_cases
PASS  test_history_hash_chain_and_pattern
PASS  test_incomplete_week_labels_lines_as_lower_bounds
PASS  test_inv_never_negative
PASS  test_inv_owed_ge_piece
PASS  test_inv_rest_pay_ge_mw_times_rest_hours
PASS  test_inv_scaling_units_scales_piece_exactly
PASS  test_inv_shortfall_monotonic_as_paid_decreases
PASS  test_inv_zero_hour_day_changes_nothing
PASS  test_single_ticket_refused

13/13 passed — all green
```

Includes the **new session golden case** (complete 5-day week) and the
**incomplete-week case** (missing 2026-07-09 → `lower_bound=True`, every line's
arithmetic prefixed `[LOWER BOUND — …]`), plus single-ticket refusal, claim-value
labeling, and dollar-sensitivity shape checks.

---

## 3. Property + metamorphic invariants — ≥200 examples each, ZERO falsifications

The suite runs each invariant at `@settings(max_examples=200)`. Independent
counter-instrumented run proving the example count (scratchpad `count_examples.py`):

```
$ .venv-gemma/bin/python count_examples.py
invariant : examples executed (zero falsifications)
  owed_ge_piece         : 200
  never_negative        : 200
  rest_ge_mw            : 200
  shortfall_monotonic   : 200
  scaling               : 200
  zero_day              : 200
ALL invariants >=200 examples, zero falsifications
```

Invariants (all held by the existing engine — no engine changes needed, no wrong
invariants found):
1. `owed ≥ piece earnings`
2. no component ever negative
3. `rest_pay ≥ min_wage × rest_hours`
4. shortfall monotonic non-decreasing as paid decreases
5. scaling all units by k scales piece earnings by exactly k (metamorphic)
6. adding a zero-hour day changes nothing (metamorphic)

---

## 4. minimum-wage config — GREEN

```
$ .venv-gemma/bin/python -m core.wage_engine.config
config.py: minimum_wage_for self-checks passed
```

---

## 5. Hash-chained history + pattern detection — GREEN (tamper-evident)

```
$ .venv-gemma/bin/python -m core.wage_engine.history
history.py: hash-chain + pattern_summary self-checks passed
  pattern: shorted 2 of last 3 periods, $35.00 cumulative
```

`verify_chain()` returns True on a fresh chain and **False after an in-place tamper**
of any stored receipt (each receipt's SHA-256 embeds the previous receipt's hash — E5).
`pattern_summary()` returns the cross-period story with per-period statute citations.

---

## 6. One fully-worked Receipt JSON (incomplete week, stored → chain_hash populated)

Employer paid $800 for an observed 4-day partial week; MW $16.90 (2026 config).
Note the `[LOWER BOUND …]` prefix on every line, `lower_bound: true`, `missing_days`,
per-line `dollar_sensitivity`, the citation data table, the `claim_value` labeled
"potential … an advocate confirms", the es+en CRLA disclaimer, and the populated
`chain_hash`.

```json
{
  "session_id": "wk-incomplete",
  "lines": [
    {
      "item": "piece_earnings",
      "amount": "1210.00",
      "citation": "Labor Code §200; IWC Wage Order 14 §4",
      "arithmetic": "[LOWER BOUND — missing days ['2026-07-09']; totals sum only observed tickets] Σ(units × rate) across all rows = $1210.00",
      "sources": ["day0.rows[0].units","day0.rows[0].rate","day1.rows[0].units","day1.rows[0].rate","day2.rows[0].units","day2.rows[0].rate","day3.rows[0].units","day3.rows[0].rate"],
      "dollar_sensitivity": {
        "day0.rows[0].units": "2.00/unit", "day0.rows[0].rate": "150.00/$1rate",
        "day1.rows[0].units": "2.00/unit", "day1.rows[0].rate": "160.00/$1rate",
        "day2.rows[0].units": "2.00/unit", "day2.rows[0].rate": "140.00/$1rate",
        "day3.rows[0].units": "2.00/unit", "day3.rows[0].rate": "155.00/$1rate"
      }
    },
    {
      "item": "rest_pay",
      "amount": "80.67",
      "citation": "Labor Code §226.2(a)(3)(A)",
      "arithmetic": "[LOWER BOUND — missing days ['2026-07-09']; totals sum only observed tickets] 2.0h rest × $40.33 (weekly avg rate vs MW $16.90, higher wins) = $80.67",
      "sources": ["day0.rows[0].units","day0.rows[0].rate","day0.productive_hours","day0.rest_hours","day1.rows[0].units","day1.rows[0].rate","day1.productive_hours","day1.rest_hours","day2.rows[0].units","day2.rows[0].rate","day2.productive_hours","day2.rest_hours","day3.rows[0].units","day3.rows[0].rate","day3.productive_hours","day3.rest_hours"],
      "dollar_sensitivity": {
        "day0.rows[0].units": "0.13/unit", "day0.rows[0].rate": "10.00/$1rate", "day0.productive_hours": "2.61/h", "day0.rest_hours": "40.33/h",
        "day1.rows[0].units": "0.13/unit", "day1.rows[0].rate": "10.66/$1rate", "day1.productive_hours": "2.61/h", "day1.rest_hours": "40.33/h",
        "day2.rows[0].units": "0.13/unit", "day2.rows[0].rate": "9.33/$1rate", "day2.productive_hours": "2.61/h", "day2.rest_hours": "40.33/h",
        "day3.rows[0].units": "0.13/unit", "day3.rows[0].rate": "10.33/$1rate", "day3.productive_hours": "2.61/h", "day3.rest_hours": "40.33/h"
      }
    }
  ],
  "total_owed": "1290.67",
  "amount_paid": "800.00",
  "shortfall": "490.67",
  "claim_value": {
    "liquidated_damages": "490.67",
    "interest": "0.94",
    "citation": "LC §1194.2, §98.1",
    "interest_basis": "10%/yr simple on $490.67 for 7 days (period end→as_of)",
    "note": "potential claim value — an advocate confirms what applies"
  },
  "lower_bound": true,
  "missing_days": ["2026-07-09"],
  "disclaimer": "Estimación para discutir con un defensor — no es asesoría legal. Comuníquese con CRLA (California Rural Legal Assistance). / Estimate for discussion with an advocate — not legal advice. Contact CRLA (California Rural Legal Assistance).",
  "chain_hash": "270880bbdea7e1d06be1d4b6de9f8bb604d7ce89d4c6d5a44c9fa138bdce41ee",
  "residual_error": null
}
```

(A complete-week worked receipt with OT and MW-makeup lines is printed by
`.venv-gemma/bin/python -m core.wage_engine.session_audit`.)

---

## Design decisions worth flagging to the orchestrator / WS-E / WS-F

- **`audit_session` runs `reconstruct()` exactly ONCE** for the canonical numbers.
  The per-line dollar-sensitivity uses extra *throwaway* reconstructs on perturbed
  input copies (finite differences) — a derivative, never the answer. It is exact
  for a 1-unit misread (the #43 semantic), including nonlinear effects: e.g. a units
  misread correctly shows up on `rest_pay` because it moves the §226.2 weekly-average
  rate.
- **`lower_bound` is per-receipt (frozen contract has no per-line bool).** Per-line
  labeling is done by prefixing each line's `arithmetic` string — the only place in
  the frozen `ReceiptLine` shape to carry it.
- **F4 flag-gating is NOT enforced in `audit_session`.** It computes; the "no receipt
  without human confirmation on every flagged field" invariant is WS-D's structural
  server-side gate (the contract already exposes `SessionExtraction.has_unconfirmed_flags`).
  Kept the compute layer pure so WS-D owns the safety gate.
- **`amount_paid` convention:** a stub field named `amount_paid | gross_pay | net_pay`
  (first found); absent → `$0` (fully unpaid). `kn:` — confirm the real stub field name
  with WS-A once the paystub schema lands.

## BLOCKED / wounds

- None blocking. Two `kn:` items that are **Kannishk-only** by design (§10.6), left as
  loud flags, not bugs:
  1. **`minimum_wage.json` values need verification** — especially the **2026 CA figure
     `$16.90`, which is a best-estimate placeholder** (CPI-indexed; my independent
     recollection, confidence ~50%). §10.6 item 5.
  2. **Statute citations & claim-value base need the legal-wording pass** (§10.6 item 4).
     Specifically: LC §1194.2 liquidated damages strictly double only the *sub-minimum-wage*
     slice of the shortfall; the calculator conservatively uses the **whole shortfall** as
     the base and labels it "potential — advocate confirms". Marked with a `kn:` in
     `session_audit._claim_value`. This is intentionally generous and flagged, not silent.
- **Single-ticket refusal ceiling:** a *legitimately* 1-day pay period is currently
  refused along with the mathematically-wrong single-ticket case. `kn:`-flagged in
  `audit_session`. Consistent behavior for WS-F's critic: 3 tickets → audits as an
  incomplete (lower-bound) week; 1 ticket → refused.
```
