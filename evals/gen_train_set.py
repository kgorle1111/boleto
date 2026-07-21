"""Generate the LoRA fine-tuning dataset — SEPARATE from the eval set.

Discipline: training data must never overlap eval data. The eval sets use seeds
42/7/11 in evals/tickets_synthetic/; this writes to evals/tickets_train/ with
seed=2026 and a random (not grid-swept) corruption mix — training wants variety,
the eval wants a clean severity curve.

Outputs:
  evals/tickets_train/t*.png           the images
  evals/tickets_train/manifest.json    ground truth + corruption vector per image
  evals/tickets_train/train.jsonl      {image, prompt, completion} — 90% split
  evals/tickets_train/val.jsonl        10% held-out for during-training validation

Run:  .venv-gemma/bin/python -m evals.gen_train_set 2500
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import evals.generator as g  # noqa: E402

TRAIN_DIR = ROOT / "evals" / "tickets_train"
SEED = 2026  # NOT 42/7/11 — those are eval seeds; train/eval must never overlap

PROMPT = (ROOT / "evals" / "prompts" / "ticket_whole.txt").read_text().split("---", 1)[1].strip()


def main(n: int = 2500) -> None:
    g.OUT = TRAIN_DIR  # kn: module-level redirect; generator writes wherever OUT points
    manifest = g.gen_dataset(n, seed=SEED, spread=False)

    split = int(n * 0.9)
    # mlx_vlm.lora native format: {"image": path, "messages": [{role, content}...]}
    # (probe-verified 2026-07-19; the earlier prompt/completion shape KeyErrors)
    for name, chunk in (("train.jsonl", manifest[:split]), ("val.jsonl", manifest[split:])):
        with open(TRAIN_DIR / name, "w") as f:
            for m in chunk:
                f.write(json.dumps({
                    "image": str(TRAIN_DIR / m["image"]),
                    "messages": [
                        {"role": "user", "content": PROMPT},
                        {"role": "assistant",
                         "content": json.dumps(m["truth"], separators=(",", ":"))},
                    ],
                }) + "\n")
    print(f"{n} tickets → {TRAIN_DIR}")
    print(f"train.jsonl: {split} examples | val.jsonl: {n - split} examples")
    print("corruption mix is random (spread=False); eval sets remain untouched")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2500)
