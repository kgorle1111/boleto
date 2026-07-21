# WS-F — Security pass (SecOps)

Scope: verify the "nothing ever leaves the device" promise **structurally**, plus local-data
handling. Files audited: `app/server.py`, `core/model_client.py`,
`core/wage_engine/history.py`, `app/real_contracts.py`. Every claim below is backed by a
command that was run on this machine. **No control was weakened; issues are flagged, not fixed.**

## Headline verdict

The zero-server / zero-egress promise **holds and is structurally enforced.** Localhost-only
binding, single permitted egress endpoint (localhost ollama), and zero hardcoded secrets are all
confirmed by direct evidence. Two local-data findings: one **latent** wipe-completeness gap (holds
on this build, rides a compile default), and one **real, demonstrated** hash-chain integrity hazard
under concurrent writes. Neither leaks data off-device; both concern on-device integrity/erasure.

| # | Check | Result | Severity |
|---|-------|--------|----------|
| 1 | Localhost-only binding | **PASS** — socket bound `127.0.0.1`, LAN IP refused | — |
| 2 | No outbound calls (only localhost ollama) | **PASS** — sole egress is `127.0.0.1:11434` | — |
| 3 | Zero hardcoded secrets | **PASS** — none in tree | — |
| 4a | Wipe-all completeness | **PASS on this build**, latent gap off it | LOW→MED |
| 4b | Hash-chain tamper-evidence | **PASS** — verify_chain fails on in-place edit | — |
| 4c | `check_same_thread=False` concurrency | **FAIL** — chain corrupts + data lost under concurrent store | MEDIUM |
| — | At-rest encryption (vs plan §3) | Plaintext DB — impl/spec gap | LOW (demo) |

---

## 1. Localhost-only binding — PASS

Config: `app/server.py:47` `HOST, PORT = "127.0.0.1", 8010`; `:322` `uvicorn.run(app, host=HOST, ...)`.
`grep` for `0.0.0.0` across all `.py`: **NONE**. Only `host=` binding in the tree is `HOST`.

Booted the real server and inspected the live listening socket:

```
$ lsof -nP -iTCP:8010 -sTCP:LISTEN
python3.1 50919 ... IPv4 ... TCP 127.0.0.1:8010 (LISTEN)

$ curl -s http://172.20.20.20:8010/api/trust   # LAN IP
refused on LAN IP — GOOD (loopback-only confirmed)
```

The socket physically cannot accept a connection from anything but loopback. Binding is correct
and structural, not policy.

## 2. No outbound calls at runtime — PASS

Full grep of runtime code (`app/ core/ extraction/`, tests excluded) for
`urllib|requests|httpx|aiohttp|http.client|socket.|urlopen|websocket|smtplib|ftplib|.connect(|Request(`:

- `core/model_client.py:56-68` — `urllib.request` → **`http://127.0.0.1:11434/api/chat`** (localhost ollama, the one permitted endpoint).
- `core/wage_engine/history.py:54` — `sqlite3.connect` (local file, not a network socket).

Grep of every `http(s)://` URL in runtime code returns exactly one: `http://127.0.0.1:11434/api/chat`.
No telemetry, analytics, HF pull, or remote host at read time. `model_client._mlx_resolve`
(`:114-124`) resolves the **local** HF snapshot on disk specifically to avoid a network pull.
TTS (`server.py:248`) writes a wav to the local tempdir; no egress. **Sole runtime egress is
loopback ollama — asserted.**

Note: the live socket probe in §1 already doubles as a negative network assertion — the process
holds no non-loopback socket. A standing regression test would strengthen this (see recommendation).

## 3. Secrets — PASS (zero)

Tree-wide scan (`*.py *.json *.toml *.sh *.md *.txt *.cfg *.ini *.env*`) for password / secret /
api_key / access_key / token / credential / private-key PEM / `AKIA…` / `AIza…` / bearer:
**zero hardcoded secrets.** Every hit is either the word "token(s_per_s)" in latency metrics, or
the planning notes *describing* the `ANDROID_KEYSTORE`/`HF_TOKEN` that live only in CI/personal
env and are explicitly **never in repo**. The design has no accounts/keys and the code matches it.

## 4. Local history storage

### 4b. Hash-chain tamper-evidence — PASS (real)

`history.demo()` stores a chain, then does an in-place `UPDATE ... REPLACE(receipt_json,'20.00','99.00')`
and asserts `verify_chain() is False`. Ran it: **passed.** `verify_chain` (`history.py:92`) recomputes
`sha256(prev + canonical_json_without_chain_hash)` for every row in `seq` order and returns False on
any mismatch — so an edit to any past receipt breaks the chain from that row forward. Tamper-evidence
is genuine, not decorative.

### 4a. Wipe-all completeness — PASS on this build, LATENT gap (LOW→MEDIUM)

`/api/wipe` (`server.py:281`) runs `SESSIONS.clear()` + `DELETE FROM receipts` + `commit()`. It
deletes the **rows** but not the **file**, and does not `VACUUM`. Empirical test:

```
PRAGMA secure_delete = 1   (this build)   journal_mode = delete   auto_vacuum = 0
10 receipts stored → DELETE FROM receipts → grep wiped file for markers: NONE
```

