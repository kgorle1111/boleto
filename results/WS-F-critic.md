# WS-F — CRITIC pass (adversarial QA on the integrated Boleto system)

Posture: assume broken until proven. Each wound has a runnable repro. Five wounds;
two attacks came back **demonstrably handled** and are recorded as such.

---

## BLOCKER 1 — the "residual < 0.5%" printed on the receipt is not what the running system achieves

**Two independent defects collapse the finalized error budget:**

1. **The two-witness reconciliation is tautological in the shipped path.**
   `app/real_contracts.py:61` builds the stub's `piece_total` FROM the tickets:
   `piece_sum = sum(ticket_piece_total(ticket_values(t)) for t in se.tickets)`.
   `reconcile()` then compares ticket-Σ-piece vs that same number → always equal.
   A *unanimous* misread changes both "witnesses" identically, so it never mismatches.
   Repro:
   ```
   unanimous misread present, stub derived from tickets -> reconcile status: ok
   two-witness caught the escaped error? False
   ```
   WS-E §1 step 8 claims two-witness is "the last automated catch" for the escaped
   (valid-JSON-wrong-numbers-unanimous) case. It is not — the second witness is
   manufactured from the first. Real paystub OCR (the only independent witness) is
   admitted unbuilt (`ponytail:` in real_contracts).

2. **The cross-field checksum never fires in the shipping path.**
   `suggest_single_digit_repair` is called only in tests / `__main__` demos — never in
   `extract_session`/`extract_ticket`. The synthetic tickets carry no row-subtotal line,
   so there is no `target_total` to check against. The layer contributes zero live catch.

**Consequence:** `error_budget.finalized_receipt_residual()` multiplies the base by
`(1−0.5)·(1−0.6)` for checksum + two-witness. For the ONLY error class that can silently
reach a receipt (unanimous-wrong), neither reducer applies and human review never sees it
(not flagged). Real residual = base:
```
claimed finalized residual : < 0.5%
actual (no live reducers)   : < 2%  = 0.018   (its own simulated base, 1.8%)
```
The receipt prints "Checked 5 ways; residual error < 0.5%". The system, on its own
simulated numbers, delivers ~1.8% — **~4.6× optimistic** — and the real-handwriting base
is still unmeasured. House rule: a real eval beats an inflated claim. This is the inflated
claim, and it is printed on the artifact a worker hands to an advocate.

**Why it matters:** it is the pitch's centerpiece SRE number and it is on the receipt.
A judge who reads `real_contracts.py` sees the stub is derived from the tickets in ~10s.

**Smallest fix:** (a) drop checksum + two-witness from `finalized_receipt_residual` until
each fires on real independent data — finalized residual becomes the honest base (state it
as `< 2%`, labelled "post-self-consistency, no independent stub yet"). (b) Change the receipt
string to the count of layers that actually ran this session, or to "< 2% (est.)". Do not
claim a catch the running code did not perform.

---

## SERIOUS 2 — duplicate photos double-count into owed pay; no gate stops it

`dedup_groups` (pipeline.py) detects near-duplicates and tags a shared `dedup_group`, but
that tag is not a `flagged` field, so `has_unconfirmed_flags()` does not block, and
`audit_session` sums every ticket regardless of group. A day photographed twice is counted
twice. Repro:
```
gate blocks on duplicate? False
piece_earnings with the day double-photographed: 600.00   (one 150u×$2 day = $300)
```
A wrong receipt — inflated shortfall, in the worker's favour, i.e. exactly the kind of
overstated claim that discredits the tool in front of an advocate. "Never silently
double-count" (pipeline docstring) is violated silently.
**Fix:** in the session assembly, mark all-but-one member of each `dedup_group` as
`flagged` (reason `duplicate_photo`) so the human picks which to keep, OR exclude duplicates
from the audit sum. One guard where tickets are assembled.

---

## SERIOUS 3 — F4 gate blocks on non-pay-bearing flags (worker_id / crop)

Confirmed in WS-E §2: gemma3 flagged `d3.worker_id` and `d3.rows[0].crop` and the receipt
was blocked until both were confirmed — yet `_parse_ticket` ignores worker_id/crop (not
pay-bearing). The human is forced to hand-confirm fields with zero effect on the receipt.
Conservative-safe, but a demo-tax: combined with a high flag rate it degrades the review
screen toward full manual entry, and it makes the gate fire on noise. `PAY_FIELDS` already
exists in contracts.py — the machinery to route only pay-bearing flags to the *hard* gate
is present and unused.
**Fix:** `has_unconfirmed_flags` hard-blocks only when the flagged field's name ∈
`PAY_FIELDS` (or the flag is a reconciliation mismatch); surface non-pay flags as advisory.
~5 lines in the frozen contract method.

---

## SERIOUS 4 — a legitimately 1-day pay period crashes the server (HTTP 500, not a named state)

`audit_session` raises `ValueError` on `len(tickets) < 2`; `server.get_receipt` calls
`build_receipt`→`audit_session` with no try/except → unhandled 500. Repro:
```
RAISES ValueError (unhandled in server.get_receipt -> HTTP 500)
```
WS-C's fault-injection claims "never an unhandled exception; always degrades to a NAMED
state." The single-ticket boundary is an unhandled exception at the session entry point.
"3 tickets not 6" is fine (≥2); the wound is the <2 boundary and its ungraceful surfacing.
**Fix:** catch the refusal in `get_receipt` and return a 422 with the explanatory message
(a named "single-ticket refused" state), not a 500.

---

## Attacks that came back HANDLED (recorded, not manufactured into wounds)

- **Ticket from the wrong week:** `reconcile()` checks each ticket date against the stub
  period → `orphan_day` flag → status `mismatch` → gate blocks. Genuinely works.
- **Session abandoned mid-review:** server-side session state + SSE replay; nothing reaches
  RECEIPT without the confirmation event. The gate is structural, re-uses the frozen
  contract method, not UI courtesy. Holds.
- **Two stubs:** no code path accepts a second stub — unsupported, not broken. Not a wound.
- **All fields flagged:** review screen shows every flag; tool degrades to manual entry but
  never to a *wrong* receipt. Usability ceiling, not a correctness wound. Cosmetic.

---

## Verdict

The safety *architecture* (human-gate-always-wins, deterministic engine, structural F4) is
sound and survives scrutiny. The **stated reliability number does not** — its two strongest
reducers do not fire on the one error class that can reach a receipt, and the receipt prints
the optimistic figure anyway. Fix BLOCKER 1 (honest residual + honest two-witness claim) and
SERIOUS 2 (double-count) before any demo where a judge can read the source or a worker keeps
the receipt. The other two are proceed-with-changes.
