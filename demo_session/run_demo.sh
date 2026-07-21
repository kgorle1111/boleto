#!/usr/bin/env bash
# ONE COMMAND boots the whole Boleto demo (WS-E integration).
#
#   bash demo_session/run_demo.sh
#
# Default = MOCK adapter: deterministic, GPU-free, the guaranteed caught-error beat
# (day_03 smudge → 293 vs 298 disagreement → review → correct → receipt). It exercises
# the REAL server, the REAL F4 gate, the REAL WS-B error-budget residual, and the durable
# hash-chained sqlite history. Three flows: happy path, caught-error path, and the
# two-witness reconciliation-MISMATCH path (the gate blocks a receipt that can't reconcile).
#
# For the REAL model path (extraction + audit on gemma3/MLX), boot the app with:
#   BOLETO_ADAPTER=real BOLETO_MODEL=gemma3:latest .venv-gemma/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8010
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv-gemma/bin/python
B=http://127.0.0.1:8010

echo "== building demo fixtures (incl. the reconciliation-mismatch variant) =="
$PY demo_session/build.py
rm -f demo_session/history.db        # fresh tamper-evident chain each run (determinism)

echo "== booting server (127.0.0.1 only, mock adapter) =="
BOLETO_ADAPTER=mock BOLETO_MODEL=mock \
  $PY -m uvicorn app.server:app --host 127.0.0.1 --port 8010 --log-level warning &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
for i in $(seq 1 20); do curl -s -o /dev/null "$B/api/trust" && break || sleep 0.4; done

echo; echo "== 1. create pay-period session =="
SID=$(curl -s -X POST "$B/api/session" | tee /dev/stderr | $PY -c "import sys,json;print(json.load(sys.stdin)['session_id'])")

echo; echo "== 2. stream per-ticket extraction (SSE — receipt builds line-by-line) =="
curl -s --max-time 5 -N "$B/api/session/$SID/events" | grep -o '"index": [0-9]*\|"has_flag": [a-z]*' | paste - - || true

echo; echo "== 3. CAUGHT-ERROR PATH: ask for receipt while the smudged field is unconfirmed =="
curl -s -w "  -> HTTP %{http_code}\n" "$B/api/session/$SID/receipt"

echo; echo "== 4. human confirms the flagged field (293 vs 298 -> worker picks 298) =="
curl -s -X POST "$B/api/session/$SID/confirm" -H 'Content-Type: application/json' \
  -d '{"field":"d3.rows[0].units","value":"298"}' -w "\n  -> HTTP %{http_code}\n"

echo; echo "== 5. HAPPY PATH: receipt now prints (gate open), residual error from WS-B budget =="
curl -s "$B/api/session/$SID/receipt" | $PY -c "import sys,json;d=json.load(sys.stdin);print('  total_owed',d['total_owed'],'paid',d['amount_paid'],'shortfall',d['shortfall'],'| residual_error',d['residual_error'],'| chain_hash',(d.get('chain_hash') or '')[:12],'| state',d['state'])"

echo; echo "== 6. spoken receipt (macOS say -> WAV) =="
curl -s -X POST "$B/api/tts" -H 'Content-Type: application/json' \
  -d '{"text":"Le deben 320 dolares","lang":"es"}' -o /tmp/boleto_demo.wav -w "  tts -> HTTP %{http_code} %{size_download} bytes\n"

echo; echo "== 7. history + pattern banner (durable, hash-chained sqlite; tamper-evident) =="
curl -s "$B/api/history" | $PY -c "import sys,json;d=json.load(sys.stdin);print('  pattern:',d['pattern'],'| chain_verified:',d['chain_verified'],'|',d['summary'])"

echo; echo "== 8. RECONCILIATION-MISMATCH PATH: two-witness fails -> gate blocks, no field to confirm =="
MID=$(curl -s -X POST "$B/api/session" -H 'Content-Type: application/json' -d '{"variant":"mismatch"}' | $PY -c "import sys,json;print(json.load(sys.stdin)['session_id'])")
curl -s -w "  -> HTTP %{http_code}\n" "$B/api/session/$MID/receipt"
echo "  (an unresolved ticket-vs-stub mismatch NEVER prints a receipt — the safety property)"

echo; echo "== done. server pid $SRV will be killed on exit =="
