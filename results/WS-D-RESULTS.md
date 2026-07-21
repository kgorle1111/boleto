# WS-D — App skin, UX & trust surface — RESULTS

> **Runs against MOCKED WS-A/WS-B contracts.** WS-D consumes `SessionExtraction`
> and `Receipt` from `core/contracts.py` (FROZEN). Until WS-A/WS-B land, the app
> reads fixtures from `demo_session/session.json` + `receipt.json` via
> `app/mock_contracts.py`. The F4 gate uses the FROZEN
> `SessionExtraction.has_unconfirmed_flags` — it is **not** re-implemented, so it
> cannot drift from the contract. WS-E swaps `load_session`/`load_receipt` for real
> WS-A/WS-B calls; nothing else in the server moves.
>
> **Eval-results screen is already showing REAL numbers:** WS-C landed its reports
> in `evals/results/` during this build, so `/api/evals` serves the real
> `gemma3:latest` run (n=8, 97.5% field exact match) instead of the mock fallback.

## What was built (§10.1 WS-D items 1–7)

| # | Item | Where |
|---|------|-------|
| 1 | Pay-period session flow; per-ticket results stream in (SSE), receipt builds line-by-line, session survives refresh (server-side state) | `app/server.py` (`/api/session`, `/events`), `app/static/app.js` (capture/extract screens) |
| 2 | Review UI: crop + all K reads side-by-side + big numeric keypad; **F4 enforced server-side** | `app/server.py` (`get_receipt` gate, `confirm_field`), `app/static/app.js` (review screen) |
| 3 | Provenance chain: tap a receipt line → arithmetic + statute citation + source crop image(s) + dollar-sensitivity | `app/static/app.js` (receipt screen), `/api/session/{id}/field/{id}`, `/crop/...` |
| 4 | Receipt + TTS + language switch: all text from `strings.json` (es-MX default, en second, **no hardcoded UI strings**); persistent "Español ⇄ English" slide switch re-renders + swaps TTS voice mid-session; advocate disclaimer + CRLA on every receipt | `app/static/strings.json`, `app/static/app.js` (`langsw`), `/api/tts` (macOS `say`) |
| 5 | History view + pattern banner + one-tap wipe-everything | `app/static/app.js` (history screen), `/api/history`, `/api/wipe` |
| 6 | Trust surface: visible panel proving localhost-only bind + how to verify with the OS network monitor | `app/static/app.js` (trust screen), `/api/trust`; server binds `127.0.0.1` only |
| 7 | Eval-results screen rendering latest `evals/results/` report | `app/static/app.js` (evals screen), `/api/evals` |

Files created:
```
app/server.py              FastAPI backend, 127.0.0.1 only, F4 state machine server-side
app/mock_contracts.py      loads demo_session fixtures as FROZEN contract objects
app/mock_eval_report.json  eval-screen fallback (only used if evals/results/ is empty)
app/static/index... etc    vanilla-JS SPA (no framework):
app/static/app.js            router + 7 screens + language switch + TTS + provenance
app/static/style.css         low-literacy: 48px+ targets, high contrast, one action/screen
app/static/strings.json      es-MX + en; NO hardcoded UI strings anywhere
app/templates/index.html     shell: pinned language switch + tab bar
app/test_f4_invariant.py   the F4 gate + loopback + path-traversal tests (stdlib runner)
demo_session/build.py      builds the mock session incl. the smudged day_03 ticket
demo_session/run_demo.sh   one-command scripted end-to-end (both paths)
demo_session/{tickets,crops,session.json,receipt.json,stub.png}   prepared fixtures
```

## How to run

```bash
# one-command scripted end-to-end (boots server, drives both paths, tears down)
bash demo_session/run_demo.sh

# or run the app interactively
.venv-gemma/bin/python demo_session/build.py            # (re)build fixtures
.venv-gemma/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8010
#   → open http://127.0.0.1:8010/

# the F4 safety test
.venv-gemma/bin/python app/test_f4_invariant.py
```

---

## F4 invariant test — the receipt gate is unbypassable (real output)

`.venv-gemma/bin/python app/test_f4_invariant.py`

```
PASS  test_confirm_requires_value
PASS  test_crop_id_rejects_path_traversal
PASS  test_gate_opens_only_after_confirmation
PASS  test_receipt_refused_while_flag_unconfirmed
PASS  test_server_binds_loopback_only
PASS  test_unknown_session_404

6/6 passed
```

