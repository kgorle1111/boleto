# WS-A — Extraction pipeline — RESULTS

**Model policy:** the full 8-ticket end-to-end run + all unit tests ran on `BOLETO_MODEL=mock`
(the whole-ticket oracle — no GPU). One real smoke run over 1 ticket used `gemma3:latest` via
ollama to prove the real path and capture latency. **The real path RUNS and is not too slow
(~2.7s per whole-ticket vision call; 15.0s/ticket at K=2 per-field), but per-field-crop read
ACCURACY on the printed synthetic set is poor — see the WOUND section. This is a
data/format-calibration wound, not a pipeline-logic bug; all pipeline logic is validated on mock.**

Files created (all absolute):
- `/Users/kannishknaidu/geeeeeemm/extraction/pipeline.py` — the pipeline (all 9 WS-A items)
- `/Users/kannishknaidu/geeeeeemm/extraction/__main__.py` — thin CLI (`python -m extraction <cmd>`)
- `/Users/kannishknaidu/geeeeeemm/extraction/test_pipeline.py` — 14 assert-based tests
- `/Users/kannishknaidu/geeeeeemm/extraction/__init__.py` — public API exports

Frozen files imported, never edited: `core/contracts.py`, `core/model_client.py`,
`core/extraction/schema.json`, `core/extraction/formats/format_0.json`,
`core/extraction/prompts/{ticket_field,paystub}.txt`. No new prompt files needed (the existing
`ticket_field.txt` covers per-field reads). `wage_engine.py`, `evals/`, `app/`, `pyproject.toml`
untouched.

---

## 1. Unit tests — GREEN (14/14)

pytest is NOT installed in `.venv-gemma` and `pyproject.toml` is frozen for WS-A, so tests are
plain assert-based with a stdlib runner (they still fail loudly; a later `pip install pytest`
collects the same `test_*` functions unchanged).

```
$ BOLETO_MODEL=mock .venv-gemma/bin/python -m extraction.test_pipeline
PASS  test_dedup_flags_duplicate_not_distinct
PASS  test_ensemble_agreement_stops_at_two_reads
PASS  test_ensemble_disagreement_triggers_third_read_and_flags
PASS  test_extract_session_over_eight_tickets
PASS  test_extract_ticket_matches_truth_on_mock
PASS  test_garbage_model_value_flags_never_crashes
PASS  test_quality_gate_passes_sharp_ticket
PASS  test_quality_gate_rejects_blank_image
PASS  test_reconcile_mismatch_blocks_receipt
PASS  test_reconcile_ok_when_totals_match
PASS  test_reconcile_orphan_day
PASS  test_repair_empty_when_consistent
PASS  test_schema_validators
PASS  test_single_digit_repair_finds_minimal_fix

14 passed, 0 failed, 14 total
```

Each test can actually fail: `test_quality_gate_rejects_blank_image` caught a real bug during
development (the exposure check flagged normal white-background documents as over-exposed —
fixed to only retake on crushed-black or ink-less blow-out).

---

## 2. End-to-end on the 8 synthetic tickets (mock) + adaptive-sampling savings

```
$ BOLETO_MODEL=mock .venv-gemma/bin/python -m extraction session
model=mock  tickets=8  dir=/Users/kannishknaidu/geeeeeemm/tickets
per-field flag rate : 0.0
quality verdicts    : ['ok', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok']
fields extracted    : 100
model reads (calls) : 200  (adaptive: 200 vs 300 at fixed K=3 → 100 saved)
seconds/ticket      : 0.0318  (total 0.254s over 8 tickets)
session period      : 2026-07-10 .. 2026-07-17
```

- **Per-field flag rate = 0.0** on the mock, because the mock is a *noiseless* oracle: all K reads
  are identical → unanimous → nothing flagged. This is honest, not a bug — the flag machinery is
  proven separately by `test_ensemble_disagreement_triggers_third_read_and_flags` (a disagreeing
  read is flagged) and `test_garbage_model_value_flags_never_crashes` (invalid value flagged). A
  real model's per-read variance is what drives a non-zero field flag rate; that number is WS-C's
  to measure against the degradation suite.
