"""E4B ensemble-config sweep — which (K, temperature) is best for the demo backend?

Measures the SHIPPING path (extraction.extract_ticket, whole-ticket mode) over the
8-ticket printed benchmark for each config. adaptive=False so K is honored exactly
(adaptive caps at 3 reads and would silently shrink K=4). Appends a dated JSON +
markdown table to evals/results/ — evals before opinions, committed forever.

Run:  .venv-gemma/bin/python -m evals.config_sweep            # default config list, printed set
      .venv-gemma/bin/python -m evals.config_sweep 2,0.2 3,0.5  # custom configs
      .venv-gemma/bin/python -m evals.config_sweep --handwritten 24 2,0.0 2,0.2 3,0.2
        # sweep on N generator handwritten tickets (mixed hands + degradations)
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extraction import extract_ticket  # noqa: E402

RESULTS = ROOT / "evals" / "results"
TICKETS = ROOT / "tickets"
import os  # noqa: E402
MODEL = os.environ.get("SWEEP_MODEL", "mlx")  # "mlx" = base E4B;
# "mlx:@evals/adapters/boleto-v1" = base + LoRA adapter (before/after comparisons)

DEFAULT_CONFIGS: list[tuple[int, float]] = [
    (2, 0.2),   # current champion (baseline, re-measured for a fair same-run comparison)
    (3, 0.2),
    (4, 0.2),
    (2, 0.1),
    (2, 0.0),
    (4, 0.0),
]


def _pay_tuple(rec: dict) -> tuple:
    rows = tuple((r.get("units"), float(r.get("rate") or 0)) for r in rec.get("rows", []))
    return (rows, rec.get("productive_hours"), rec.get("nonproductive_hours"),
            rec.get("rest_hours"))


def _reassemble(te) -> dict:
    pred: dict = {"rows": []}
    rowmap: dict[int, dict] = {}
    for f in te.fields:
        if f.field.startswith("rows["):
            idx = int(f.field[5:f.field.index("]")])
            rowmap.setdefault(idx, {})[f.field.split(".")[1]] = f.value
        else:
            pred[f.field] = f.value
    pred["rows"] = [rowmap[k] for k in sorted(rowmap)]
    return pred


def run_config(k: int, temperature: float, truth: list[dict],
               images: list[Path] | None = None) -> dict:
    crops = Path(tempfile.mkdtemp())
    n = len(truth)
    pay_hits = pay_total = 0
    critical = escaped = flagged_tickets = pay_flagged_tickets = 0
    calls = 0
    t0 = time.perf_counter()
    for i, t in enumerate(truth):
        img = images[i] if images else TICKETS / f"ticket_{i:02d}.png"
        te = extract_ticket(img, crops, model=MODEL,
                            k=k, temperature=temperature,
                            whole_ticket=True, adaptive=False)
        calls += k
        pred = _reassemble(te)
        # per-value exact match on every pay-bearing cell
        for key in ("productive_hours", "nonproductive_hours", "rest_hours"):
            pay_total += 1
            pay_hits += int(float(pred.get(key) or 0) == float(t.get(key) or 0))
        for ri, row in enumerate(t["rows"]):
            for cell in ("units", "rate"):
                pay_total += 1
                got = (pred["rows"][ri].get(cell) if ri < len(pred["rows"]) else None)
                pay_hits += int(float(got or 0) == float(row.get(cell) or 0))
        crit = _pay_tuple(pred) != _pay_tuple(t)
        critical += int(crit)
        tflags = [f.field for f in te.fields if f.flagged]
        pay_flags = [f for f in tflags
                     if ("rows[" in f and (f.endswith(".units") or f.endswith(".rate")))
                     or f.endswith("_hours")]
        flagged_tickets += bool(tflags)
        pay_flagged_tickets += bool(pay_flags)
        if crit and not pay_flags:
            escaped += 1
    dt = time.perf_counter() - t0
    return {
        "k": k, "temperature": temperature, "n_tickets": n,
        "pay_value_exact": round(pay_hits / pay_total, 4),
        "pay_values": f"{pay_hits}/{pay_total}",
        "conclusion_critical_rate": round(critical / n, 3),
        "escaped_rate": round(escaped / n, 3),
        "any_flag_ticket_rate": round(flagged_tickets / n, 3),
        "pay_flag_ticket_rate": round(pay_flagged_tickets / n, 3),
        "model_calls": calls,
        "s_per_ticket": round(dt / n, 2),
        "wall_s": round(dt, 1),
    }


def main() -> None:
    args = sys.argv[1:]
    images = None
    bench_name = "8 printed tickets (tickets/)"
    if args and args[0] == "--handwritten":
        n_hand = int(args[1])
        args = args[2:]
        from evals.generator import gen_dataset
        manifest = gen_dataset(n_hand, seed=11)   # fresh deterministic handwritten batch
        syn = ROOT / "evals" / "tickets_synthetic"
        images = [syn / m["image"] for m in manifest]
        truth = [m["truth"] for m in manifest]
        bench_name = f"{n_hand} generator handwritten tickets (mixed hands + degradations, seed=11)"
    else:
        truth = json.loads((TICKETS / "truth.json").read_text())
    configs = ([(int(a.split(",")[0]), float(a.split(",")[1])) for a in args]
               if args else DEFAULT_CONFIGS)
    rows = []
    for k, temp in configs:
        print(f"running K={k} T={temp} on {bench_name} ...", flush=True)
        r = run_config(k, temp, truth, images)
        rows.append(r)
        print("  ", r, flush=True)

    stamp = date.today().isoformat()
    tag = "hand" if images else "printed"
    if "@" in MODEL:
        tag += "_adapter"
    out_json = RESULTS / f"config_sweep_{stamp}_e4b_{tag}.json"
    out_json.write_text(json.dumps({"model": MODEL,
                                    "benchmark": bench_name,
                                    "adaptive": False, "rows": rows}, indent=2))
    md = [f"# E4B config sweep — {stamp} ({bench_name}, shipping path)",
          "", "| K | T | pay-exact | critical | escaped | pay-flag rate | any-flag rate | calls | s/ticket |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['k']} | {r['temperature']} | {r['pay_values']} ({r['pay_value_exact']:.1%}) "
                  f"| {r['conclusion_critical_rate']} | {r['escaped_rate']} "
                  f"| {r['pay_flag_ticket_rate']} | {r['any_flag_ticket_rate']} "
                  f"| {r['model_calls']} | {r['s_per_ticket']} |")
    md.append("")
    md.append("Selection rule: lowest escaped-rate first, then lowest pay-flag rate "
              "(automation), then fewest calls (latency/energy). kn: synthetic benchmark — "
              "re-run on the real consented/handwritten set before trusting it there.")
    out_md = RESULTS / f"config_sweep_{stamp}_e4b_{tag}.md"
    out_md.write_text("\n".join(md))
    print(f"\nwrote {out_json.name} + {out_md.name}")
    print("\n".join(md))


if __name__ == "__main__":
    main()
