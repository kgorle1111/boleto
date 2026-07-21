"""Boleto WS-C human-baseline MATERIALS (#42) — Kannishk runs the humans (§10.6).

Produces exactly three things and stops:
  1. a sampled degraded ticket set (mid/high severity — where humans and the model
     both struggle, so the comparison is meaningful),
  2. a blank answer sheet (CSV) a human fills in by reading each image,
  3. a scoring script that reads a filled answer sheet and emits the SAME metrics as
     the model eval (evals.metrics.score), so the human column drops straight into
     the final report next to the model column.

This module does NOT run or simulate humans — that's the whole point of a baseline.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from evals import generator, metrics

DIR = Path(__file__).parent
PACK = DIR / "results" / "human_baseline"


def build_materials(n_sample: int = 20, min_severity: float = 0.5, seed: int = 99) -> Path:
    """Sample degraded tickets + write blank answer sheet + a README. Returns the dir."""
    PACK.mkdir(parents=True, exist_ok=True)
    manifest = generator.gen_dataset(max(n_sample * 4, 80), seed=seed)
    degraded = [m for m in manifest if m["corruption"]["severity"] >= min_severity][:n_sample]

    # copy the chosen images into the pack + write a truth sidecar (for scoring only)
    truth_map = {}
    for m in degraded:
        src = generator.OUT / m["image"]
        (PACK / m["image"]).write_bytes(src.read_bytes())
        truth_map[m["image"]] = {"truth": m["truth"], "corruption": m["corruption"]}
    (PACK / "_truth.json").write_text(json.dumps(truth_map, indent=2))

    # blank answer sheet: one row per image, one column per field the human transcribes
    sheet = PACK / "answer_sheet.csv"
    cols = ["image", "worker_id", "date",
            "row1_units", "row1_rate", "row2_units", "row2_rate",
            "row3_units", "row3_rate", "row4_units", "row4_rate",
            "productive_hours", "nonproductive_hours", "rest_hours"]
    with sheet.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for m in degraded:
            w.writerow([m["image"]] + [""] * (len(cols) - 1))

    (PACK / "README.txt").write_text(
        "Boleto human-baseline pack\n"
        f"{len(degraded)} degraded tickets (severity >= {min_severity}).\n\n"
        "1. Give answer_sheet.csv + the PNGs to each reader (NOT _truth.json).\n"
        "2. They transcribe every field they can read; blank = unreadable.\n"
        "3. Score:  .venv-gemma/bin/python -m evals.human_baseline score answer_sheet_filled.csv\n"
        "It emits the same metrics as the model eval, ready to sit beside the model column.\n")
    return PACK


def _row_to_record(row: dict) -> dict:
    rows = []
    for i in (1, 2, 3, 4):
        u, r = row.get(f"row{i}_units", ""), row.get(f"row{i}_rate", "")
        if u == "" and r == "":
            continue
        rows.append({"crop": "", "units": int(u) if u else 0,
                     "rate": float(str(r).lstrip("$")) if r else 0.0})

    def num(k):
        v = row.get(k, "")
        return float(v) if v not in ("", None) else 0

    return {"worker_id": row.get("worker_id", ""), "date": row.get("date", ""),
            "rows": rows, "productive_hours": num("productive_hours"),
            "nonproductive_hours": num("nonproductive_hours"),
            "rest_hours": num("rest_hours")}


def score_filled(filled_csv: str) -> dict:
    """Score a human-filled answer sheet with the model-eval metrics."""
    truth_map = json.loads((PACK / "_truth.json").read_text())
    preds, truth, cor = [], [], []
    with open(filled_csv, newline="") as f:
        for row in csv.DictReader(f):
            img = row["image"]
            if img not in truth_map:
                continue
            preds.append(_row_to_record(row))
            truth.append(truth_map[img]["truth"])
            cor.append(truth_map[img]["corruption"])
    # humans have no ensemble → no self-consistency flags; all critical errors escape.
    return metrics.score(preds, truth, [[] for _ in truth], cor)


def demo() -> None:
    """Self-check: build materials, fabricate a PERFECT fill, confirm scorer runs."""
    pack = build_materials(n_sample=6, min_severity=0.5, seed=99)
    truth_map = json.loads((pack / "_truth.json").read_text())
    # fabricate a perfectly-filled sheet from truth to prove the scoring path
    filled = pack / "answer_sheet_filled.csv"
    with (pack / "answer_sheet.csv").open() as f:
        cols = f.readline().strip().split(",")
    with filled.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for img, meta in truth_map.items():
            t = meta["truth"]
            r = t["rows"]
            def cell(i, key):
                return r[i][key] if i < len(r) else ""
            w.writerow([img, t["worker_id"], t["date"],
                        cell(0, "units"), cell(0, "rate"), cell(1, "units"), cell(1, "rate"),
                        cell(2, "units"), cell(2, "rate"), cell(3, "units"), cell(3, "rate"),
                        t["productive_hours"], t["nonproductive_hours"], t["rest_hours"]])
    sc = score_filled(str(filled))
    assert sc["tickets"] == len(truth_map)
    assert sc["conclusion_critical_rate"] == 0.0, sc  # perfect fill → no critical errors
    print(f"human_baseline.py: materials at {pack.name}/ ✓  scorer on perfect fill: "
          f"crit_rate={sc['conclusion_critical_rate']} ✓")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "score":
        print(json.dumps(score_filled(sys.argv[2]), indent=2))
    else:
        demo()
