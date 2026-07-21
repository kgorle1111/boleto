"""Thin WS-A CLI. Model via env BOLETO_MODEL (mock | gemma3:latest).

    python -m extraction session [ticket_dir]   # extract 8 tickets, flag rate + timing
    python -m extraction dedup                    # perceptual-hash dedup demo
    python -m extraction checksum                 # single-digit-repair catch demo
    python -m extraction recon                    # two-witness reconciliation catch demo
    python -m extraction smoke                     # all of the above, one shot (for RESULTS)
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from core.model_client import DEFAULT_MODEL, available
from extraction import pipeline as P

ROOT = Path(__file__).resolve().parent.parent
TICKETS = ROOT / "tickets"


def _crops() -> Path:
    return Path(tempfile.mkdtemp(prefix="wsa_crops_"))


def cmd_session(ticket_dir: Path | None = None) -> None:
    tdir = ticket_dir or TICKETS
    paths = sorted(tdir.glob("ticket_*.png"))
    print(f"model={DEFAULT_MODEL}  tickets={len(paths)}  dir={tdir}")
    t0 = time.perf_counter()
    se, stats = P.extract_session(paths, None, _crops())
    dt = time.perf_counter() - t0
    n = len(se.tickets)
    print(f"per-field flag rate : {P.flag_rate(se)}")
    print(f"quality verdicts    : {[t.quality.verdict for t in se.tickets]}")
    print(f"fields extracted    : {stats.fields}")
    print(f"model reads (calls) : {stats.calls}  "
          f"(adaptive: {stats.calls} vs {stats.fields * 3} at fixed K=3 → "
          f"{stats.fields * 3 - stats.calls} saved)")
    print(f"seconds/ticket      : {dt / n:.4f}  (total {dt:.3f}s over {n} tickets)")
    print(f"session period      : {se.period_start} .. {se.period_end}")


def cmd_dedup() -> None:
    d = _crops()
    from PIL import Image

    dup = d / "ticket_00_again.png"
    Image.open(TICKETS / "ticket_00.png").save(dup)  # same day photographed twice
    paths = [TICKETS / "ticket_00.png", TICKETS / "ticket_03.png", dup]
    groups = P.dedup_groups(paths)
    print("dedup demo (a session where day-0 was photographed twice):")
    for p, g in zip(paths, groups):
        tag = f"DUP → group {g}" if g else "unique"
        print(f"  {p.name:24s} {tag}")
    assert groups[0] == groups[2] and groups[1] is None, groups
    print("  ✓ duplicate flagged (shared group), never silently dropped")


def cmd_checksum() -> None:
    truth = json.loads((TICKETS / "truth.json").read_text())[0]  # W100, 3 rows
    true_total = round(sum(r["units"] * r["rate"] for r in truth["rows"]), 2)
    misread = json.loads(json.dumps(truth))
    misread["rows"][1]["units"] = 71   # a confident wrong digit: real 77 → read 71
    got = P.ticket_piece_total(misread)
    print("checksum demo (units×rate must satisfy the stub's piece line):")
    print(f"  stub piece line (witness 2) : {true_total}")
    print(f"  extracted Σ(units×rate)     : {got}   ✗ inconsistent")
    sugg = P.suggest_single_digit_repair(misread, true_total)
    print(f"  minimal single-digit repair : {sugg[0]['field']} "
          f"{sugg[0]['from']}→{sugg[0]['to']}  [{sugg[0]['note']}]")
    print(f"  evidence                    : {sugg[0]['evidence']}")
    assert sugg[0]["from"] == 71 and sugg[0]["to"] == 77, sugg
    print("  ✓ misread digit caught by the ticket's own arithmetic — suggested, not applied")


def cmd_recon() -> None:
    from PIL import Image

    d = _crops()
    t0 = P.extract_ticket(TICKETS / "ticket_01.png", d / "crops")  # W101, 2026-07-11
    total = P.ticket_piece_total(P.ticket_values(t0))

    def make_stub(name: str, **fields) -> Path:
        img = d / name
        Image.new("RGB", (400, 200), "white").save(img)
        (d / f"{name}.truth.json").write_text(json.dumps(fields))
        return img

    good = P.extract_stub(make_stub("stub_ok.png", worker_id="W101", piece_total=total,
                                    period_start="2026-07-11", period_end="2026-07-11",
                                    hours_paid=9, amount_paid=total))
    bad = P.extract_stub(make_stub("stub_bad.png", worker_id="W101", piece_total=total + 25,
                                   period_start="2026-07-11", period_end="2026-07-11",
                                   hours_paid=9, amount_paid=total))
    print("two-witness reconciliation demo (ticket Σ piece vs stub piece line):")
    r_ok = P.reconcile([t0], good)
    print(f"  matching stub  → status={r_ok.status}")
    r_bad = P.reconcile([t0], bad)
    print(f"  tampered stub  → status={r_bad.status}  flags={[f['type'] for f in r_bad.flags]}")
    print(f"    detail: {r_bad.flags[0]['detail']}")
    assert r_ok.status == "ok" and r_bad.status == "mismatch"
    print("  ✓ mismatch blocks the receipt (contract.has_unconfirmed_flags → True)")


def cmd_smoke() -> None:
    print(f"=== WS-A smoke (model={DEFAULT_MODEL}, available={available()}) ===\n")
    for name, fn in (("SESSION", cmd_session), ("DEDUP", cmd_dedup),
                     ("CHECKSUM", cmd_checksum), ("RECON", cmd_recon)):
        print(f"--- {name} ---")
        fn()
        print()


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "smoke"
    if cmd == "session":
        cmd_session(Path(argv[1]) if len(argv) > 1 else None)
    elif cmd == "dedup":
        cmd_dedup()
    elif cmd == "checksum":
        cmd_checksum()
    elif cmd == "recon":
        cmd_recon()
    elif cmd == "smoke":
        cmd_smoke()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
