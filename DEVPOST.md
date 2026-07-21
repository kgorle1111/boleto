# Boleto — Devpost submission copy

Paste-ready text for the Devpost form. Screenshots are in `assets/screenshots/`
(upload all four; lead with `2-review.png`).

## Project title

Boleto: on-device wage audit for farmworkers.

Tagline: Photograph your pay tickets and find out what you are legally owed, in
Spanish, on your own phone.

## Inspiration

California piece-rate farmworkers get paid per bucket, per row, per tray. They are
often underpaid for the rest breaks and non-productive time the law requires (Labor
Code §226.2). The people it happens to are usually the least equipped to catch it.
The evidence is a stack of handwritten punch tickets and a printed paystub, the math
is statutory, English is often a second language, and handing your documents to a
stranger can feel risky. So the underpayment stands. We wanted the audit to run on
the worker's own phone, in their language, with nothing leaving the device.

## Problem

Piece-rate wage underpayment is common and hard to detect, and the odds are stacked
against the worker. Checking a pay period means reconciling handwritten daily tickets
against a paystub under California wage law. That is arithmetic most workers cannot be
expected to do, in a language that often is not theirs, with privacy fears that
discourage sharing documents. Legal aid can help but is limited and reactive. Money
that is owed goes unclaimed because nobody can cheaply and privately show it is owed.

## Solution

Boleto runs on the device. The worker photographs the week's punch tickets and the
paystub. A local Gemma model reads the numbers and only the numbers. It never decides
anything legal. Those numbers go into a deterministic California-wage-law engine that
recomputes what is owed: piece earnings, rest-break pay, minimum-wage make-up, and
overtime. Money is exact `Decimal`, unit-tested against golden cases. The output is a
line-itemized receipt, read aloud in Spanish, with statute citations and a potential
claim value, that a worker can bring to an advocate. Nothing leaves the phone. When
the model's reads disagree on a number, the app stops and asks the person to confirm
it, so no unconfirmed value becomes a legal claim.

## Key features

- Runs on the device and stays private. The model and the engine are local, and the
  server binds to `127.0.0.1` only. It works with Wi-Fi off. Sensitive documents never
  leave the phone.
- The AI reads, the law computes, the human decides. The model extracts numbers. Every
  legally meaningful number is recomputed by a deterministic, auditable engine (6 golden
  known-answer cases plus property tests). No model output is ever a legal conclusion.
- A self-consistency review gate. Each number is read three times. When the reads agree,
  they are trusted. When they disagree, the number goes to the person: the machine read
  293 or 298, you decide. This is calibrated, not cosmetic (see accuracy).
- Spanish first, with an English toggle and a spoken receipt for readers who prefer to
  listen.
- Provenance and a real receipt. Tap any line to see the source crop. Statute citations
  (LC §1194.2, §98.1) and a potential claim value are attached, along with an honest
  residual-error figure.
- Tamper-evident local history, wipeable in one tap.

## Accuracy (measured)

On 8 real printed tickets with Gemma 3 running locally: 0% escaped-error rate (the ship
bar is 5% or lower), 0% wrong legal conclusions, 95% of fields read exactly right, about
5 seconds per ticket. The gate is calibrated: when the three-read ensemble agrees it is
right every time (46 of 46), and when it disagrees it is wrong every time (0 of 2), so
disagreement is exactly what goes to the human. Honest scope: this is the printed set
(n=8). Real handwriting is the next measurement and is not done yet. The full verdict,
error budget, and open problems are in the repo's FINAL-REPORT.md.

## Technologies used

- Gemma running locally: Gemma 3 vision through Ollama for the numbers above, and Gemma
  4 E4B through MLX as a second fully-offline backend.
- Python 3.12, FastAPI, and a vanilla-JS single-page app (no framework). The server
  enforces the human-review gate as a state machine.
- A deterministic wage engine in pure Python with `Decimal` money and statute-citation
  data, plus a hash-chained SQLite receipt history.
- Extraction pipeline: per-field cropping, a self-consistency ensemble, a quality
  pre-gate, perceptual-hash (imagehash) dedup, and checksum reconciliation.
- Eval harness: a synthetic-handwriting generator (Pillow and NumPy), a degradation
  suite, and calibration, all one-command reproducible. Hypothesis property tests. 46
  tests in total.
- On-device text to speech for the spoken receipt.

## Target users

California piece-rate farmworkers, often Spanish-speaking, sometimes with limited
literacy, and sensitive about privacy. Also the legal-aid advocates (for example
California Rural Legal Assistance) who receive the resulting receipt as a starting
point. It is built phone-first for the worker, not desktop-first for an analyst.

## What's next

Measure the real-handwriting accuracy floor on consented tickets. Get the statute
wording reviewed by counsel. Verify the 2026 California minimum-wage figure. Build a
native Android port with at-rest encryption. None of these are needed to run today's
demo. They are the path to putting it in a real worker's hands.

## Why we ship the base model (our ML work, and a deliberate negative result)

We tried to beat our own model. We fine-tuned four LoRA specialists on synthetic
handwriting to push the accuracy floor higher. Every one of them lost to the stock
model, and our eval caught each regression before it could ship, so the base model
stays in production. The diagnosis is committed in the repo: the adapters memorized
their training images instead of generalizing, because our synthetic data contained
nothing the base model did not already know. The lesson is the whole point of the
product. The reliability win is not a bigger model, it is the system around it: the
self-consistency ensemble, the deterministic engine, and the human gate. On synthetic
tickets the stock model is already the ceiling. Real handwritten data is where a
specialist could still earn its place, and that is an explicit next step. Trying to
beat our model three ways and having our eval stop us every time is the discipline we
are claiming.

## Team

Solo submission. (Add your name and role.)

## Links

- Repository: (paste your public GitHub URL)
- Demo: the four screenshots in `assets/screenshots/`. A screen recording is optional;
  screenshots meet the requirement.