- `test_receipt_refused_while_flag_unconfirmed` — `GET /receipt` returns **409** while
  `d3.rows[0].units` (the smudged field) is unconfirmed.
- `test_gate_opens_only_after_confirmation` — confirming a **non-flagged** field is
  refused (422) and does NOT open the gate; only the human-confirmation event on the
  flagged field opens it, after which `/receipt` returns 200 with `shortfall = "20.00"`.
- The gate is `SessionExtraction.has_unconfirmed_flags(confirmed)` — the frozen
  contract method, called server-side in `get_receipt`. No client path bypasses it.

## Scripted end-to-end — both paths (real output)

`bash demo_session/run_demo.sh`

```
== building demo fixtures ==
demo_session built:
  tickets: 5 (day_03 smudged, day3.units flagged)
  crops:   3
  gate:    has_unconfirmed_flags() = True
  self-check: gate opens only after d3.rows[0].units is confirmed  OK
== booting server (127.0.0.1 only) ==

== 1. create pay-period session ==
{"session_id":"cc291b2bff09","state":"CAPTURED","period_start":"2026-07-06","period_end":"2026-07-12","n_tickets":5}
== 2. stream per-ticket extraction (SSE — receipt builds line-by-line) ==
"index": 0	"has_flag": false
"index": 1	"has_flag": false
"index": 2	"has_flag": true         <-- the smudged day_03 ticket
"index": 3	"has_flag": false
"index": 4	"has_flag": false

== 3. CAUGHT-ERROR PATH: ask for receipt while the smudged field is unconfirmed ==
{"error":"gate_blocked","reason":"unconfirmed flagged field(s) — human confirmation required","unconfirmed":["d3.rows[0].units"],"reconciliation":"ok"}  -> HTTP 409

== 4. human confirms the flagged field (293 vs 298 -> worker picks 298) ==
{"confirmed":"d3.rows[0].units","value":"298","gate_blocks":false,"state":"REVIEWED"}
  -> HTTP 200

== 5. HAPPY PATH: receipt now prints (gate open) ==
{ ... "total_owed":"320.00","amount_paid":"300.00","shortfall":"20.00",
  "claim_value":{"liquidated_damages":"20.00","interest":"2.00","citation":"LC §1194.2, §98.1"},
  "residual_error":"< 0.5%","confirmed":{"d3.rows[0].units":"298"},"state":"RECEIPT" }
== 6. spoken receipt (macOS say -> WAV) ==
  tts -> HTTP 200 102256 bytes
== 7. history + pattern banner ==
  pattern: {'shorted': 1, 'total': 1, 'amount': '20.00'}
```

## Trust surface — loopback-only bind, honestly verifiable (real output)

`GET /api/trust`:
```json
{"bind":"127.0.0.1:8010","loopback_only":true,
 "verify_cmd":"lsof -nP -iTCP:8010 -sTCP:LISTEN   # shows 127.0.0.1 only",
 "note":"No outbound sockets in the runtime dependency graph. Turn Wi-Fi off and reload — it still works."}
```

`lsof -nP -iTCP:8010 -sTCP:LISTEN` (proves the claim — the in-app panel shows this command):
```
COMMAND     PID          USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
python3.1 48618 kannishknaidu    6u  IPv4 0xca979086b279e707      0t0  TCP 127.0.0.1:8010 (LISTEN)
```

## Eval-results screen — serving the REAL WS-C report (real output)