- **Adaptive sampling (2 reads, 3rd only on disagreement)** saved 100 of 300 model calls (33%) on
  this run — exactly the §8b lever, measured, zero accuracy loss since all fields were unanimous.
- `extract_ticket_matches_truth_on_mock` proves the crop→read→vote→reassemble path returns the
  exact truth: `W100`, `2026-07-10`, units `[26, 77, 46]`, zero flags.

---

## 3. Dedup demo (perceptual-hash, #10)

```
$ BOLETO_MODEL=mock .venv-gemma/bin/python -m extraction dedup
dedup demo (a session where day-0 was photographed twice):
  ticket_00.png            DUP → group dup0
  ticket_03.png            unique
  ticket_00_again.png      DUP → group dup0
  ✓ duplicate flagged (shared group), never silently dropped
```

Near-duplicates share a `dedup_group` id (pHash Hamming ≤ 6); a genuinely different ticket stays
`None`. Duplicates are flagged for the human, never auto-dropped — the §5a "same day photographed
twice must not double-count, but the human decides" rule.

---

## 4. Checksum single-digit-repair catch demo (#15 / E1)

```
$ BOLETO_MODEL=mock .venv-gemma/bin/python -m extraction checksum
checksum demo (units×rate must satisfy the stub's piece line):
  stub piece line (witness 2) : 293.0
  extracted Σ(units×rate)     : 281.0   ✗ inconsistent
  minimal single-digit repair : rows[1].units 71→77  [SUGGESTION ONLY — never auto-applied]
  evidence                    : Σ(units×rate) 281.0 → 293.0 if 71→77
  ✓ misread digit caught by the ticket's own arithmetic — suggested, not applied
```

A confident wrong digit (77 read as 71) makes the row sum disagree with the stub's piece line; the
repair search finds the minimal one-digit edit that restores consistency and surfaces it **as a
suggestion with evidence — never auto-applied**. "A misread digit has to defeat the ticket's own
arithmetic."

---

## 5. Two-witness reconciliation catch demo (#30)

```
$ BOLETO_MODEL=mock .venv-gemma/bin/python -m extraction recon
two-witness reconciliation demo (ticket Σ piece vs stub piece line):
  matching stub  → status=ok
  tampered stub  → status=mismatch  flags=['amount_mismatch']
    detail: ticket Σ piece = 150.0, stub piece line = 175.0
  ✓ mismatch blocks the receipt (contract.has_unconfirmed_flags → True)
```

Ticket-side piece total (witness 1) vs the stub's piece line (witness 2). A $25 discrepancy raises
a distinct `amount_mismatch` flag; `test_reconcile_orphan_day` additionally proves `orphan_day`
flags for a ticket dated outside the stub period. An unresolved mismatch makes the frozen contract's
`has_unconfirmed_flags()` return True, so **no receipt prints** until it is resolved (F4 safety
property, enforced structurally, not by UI courtesy).

---

## 6. Real-model smoke run (`gemma3:latest`, 1 ticket) — latency captured

```
$ BOLETO_MODEL=gemma3:latest .venv-gemma/bin/python -c "<extract_ticket ticket_00, K=2, timed>"
available(gemma3:latest) = True
seconds/ticket (real, K=2): 15.0s
quality: ok None
fields read: 5  flagged: 5
worker_id -> None ; date -> None ; rows -> []   (truth: W100 2026-07-10 units [26,77,46])
```

- **Measured seconds/ticket (real, K=2, per-field crops): 15.0s.** Only 1 ticket was run — the GPU
  is shared with a sibling agent, and the instruction caps the real run at 1–2 tickets.
- **tokens/s: not available** — the frozen `core/model_client.py` returns `tokens_per_s=None`; it
  does not expose token counts. Reporting `None` rather than inventing a number.
- The path executes end-to-end without error. **Accuracy is poor — see WOUND below.**

---

## WOUND — per-field-crop reads are inaccurate on the printed synthetic set + gemma3

Two cheap diagnostic probes (pasted) locate it precisely:

