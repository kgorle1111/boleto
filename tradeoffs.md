# Boleto — Tradeoffs & Decision Record

Every consequential either/or, with what we chose, what it costs, and what would reverse it. Format: decision → why → price paid → reversal trigger.

---

## 1. Distribution: website sideload vs Play Store vs PWA

**Chose: website-first (signed APK sideload + desktop installers), Play Store additive later, PWA rejected for now.**

- **Website sideload** — PRO: zero gatekeeping, ships the day we tag a release, no review cycle that could flag "legal-adjacent" content, QR-poster friendly, aligns with worker-center assisted installs. CON: "allow unknown apps" friction scares exactly the low-trust users we serve; no auto-updates (mitigate: in-app "new version" check against a static JSON — read-only, still no telemetry); Android may show scare warnings.
- **Play Store** — PRO: one-tap install, auto-updates, trust halo. CON: review delay/rejection risk, $25 account, Google could remove it, and a Play listing is visible to hostile parties (employers searching "wage app") — sideload via QR is quieter. VERDICT: do it *after* the pilot; it's additive, not foundational.
- **PWA (browser, WebGPU)** — PRO: literally click-a-link, no install, updates instantly. CON (fatal today): multimodal 4B-class inference in mobile browsers is not dependable on mid-range Android in 2026; model caching in browser storage is evictable (a worker could lose the model on a data-less day — the exact moment they need it); camera+TTS quality worse than native. REVERSAL TRIGGER: when WebGPU + LiteRT-web runs E2B-class vision reliably on a $200 Android phone, the PWA becomes the better primary — revisit every ~6 months.

## 2. Model delivery: bundled in APK vs first-launch download

**Chose: first-launch Wi-Fi download with resume, from a project-controlled HF mirror.**

- Bundled — PRO: install = done, true single artifact. CON: 2-4GB APK punishes data plans (target users often pay per GB), most sideload hosts/stores choke on it, every app update re-ships the model.
- First-launch download — PRO: 30-60MB installer; model fetched once on Wi-Fi (worker center install events make this practical); app updates don't re-download the model. CON: two-step "ready" state; needs a clear progress/resume UI; needs the download URL to stay alive → mitigate by mirroring exact model files to a project HF repo.
- REVERSAL TRIGGER: if pilot shows workers installing alone on cellular and failing the 1.5GB pull, ship a "worker-center USB/local-transfer" flow (model file share via nearby-share) rather than bundling.

## 3. Model: E2B vs E4B on phones