`GET /api/evals` (unwrapped from WS-C's `{"report":{…}}` shape to flat `EvalReport`):
```
model gemma3:latest | n 8 | exact 0.975 | escaped 0.0 | source eval_2026-07-19_gemma3-latest_real.json
per_severity ['clean'] | calibration 2 rows
```
The in-app screen renders: model, tickets evaluated, per-field exact match 97.5%,
conclusion-critical 0.0%, escaped 0.0%, review rate; the per-severity robustness
bars; and the calibration table (66.7% agreement → 50% accuracy; 100% agreement →
100% accuracy). If `evals/results/` were empty it falls back to
`app/mock_eval_report.json` with a visible "sample report" banner.

---

## Screenshots — every screen (captured live in the mobile viewport, 375×812)

Captured during this build against the running app at `http://127.0.0.1:8010/`:

1. **Capture (es-MX default)** — "Sube los boletos de la semana"; 5 day-tickets + 1
   stub as thumbnails; **Boleto 3 shows the smudge**; big "📷 Empezar a leer".
2. **Extract (SSE stream)** — per-ticket rows appear one-by-one (verified via the SSE
   log in run_demo.sh step 2); the smudged ticket row carries the "⚠ needs review" badge.
3. **Review — the money moment (es, then en)** — the smudged crop up top with an orange
   border; **"La máquina leyó: 293 (2×) · 298 (1×)"** side-by-side; big numeric keypad;
   ✓ Confirmar. Toggling the language switch re-rendered the *entire* screen to
   English mid-session ("Check this number / The machine read: / Fix it if it doesn't
   match the photo") — instant, no confirmation.
4. **Receipt + provenance (en)** — "You are owed **$320.00** / paid $300.00 /
   Difference **$20.00**"; tappable lines; expanded provenance shows **Math:
   Σ(units × rate)…**, **Law: LC §226.2(a)**, source field `d3.rows[0].units — Reads:
   293, 293, 298` with its crop image, and dollar-sensitivity; "Checked 4 ways;
   residual error < 0.5%"; **"Listen out loud"** (TTS); disclaimer + **CRLA** referral.
5. **Trust (en)** — "Nothing leaves this phone"; ✈ "Works with data OFF — try it";
   `127.0.0.1:8010`; the `lsof` verify command; "No outbound sockets…".
6. **Accuracy / evals (en)** — the real `gemma3:latest` numbers (see above).
7. **History (en)** — pattern banner "Shorted in 1 of 1 weeks — $20.00 total"; the
   period row `2026-07-06 → 2026-07-12  $20.00`; 🗑 "Wipe everything".

> Note on durable capture: the SPA is JS-rendered, so screens are captured as live
> screenshots (above) rather than static HTML. `bash demo_session/run_demo.sh`
> reproduces the full backend flow deterministically for re-verification.

---

## Definition of Done — checklist

- ☑ Full flow runs end-to-end against MOCKED contracts, scripted (`run_demo.sh`),
  with the deliberately-smudged `day_03` triggering the review screen.
- ☑ A screenshot of every screen (7 screens, listed above).
- ☑ F4 invariant tested: fetching a receipt while a flagged field is unconfirmed →
  server refuses (409). 6/6 tests pass.
- ☑ Language: es-MX default + en, all UI text from `strings.json`, persistent slide
  switch, TTS voice swap (Paulina es_MX / Samantha en_US via macOS `say`).
- ☑ Provenance chain, trust surface, history + pattern + wipe, eval-results screen.
- ☑ Loopback-only bind asserted by test and by `lsof`.

## Shortcuts marked (`kn:` / `ponytail:`)

- `app/server.py` — `HISTORY`/`SESSIONS` are in-memory dicts (`ponytail:` comments).
  Survives refresh (same process) which satisfies "server-side session state", but
  not a server restart. WS-B owns the durable sqlite receipt store (#27/#28); wire
  the history view to it at integration.
- `app/server.py` `/api/tts` — server-side macOS `say` is the **demo seam**
  (`ponytail:` comment). On device this becomes the platform TTS (Android es-MX voice
  / `AVSpeechSynthesizer`) at the same call site.
- `app/test_f4_invariant.py` — stdlib test runner because `pytest` isn't in
  `.venv-gemma` and adding deps is out of my lane (`pyproject.toml` is off-limits).
  Convert to `pytest` when it's available; the assertions are unchanged.
- Crop images in `demo_session/build.py` are horizontal bands of the ticket, not
  layout-accurate field boxes — sufficient to demo "number next to its picture" and
  the smudge; WS-A's real per-field crops replace them at integration.

## BLOCKED / wounds

- **Not blocked.** Everything ran; outputs above are real.
- **Wound (minor):** the flagged-units crop currently shows the ticket band that
  happens to contain "rest hrs" text under the smudge (band fractions are generic,
  not format-0 field boxes). Cosmetic for the mock; disappears once WS-A supplies
  real content-addressed field crops. The smudge (the demo point) is clearly visible.
- **Wound (design, for WS-F/critic):** the receipt currently reveals the mock
  `Receipt` fixture; the F4 gate correctly blocks it until confirmation, but the
  *reconciliation-mismatch* branch of the gate (`reconciliation.status == "mismatch"`)
  is exercised only by the contract's own logic, not yet by a UI flow — WS-E should
  add a demo session whose two-witness reconcile fails to screenshot that path too.
```