```
WHOLE-TICKET read -> {'worker_id': 'W100', 'date': '2026-07-10', 'strawberry_units': 26,
                      'rate': 1.75, 'blackberry_units': 46, ...}   (2.7s)   ✓ reads correctly
HEADER-CROP worker_id read -> {}                                            ✗ empty
rows[0].units (crop, upscaled 3×) -> {'value': None}   (truth 26)           ✗ null
```

Root cause (traced, not guessed):
1. **`gemma3:latest` reads the whole 520×380 ticket well (W100 + date + most numbers in 2.7s) but
   returns `{}` on the isolated 496×26px header crop** — the crops are below its reliable read size.
   Upscaling the crop 3× did **not** fix it (tested).
2. **`format_0.json`'s per-cell boxes don't align to the generator's rendered rows.** `htr_harness`
   draws each row as ONE string (`"{crop:12s} units: {units:4d} rate: ${rate:.2f}"` starting at
   x=20); the frozen `cell_boxes` (`units:[150,300]`, `rate:[300,460]`) are approximate and slice
   through glyphs, so a units crop contains partial/garbled text → the model returns null.

Why this is NOT a WS-A logic bug: the pipeline logic (crop → K-vote → validate → flag → checksum →
dedup → reconcile → session assembly) is fully exercised and correct on the mock oracle (§1–§5).
The per-field-crop design is the right one for the **shipping** path — E4B via MLX on real
handwritten tickets with properly authored `formats/format_1..N.json` — where short-transcription
crops play to E4B's strength and give the review screen its provenance images. On **this printed
synthetic data with gemma3 as a sanity model**, whole-ticket reading simply beats tiny crops.

Fix owners / cheapest next step (out of WS-A scope, flagged not built — ponytail: don't fix data
in the extractor):
- **WS-C** authors real handwritten-ticket formats with generous field boxes and generates the
  handwritten set; re-run this same pipeline against it.
- The real E4B (`mlx-community/gemma-4-e4b-it-8bit`) — not the ollama sanity model — is the intended
  extractor; wire it in `core/model_client.py` (a frozen-file change, so WS-E/orchestrator territory).
- Optional cheap lever if per-field crops stay weak on some formats: a **whole-ticket-read fallback**
  that slices fields when every crop returns null. Deliberately NOT built now — it would mask the
  per-field design that carries the pitch's depth story, and the mock already proves the logic.

---

## Contract conformance & trust boundary

- Output is exactly `SessionExtraction` / `TicketExtraction` / `ExtractionResult` / `Quality` /
  `Reconciliation` from the frozen `core/contracts.py` (imported, not redefined). `contracts.py`
  and `model_client.py` self-checks re-run green after all WS-A work.
- **Trust boundary honored:** every model value is schema-validated per field
  (`units` int≥0, `rate`/`_hours` number≥0, `date` `YYYY-MM-DD`, `worker_id` non-empty). Invalid or
  `null` → `flagged` with reason `schema:<why>`, `value` preserved as read — never a crash, never a
  guess (`test_garbage_model_value_flags_never_crashes`).

## `kn:` shortcuts left (named ceilings)
- `pipeline.py` quality thresholds (`_BLUR_MIN_VAR=12`, `_CLIP_MAX_FRAC=0.35`, dedup Hamming=6):
  hand-set against the printed set; **recalibrate on WS-C's parametric degradation suite**.
- `pipeline.py` mock-vs-real read branch in `_read_once`: the mock is a whole-ticket oracle, so
  per-field reads slice the oracle; the real path reads crop pixels. Everything downstream is
  identical on both paths.
- TTA (`_tta_reads`) is a no-op on the pixel-blind mock; it composes with the ensemble on the real
  path. Whether it helps is a measure-and-keep call for WS-C.

## BLOCKED
- **pytest / hypothesis-pytest not installed in `.venv-gemma`**, and `pyproject.toml` is frozen for
  WS-A → used an assert-based runner instead. Not blocking (tests fail loudly), but flagging so the
  orchestrator can `pip install pytest` if uniform pytest collection is wanted across workstreams.
