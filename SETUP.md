# SETUP — things only Kannishk can do

These are the human-in-the-loop items (plan §8 / §10.6). Agents built everything
that can be built without a person, consent, a GPU run, or a legal conversation.
Ordered by lead time — start #1 today.

## Longest lead time — start now
1. **Consent channel for real tickets.** Contact a worker center (CRLA / Center for
   Farmworker Families). Goal: 20–30 photos of ACTUAL handwritten punch tickets under
   consent, plus a pilot partner. This is the gate the synthetic set cannot replace —
   the handwriting floor is still unknown. Fallback: hand-write 20 tickets yourself.
2. **LoRA fine-tune run** (Colab A100/T4 or M3 Max MLX-LM) on WS-C's generated set;
   make the accept/reject call on the before/after eval delta. WS-C left the training
   set and the eval harness ready — `evals/run_eval.py` produces the before number now.

## Before the demo
3. **Human baseline.** Run 2–3 people through `evals/results/human_baseline/` (answer
   sheet + scoring script are prepared) so the pitch can compare model vs human.
4. **Verify the CA minimum-wage config.** `core/wage_engine/minimum_wage.json` seeds
   2026 CA MW at a **best-estimate placeholder** (WS-B confidence ~50%, marked `kn:`).
   Confirm the real state value + any local ordinance before any real receipt.
5. **Legal wording pass.** One advocate/lawyer conversation: confirm the receipt copy
   and statute citations read as "information you can check," not legal advice. The
   LC §1194.2 claim-value base is intentionally generous and labeled "potential" —
   confirm the correct base (whole shortfall vs sub-MW slice only).

## Environment
6. Finish the E4B MLX pull if you want the MLX path; `ollama gemma3:latest` is already
   installed and is what the real runs used.
7. **The 90-second demo delivery itself** (script in `project creation plan.md` §0).

## Secrets that must NEVER enter the repo
- `ANDROID_KEYSTORE` / `KEYSTORE_PASS` — GitHub Actions secrets only (post-hackathon).
- Optional `HF_TOKEN` — personal dev env only.
The shipped product has **zero** secrets, accounts, or telemetry by design. If anyone
proposes an env-configured backend URL, the design is being violated — the correct
number of servers is zero.
