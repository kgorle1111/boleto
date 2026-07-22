"""Boleto WS-C regression entry point — ONE command → full benchmark.

    BOLETO_MODEL=mock python -m evals.run_eval --source synthetic --n 240
    BOLETO_MODEL=gemma3:latest python -m evals.run_eval --source real

Generates (or loads) the dataset, runs the adaptive ensemble over it, scores every
WS-C metric, builds the calibration + latency + quant tables, then APPENDS a dated
JSON and a markdown block to evals/results/ (append-only history) and prints an
EvalReport (§10.2). The FIRST printed line is the exact command for real numbers.

Model policy (per the workstream brief):
  • mock  → SIMULATED reader; proves the whole analysis pipeline, produces the full
            report offline. Numbers are simulated, NOT a Gemma measurement.
  • real  → gemma3:latest over the 8 printed tickets/ ONLY (GPU shared with a sibling
            agent); the large synthetic set is left for Kannishk's own real run.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from datetime import date
from pathlib import Path

from core.contracts import EvalReport, PAY_FIELDS
from core import model_client
from evals import generator, metrics, pipeline

DIR = Path(__file__).parent
RESULTS = DIR / "results"
# default: the 8 committed printed tickets. Override with BOLETO_REAL_DIR to point
# at a hand-written set (e.g. evals/tickets_real) without disturbing the printed set.
TICKETS_REAL = Path(os.environ.get("BOLETO_REAL_DIR", DIR.parent / "tickets"))

REAL_CMD = "BOLETO_MODEL=gemma3:latest .venv-gemma/bin/python -m evals.run_eval --source synthetic --n 400"


def _peak_mem_gb() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux kilobytes.
    return round(ru / (1024**3 if sys.platform == "darwin" else 1024**2), 3)


def _load_dataset(source: str, n: int, seed: int):
    """Return (records, images, corruption) lists aligned by index."""
    if source == "real":
        truth = json.loads((TICKETS_REAL / "truth.json").read_text())
        imgs = [TICKETS_REAL / f"ticket_{i:02d}.png" for i in range(len(truth))]
        cor = [{"type": "clean", "severity": 0.0} for _ in truth]
        return truth, imgs, cor
    manifest = generator.gen_dataset(n, seed=seed)
    truth = [m["truth"] for m in manifest]
    imgs = [generator.OUT / m["image"] for m in manifest]
    cor = [m["corruption"] for m in manifest]
    return truth, imgs, cor


def _ws_a_read_ticket(image_path: Path, truth: dict, model: str, k: int, adaptive: bool):
    """Drive the SHIPPING WS-A extraction path (extraction.extract_ticket) and adapt its
    TicketExtraction to read_ticket's (pred, flagged, meta) contract, so `--engine ws-a`
    measures the code that ships (plan §10.1 WS-E item 2) — not a parallel reimplementation.
    Real model → whole-ticket mode (WS-A's per-field crops read null on the printed set)."""
    import json as _json
    import tempfile

    from extraction.pipeline import _EnsembleStats, extract_ticket, ticket_values

    crops = Path(tempfile.gettempdir()) / "ws_a_eval_crops"
    stats = _EnsembleStats()
    t0 = time.perf_counter()
    tk = extract_ticket(image_path, crops, model=model, k=k,
                        whole_ticket=(model != "mock"), adaptive=adaptive, stats=stats)
    secs = time.perf_counter() - t0
    vals = ticket_values(tk)
    pred = {f: vals.get(f) for f in pipeline.FIELDS}
    pred["rows"] = vals.get("rows", [])

    flagged: set[str] = set()
    for f in tk.fields:
        if not f.flagged:
            continue
        flagged.add("rows" if f.field.startswith("rows[") else f.field)
    flagged &= set(PAY_FIELDS)

    def _agree(reads: list, value) -> float:
        if not reads:
            return 0.0
        key = _json.dumps(value, sort_keys=True)
        return sum(_json.dumps(r, sort_keys=True) == key for r in reads) / len(reads)

    by = {f.field: f for f in tk.fields}
    agreements: list[tuple[float, bool]] = []
    for fld in ("worker_id", "date", "productive_hours", "nonproductive_hours", "rest_hours"):
        er = by.get(fld)
        if er is None:
            continue
        correct = _json.dumps(pred.get(fld), sort_keys=True) == _json.dumps(truth.get(fld), sort_keys=True)
        agreements.append((round(_agree(er.reads, er.value), 3), correct))
    rows_er = [f for f in tk.fields if f.field.startswith("rows[")]
    if rows_er:
        agr = min(_agree(e.reads, e.value) for e in rows_er)
        correct = _json.dumps(pred.get("rows"), sort_keys=True) == _json.dumps(truth.get("rows"), sort_keys=True)
        agreements.append((round(agr, 3), correct))

    return pred, sorted(flagged), {"reads": stats.calls, "seconds": secs, "agreements": agreements}


def run_benchmark(model: str, source: str, n: int = 240, seed: int = 42,
                  adaptive: bool = True, k: int = 3, engine: str = "sim") -> dict:
    truth, imgs, cor = _load_dataset(source, n, seed)
    preds, flags, agreements = [], [], []
    total_reads = full_reads = 0
    t0 = time.perf_counter()
    for img, t, c in zip(imgs, truth, cor):
        # truth is passed ONLY for post-hoc correctness labels + the sim backend; it is
        # NEVER fed to the real model (both read paths ignore it on the real backend), so
        # the read stays blind while calibration still gets its (agreement, correct) samples.
        if engine == "ws-a":
            pred, fl, meta = _ws_a_read_ticket(Path(img), t, model, k=k, adaptive=adaptive)
        else:
            pred, fl, meta = pipeline.read_ticket(Path(img), t, model, k=k, adaptive=adaptive,
                                                  severity=c.get("severity", 0.0))
        preds.append(pred)
        flags.append(fl)
        agreements.extend(meta["agreements"])
        total_reads += meta["reads"]
        full_reads += k
    wall = time.perf_counter() - t0

    sc = metrics.score(preds, truth, flags, cor)
    cal = metrics.calibration_table(agreements) if agreements else {
        "table": [], "chosen_threshold": "route to review when vote_agreement < 1.0",
        "note": "no ground-truth agreements (real backend runs blind); calibrate on a labeled held-out set"}

    n_t = len(truth)
    latency = {
        "s_per_ticket": round(wall / n_t, 3) if n_t else 0.0,
        "wall_seconds": round(wall, 2),
        "tokens_per_s": None,  # kn: ollama CLI doesn't surface token counts; wire /api/chat eval_count for real tps
        "peak_mem_gb": _peak_mem_gb(),
        "adaptive_reads": total_reads,
        "full_reads_would_be": full_reads,
        "reads_saved_pct": round(100 * (1 - total_reads / full_reads), 1) if full_reads else 0.0,
    }
    return {"score": sc, "calibration": cal, "latency": latency, "n": n_t}


def _tag_present(tag: str) -> bool:
    """EXACT ollama tag presence (model_client.available matches base name only, so it
    can't tell two quants of the same model apart)."""
    import subprocess
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        return any(line.split()[0] == tag for line in out.stdout.splitlines() if line.split())
    except Exception:
        return False


def quant_study(source: str, n: int) -> dict:
    """4-bit vs 8-bit E4B. Runs only if BOTH exact quant tags are installed; else emits
    the table skeleton + the one command that fills it. Never touches the GPU otherwise."""
    tags = {"8bit": "gemma3:latest", "4bit": "gemma3:4b-it-q4_K_M"}
    have = {q: t for q, t in tags.items() if _tag_present(t)}
    if len(have) < 2:
        return {
            "status": "single-quant environment — comparison not run",
            "instruction": ("pull a second quant and rerun, e.g.:\n"
                            "    ollama pull gemma3:4b-it-q4_K_M\n"
                            f"    BOLETO_MODEL={tags['4bit']} .venv-gemma/bin/python -m evals.run_eval --source {source} --n {n}\n"
                            f"    BOLETO_MODEL={tags['8bit']} .venv-gemma/bin/python -m evals.run_eval --source {source} --n {n}\n"
                            "then diff the two dated JSONs in evals/results/ on field_exact_match, s_per_ticket, peak_mem_gb"),
            "table": [{"quant": q, "field_exact_match": None, "s_per_ticket": None,
                       "peak_mem_gb": None} for q in ("8bit", "4bit")],
        }
    rows = []
    for q, tag in have.items():
        r = run_benchmark(tag, source, n)
        rows.append({"quant": q, "model": tag,
                     "field_exact_match": r["score"]["field_exact_match"],
                     "s_per_ticket": r["latency"]["s_per_ticket"],
                     "peak_mem_gb": r["latency"]["peak_mem_gb"]})
    return {"status": "ran", "table": rows}


def to_eval_report(model: str, res: dict) -> EvalReport:
    sc, cal, lat = res["score"], res["calibration"], res["latency"]
    return EvalReport(
        date=date.today().isoformat(), model=model, n=res["n"],
        field_exact_match=sc["field_exact_match"],
        conclusion_critical_rate=sc["conclusion_critical_rate"],
        escaped_rate=sc["escaped_rate"],
        dollar_weighted_escaped=sc["dollar_weighted_escaped"],
        flag_rate=sc["flag_rate"],
        per_severity=sc["per_corruption"],
        calibration=cal.get("table", []),
        latency=lat,
    )


def _append_markdown(report: EvalReport, res: dict, quant: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    md = RESULTS / "eval_log.md"
    sc, cal, lat = res["score"], res["calibration"], res["latency"]
    lines = [
        f"\n## {report.date} — model=`{report.model}` n={report.n}",
        f"- field_exact_match: **{sc['field_exact_match']}**  | conclusion_critical_rate: **{sc['conclusion_critical_rate']}**",
        f"- escaped_rate: **{sc['escaped_rate']}**  | dollar_weighted_escaped: **${sc['dollar_weighted_escaped']}**  | flag_rate: **{sc['flag_rate']}**",
        f"- caught_by_gate: {sc['critical_caught_by_gate']}  escaped_gate: {sc['critical_escaped_gate']}",
        f"- latency: {lat['s_per_ticket']} s/ticket, adaptive_reads={lat['adaptive_reads']} "
        f"(vs {lat['full_reads_would_be']}, saved {lat['reads_saved_pct']}%), peak_mem={lat['peak_mem_gb']} GB",
        f"- calibration threshold: {cal.get('chosen_threshold')}",
        f"- quant study: {quant['status']}",
    ]
    with md.open("a") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["synthetic", "real"], default="synthetic")
    ap.add_argument("--n", type=int, default=240)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-adaptive", action="store_true")
    ap.add_argument("--engine", choices=["sim", "ws-a"], default="sim",
                    help="'ws-a' measures the SHIPPING extraction.extract_ticket path "
                         "(what the app runs); 'sim' is WS-C's parallel ensemble.")
    ap.add_argument("--gallery", action="store_true", help="also write the gallery PNG")
    args = ap.parse_args()

    model = model_client.DEFAULT_MODEL
    # FIRST line: the exact command for the full REAL-model numbers.
    print(f"REAL-MODEL COMMAND (full numbers):\n    {REAL_CMD}\n")
    if model != "mock" and not model_client.available(model):
        print(f"[warn] model '{model}' not available via ollama; results will be empty reads")

    res = run_benchmark(model, args.source, n=args.n, seed=args.seed,
                        adaptive=not args.no_adaptive, engine=args.engine)
    quant = quant_study(args.source, min(args.n, 60))
    report = to_eval_report(model, res)

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = f"{report.date}_{model.replace(':', '-')}_{args.source}_{args.engine}"
    (RESULTS / f"eval_{stamp}.json").write_text(json.dumps({
        "report": report.to_dict(),
        "full_score": res["score"],
        "calibration": res["calibration"],
        "quant_study": quant,
    }, indent=2))
    _append_markdown(report, res, quant)

    if args.gallery and args.source == "synthetic":
        manifest = json.loads((generator.OUT / "manifest.json").read_text())
        g = generator.gallery(manifest)
        print(f"gallery: {g}")

    print(json.dumps(report.to_dict(), indent=2))
    print(f"\nappended → {RESULTS / 'eval_log.md'}  and eval_{stamp}.json")


if __name__ == "__main__":
    main()