On this machine's sqlite, `secure_delete=1` zeroes freed pages, so **no plaintext survives** — the
wipe is genuinely complete. **The catch:** the code never sets that pragma; it relies on the
compiled default. Upstream sqlite defaults `secure_delete=0` (FAST), where `DELETE` only unlinks
rows and the plaintext lingers in freelist pages, forensically recoverable until overwritten. The
product's real targets are Android (LiteRT) and Tauri desktop — different sqlite builds, different
defaults. So the "wipe-everything" guarantee is **not portable/structural**; it happens to hold here.

- **Business risk:** on a build with `secure_delete=0`, a worker who taps "wipe everything" (the
  literal trust affordance, plan §3/§4) leaves recoverable wage history on the device — the exact
  scenario the button exists to prevent (employer holding the phone; §F8 threat model).
- **Smallest fix (flag only):** make it explicit at connection open in `history.py:__init__` —
  `self.conn.execute("PRAGMA secure_delete=ON")` — and have `/api/wipe` additionally `VACUUM`
  (or delete+recreate the db file) so freed pages are reclaimed and zeroed regardless of build.

### 4c. `check_same_thread=False` concurrency — FAIL (MEDIUM, demonstrated)

`server.py:51` opens **one** shared connection with `check_same_thread=False` and reuses it across
FastAPI's request threadpool (`get_receipt` and `wipe` are sync `def` → run on threadpool threads).
`store()` (`history.py:67`) is a read-modify-write: `_last_hash()` → compute `chain_hash` → `INSERT`,
with **no lock**. This is a TOCTOU on the chain head. Reproduced with 40 concurrent `store()` calls
on the shared connection, exactly as the app wires it:

```
attempted 40 concurrent stores; rows persisted = 33
exceptions raised: 8  →  InterfaceError('bad parameter or other API misuse'), SystemError(...)
verify_chain() after concurrent writes: False
chain breaks/forks (prev_hash != preceding chain_hash): 29
```

Two failures compound: (1) sharing a single sqlite connection across threads without serialization
raises `InterfaceError`/`SystemError` and **silently drops receipts** (7 of 40 lost); (2) the
unlocked RMW lets concurrent calls read the same `prev_hash` and **fork the chain**, so
`verify_chain()` returns False **with no tampering at all**. That directly undermines the feature's
own promise ("her history is tamper-evident") — ordinary concurrent use (two browser tabs, a
double-tapped receipt, a retry) can corrupt the chain and lose data.

- **Mitigating context:** the demo is single-user localhost with near-zero real concurrency, and
  `get_receipt` guards with `if not _stored(sid)`. So in the scripted demo this is unlikely to fire
  — which is why it's MEDIUM, not HIGH. But it's a correctness/integrity bug, not a throughput one,
  and the in-code comment ("Add a threading.Lock if throughput matters", `history.py:52`) misframes
  it as performance.
- **Smallest fix (flag only):** a module-level `threading.Lock` held across the whole
  `_last_hash()`→`INSERT`→`commit()` critical section in `store()` (and covering `wipe`'s DELETE on
  the shared connection). One lock closes both the API-misuse crash and the chain-fork race.

### At-rest encryption — impl/spec gap (LOW for demo)

Plan §3 ("History stays in local **encrypted** storage") and §5 P1 ("local storage encryption")
call for encryption at rest. The live DB is plaintext on disk:

```
live-DB receipt readable in plaintext on disk (no at-rest encryption): True
```

Acceptable for a localhost hackathon demo; noted because the shipped product's stated promise —
and the §F8 "employer holding the phone" adversary — assumes encrypted local storage. Not a
network-egress issue; an on-device confidentiality gap vs. the plan. Upgrade path: SQLCipher / OS
keystore-backed encryption on the Android/Tauro ports (out of scope for this Python reference).

---

## Positives worth recording (checked, sound)

- **No command injection:** TTS (`server.py:248`) passes `text` as an argv element to `say` (no
  shell), capped at 600 chars. Injection-safe as written.
- **No path traversal:** crop handler (`server.py:229`) rejects anything not matching `^[0-9a-f]{64}$`;
  `ticket_img` (`:239`) whitelists `^(day_0[1-9]|stub)\.png$`. `test_f4_invariant.py:60` even asserts
  `..%2f..%2fetc%2fpasswd` is refused. Both trust boundaries hold.
- **Trust-boundary validation on model output:** `_extract_json` (`model_client.py:75`) returns `{}`
  on malformed/garbage output (routes to review), never raises — as designed.
- **F4 gate + F4 tests green:** `pytest app/test_f4_invariant.py` → 6 passed.

## Recommendations (priority order, all flag-only)

1. **[MEDIUM]** Serialize `history.store()` with a `threading.Lock` over the RMW+commit section;
   covers the chain-fork race and the shared-connection API-misuse crash. (4c)
2. **[LOW→MED]** Make wipe portable: `PRAGMA secure_delete=ON` at connect + `VACUUM` (or drop the
   file) in `/api/wipe`, so the erasure guarantee doesn't depend on the sqlite build default. (4a)
3. **[LOW]** Add a standing regression test asserting the process opens zero non-loopback sockets
   during a full session (codify §1/§2 so a future dependency can't silently add egress).
4. **[LOW]** At-rest encryption on the shipped ports (plan §3) — track for P1/P2, not the demo.