**Chose: E2B (`gemma-4-E2B-it-litert-lm`, Google's own edge artifact) as the phone default; E4B on desktop and on high-RAM phones.**

- E2B — runs on mid-range (~3GB RAM budget), faster, cooler, smaller download. Risk: HTR accuracy floor may be lower — and the whole product rides on that floor.
- E4B — better extraction, but ~2x memory/latency; excludes exactly the cheap devices our users own.
- THE EVAL DECIDES: P0 runs the conclusion-critical benchmark on BOTH. If only E4B passes the ≤5% gate, the minimum-device requirement rises and we say so honestly on the website; if E2B passes, default stays E2B with E4B as a settings upgrade.
- Note: per-field cropped short transcriptions (our pipeline) narrow the E2B/E4B gap vs whole-page reading — the design already plays to the small model.

## 4. Android app: native Kotlin vs Flutter/React Native

**Chose: native Kotlin.**

- Kotlin — PRO: LiteRT-LM integration is first-class (Google's samples are Kotlin), CameraX control for the crop pipeline, smallest APK, no bridge tax on image buffers. CON: Kannishk is Python-first — team's mobile lead carries this; bus-factor risk.
- Flutter/RN — PRO: shared UI with desktop later. CON: every ML/camera boundary is a plugin seam; those seams are exactly where our pipeline lives. Framework rule from CLAUDE.md: adopt only for a named pain — cross-platform UI is not yet a pain (desktop UI is deliberately plain HTML in Tauri).
- REVERSAL TRIGGER: if iOS ships someday AND the UI has grown large, revisit; not before.

## 5. Desktop: Tauri+llama.cpp vs Electron vs Python app

**Chose: Tauri (Rust shell) with llama.cpp sidecar running E4B GGUF.**

- Tauri — small installers (~10MB shell), signed, no Chromium bundle; llama.cpp is the most battle-tested GGUF runtime and needs no Python on the user's machine.
- Electron — 150MB+ of Chromium to show four screens; rejected on bloat alone.
- "Ship the Python app" — PRO: reuses reference code directly. CON: shipping Python to end users (installers, venvs, Gatekeeper) is misery; rejected for users, kept for dev/eval tooling.
- Wage-engine port question (Kotlin + Rust ports vs one shared core): chose per-platform ports validated against shared `golden_cases.json` — the engine is ~200 lines; two small ports cost less than one FFI layer. REVERSAL TRIGGER: if the engine grows past ~1kloc of law, consolidate to one Rust core + bindings.

## 6. Extraction: whole-ticket single shot vs per-field crops

**Chose: per-field cropped transcription with per-field confidence.**

- Whole-ticket — one call, simpler pipeline. CON: plays to the model's weakness (dense page), one hallucinated row poisons the receipt, no per-field confidence to route to human review.
- Per-field crops — more calls (still fast; fields are tiny), matches the model's documented strength (short transcriptions), enables the review screen's number-next-to-crop UX, localizes errors. This is also the accuracy lever that may keep E2B viable (see #3).
- PRICE: needs a field-region detector (deterministic layout heuristics per ticket format first; model-assisted region detection only if formats vary too much — in that order).

## 7. Trust architecture: zero-telemetry vs debuggability

**Chose: absolute zero network after model download. No crash reporting, no analytics, nothing.**

- PRICE PAID (real): we are blind — no crash rates, no usage funnels, no field error telemetry. Debugging relies on: the pilot (watch real users at the worker center), an in-app local diagnostic log the USER can choose to show/export to a human, and reproducing on reference devices.
- Why pay it: the product's entire adoption case is "provably sends nothing." One analytics endpoint — even innocuous — makes the airplane-mode demo a lie and hands a hostile party a network trace to point at. Trust is the moat (constraint-as-moat: cloud incumbents structurally can't follow).
- NON-REVERSIBLE by policy. If diagnostics are ever needed, the only acceptable form is user-initiated, visible export of a local file to a human they choose.

## 8. Receipt posture: information vs advice; flag vs file

**Chose: the receipt states extracted numbers + deterministic arithmetic + the law's formula, and stops. Never files, never advises, never estimates beyond the math.**

- PRICE: less "helpful" than an app that says "you should file a claim, here's the button." Deliberate: filing crosses into liability and — worse for the user — into retaliation-triggering territory the worker didn't choose.
- The human path: export-as-image → show a worker-center advocate. The advocate is the actuator; Boleto is the evidence.
- Wording (not architecture) is the residual legal exposure → SETUP.md item: one advocate/lawyer review of receipt copy.

## 9. Language: Spanish + English only (REVISED 2026-07-19 — Mixteco eliminated)

**Chose: Spanish (es-MX) default UI+voice via system TTS; English toggle. Nothing else.**

- Original decision (superseded): Mixteco recorded phrase bank by a community speaker. Eliminated because: no TTS exists (corpus problem, 12+ mutually unintelligible variants); the phrase bank required a community-speaker relationship off the critical path; and a partially-covered third language is worse than two covered completely — it also carried the impact-washing risk the judge flagged.
- PRICE: some monolingual Mixteco speakers are not served; the Spanish-speaking advocate channel is the mitigation.
- REVERSAL TRIGGER: a worker-center partner explicitly requests Mixteco AND supplies the speaker — then the phrase-bank design (receipt utterance space is small and bounded) comes back off the shelf.

## 10. History storage: keep vs don't

**Chose: keep past receipts in locally-encrypted storage, with a one-tap wipe-everything button and no lock-in.**

- Keeping history is what makes a *pattern* of underpayment visible across weeks — that's the advocate-grade evidence. But a phone can be seized/inspected by exactly the parties the worker fears.
- Mitigations chosen: Android Keystore-backed encryption at rest; wipe-all in two taps; no cloud copy exists anywhere by construction (see #7). Offer "don't save" mode at first launch.
- Rejected: auto-expiring storage (destroys the evidence value silently).

## 11. Open-source: public repo vs private

**Chose: public repo (Apache-2.0), except `evals/tickets_real/` which never leaves local machines (consented worker data).**

- PRO: auditability IS the trust story ("don't believe us — read it"); community/academic contributions to guards and formats; hackathon/portfolio value. CON: employers can read the guards too (fine — the law is public); a hostile fork could add telemetry (mitigate: name + signing key are the trust anchor, say "only install from our page/QR").

## 12. Scope: one crop/region's ticket formats vs universal

**Chose: Pajaro Valley berry tickets first. Ticket-format packs as data (`formats/*.json` layout heuristics), not code.**

- Universal handwriting-anywhere is a mirage that would blow the accuracy floor. One region's handful of employer formats is enumerable, testable, and where the pilot is. New regions = new format pack + eval set, added deliberately.
- REVERSAL TRIGGER: three inbound requests from another region/crop with ticket samples in hand → build that pack.
