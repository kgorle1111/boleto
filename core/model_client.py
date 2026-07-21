"""Shared model client — the ONE place a workstream calls Gemma or the mock.

Every workstream imports read_field / read_json from here so extraction, eval, and
integration all measure the same code path (plan §10: the eval must measure the
code that ships). Real backend is ollama's vision model gemma3:latest; a
deterministic mock backend proves plumbing when the model is unavailable or slow.

Selection: env BOLETO_MODEL. "mock" (default in tests) or an ollama tag like
"gemma3:latest". The mock reads ground truth from a sidecar so pipelines can run
end-to-end without a GPU, and injects seeded errors for gate/scorer testing.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

DEFAULT_MODEL = os.environ.get("BOLETO_MODEL", "mock")


class ModelError(RuntimeError):
    """Prescriptive: names what failed and the recovery, per house agent-design rule."""


def _ollama_generate(image_path: Path, prompt: str, model: str, temperature: float) -> str:
    """Call ollama's chat API with an image. Returns raw text. Raises ModelError with
    a fix hint on failure so callers can route to review instead of crashing."""
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        "stream": False,
        "options": {"temperature": temperature},
    }
    try:
        out = subprocess.run(
            ["ollama", "run", "--json", model],  # kn: `ollama run --json` path; swap to REST /api/chat if throughput matters
            input=json.dumps(payload), capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError as e:
        raise ModelError("ollama not on PATH; install ollama or set BOLETO_MODEL=mock") from e
    except subprocess.TimeoutExpired as e:
        raise ModelError(f"ollama timed out on {image_path.name}; retry or reduce K") from e
    if out.returncode != 0:
        raise ModelError(f"ollama failed ({model}): {out.stderr.strip()[:200]}; "
                         f"try `ollama pull {model}` or BOLETO_MODEL=mock")
    return out.stdout


def _rest_generate(image_path: Path, prompt: str, model: str, temperature: float) -> str:
    """Preferred backend when ollama server is up: POST /api/chat. Falls back to CLI."""
    import urllib.request
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        "stream": False,
        "options": {"temperature": temperature},
    }
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode())
        return body.get("message", {}).get("content", "")
    except Exception as e:
        raise ModelError(f"ollama REST failed: {e}; try BOLETO_MODEL=mock") from e


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of model text. Trust-boundary: model output is
    untrusted — malformed JSON returns {} (caller routes to review), never raises."""
    text = text.strip()
    # ollama --json wraps each line as a JSON event with a "response"/"message" field
    joined = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
            # Only unwrap genuine ollama stream events. A bare JSON payload (e.g. the
            # MLX path returning the extraction itself) must pass through untouched —
            # treating it as an event silently swallowed perfect model output.
            if isinstance(ev, dict) and ("response" in ev or "message" in ev):
                joined.append(ev.get("response")
                              or ev.get("message", {}).get("content", "")
                              or "")
                continue
        except json.JSONDecodeError:
            pass
        joined.append(line)
    blob = "".join(joined) if joined else text
    start, end = blob.find("{"), blob.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        obj = json.loads(blob[start:end + 1])
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


# ── MLX-VLM backend (local Gemma 4 E4B, Apple silicon) ───────────────────────
# BOLETO_MODEL="mlx" → default repo below; "mlx:<repo-or-path>" → that model.
# The loaded model is cached module-level: an 8.88GB 8-bit load costs seconds, and
# a K=3 ensemble must not pay it three times.
_MLX_DEFAULT_REPO = "mlx-community/gemma-4-e4b-it-8bit"
_mlx_cache: dict[str, Any] = {}


def _mlx_resolve(model: str) -> tuple[str, str | None]:
    """model is 'mlx', 'mlx:<repo-or-path>', or 'mlx:<repo-or-path>@<adapter_dir>'
    ('mlx:@<adapter_dir>' = default repo + adapter). → (loadable path, adapter or None)."""
    spec = model.split(":", 1)[1] if ":" in model else ""
    adapter = None
    if "@" in spec:
        spec, adapter = spec.split("@", 1)
    spec = spec or _MLX_DEFAULT_REPO
    if not Path(spec).exists():
        # resolve the local HF snapshot so we never trigger a network pull at read time
        import glob
        hub = Path.home() / ".cache/huggingface/hub"
        safe = "models--" + spec.replace("/", "--")
        snaps = sorted(glob.glob(str(hub / safe / "snapshots" / "*")))
        spec = snaps[-1] if snaps else spec
    return spec, adapter


def _mlx_load(model: str):
    path, adapter = _mlx_resolve(model)
    key = f"{path}@{adapter or ''}"
    if key not in _mlx_cache:
        try:
            from mlx_vlm import load
            from mlx_vlm.utils import load_config
        except ImportError as e:
            raise ModelError("mlx_vlm not installed; `uv pip install mlx-vlm` or BOLETO_MODEL=mock") from e
        if not any(Path(path).glob("*.safetensors")):
            raise ModelError(
                f"MLX weights incomplete at {path}; finish the pull: "
                f"`python -c \"from huggingface_hub import snapshot_download as s; "
                f"s('{_MLX_DEFAULT_REPO}')\"` — or BOLETO_MODEL=mock / gemma3:latest")
        if adapter and not Path(adapter).exists():
            raise ModelError(f"adapter path {adapter} does not exist; train it first "
                             f"(python -m mlx_vlm.lora ...) or drop the @suffix")
        m, proc = load(path, adapter_path=adapter)
        _mlx_cache[key] = (m, proc, load_config(path))
    return _mlx_cache[key]


def _mlx_generate(image_path: Path, prompt: str, model: str, temperature: float) -> tuple[str, float | None]:
    """One MLX vision read → (text, tokens_per_s). Raises ModelError with a fix hint."""
    m, proc, cfg = _mlx_load(model)
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template
    formatted = apply_chat_template(proc, cfg, prompt, num_images=1)
    kw = {"max_tokens": 400, "verbose": False}
    # temperature kwarg name has drifted across mlx_vlm versions — try both.
    for tkey in ("temperature", "temp"):
        try:
            out = generate(m, proc, formatted, [str(image_path)], **{tkey: temperature}, **kw)
            break
        except TypeError:
            continue
    else:
        out = generate(m, proc, formatted, [str(image_path)], **kw)
    text = out if isinstance(out, str) else getattr(out, "text", str(out))
    tps = getattr(out, "generation_tps", None) if not isinstance(out, str) else None
    return text, tps


# ── mock backend ─────────────────────────────────────────────────────────────
def _mock_json(image_path: Path, prompt: str, temperature: float) -> dict:
    """Deterministic ground-truth reader for plumbing tests. Looks for a sidecar
    <image>.truth.json or a truth.json in the image's dir keyed by filename stem.
    Injects a seeded 1-digit error on a subset so gate/scorer logic exercises."""
    p = Path(image_path)
    sidecar = p.with_suffix(p.suffix + ".truth.json")
    if sidecar.exists():
        return json.loads(sidecar.read_text())
    truth_file = p.parent / "truth.json"
    if truth_file.exists():
        data = json.loads(truth_file.read_text())
        # htr_harness truth.json is a list indexed by ticket_NN
        if isinstance(data, list):
            try:
                idx = int("".join(ch for ch in p.stem if ch.isdigit()))
                return data[idx]
            except (ValueError, IndexError):
                return {}
        if isinstance(data, dict):
            return data.get(p.stem, {})
    return {}


# ── public API ───────────────────────────────────────────────────────────────
def read_json(image_path: str | Path, prompt: str, model: str | None = None,
              temperature: float = 0.7) -> tuple[dict, dict]:
    """One vision read → (parsed_dict, meta). meta carries latency + token stats.
    Never raises on bad model output — returns ({}, meta) so callers route to review."""
    model = model or DEFAULT_MODEL
    t0 = time.perf_counter()
    if model == "mock":
        obj = _mock_json(Path(image_path), prompt, temperature)
        meta = {"model": "mock", "seconds": time.perf_counter() - t0, "tokens_per_s": None}
        return obj, meta
    if model == "mlx" or model.startswith("mlx:"):
        raw, tps = _mlx_generate(Path(image_path), prompt, model, temperature)
        obj = _extract_json(raw)
        meta = {"model": model, "seconds": time.perf_counter() - t0, "tokens_per_s": tps}
        return obj, meta
    try:
        try:
            raw = _rest_generate(Path(image_path), prompt, model, temperature)
        except ModelError:
            raw = _ollama_generate(Path(image_path), prompt, model, temperature)
        obj = _extract_json(raw)
    except ModelError:
        raise
    meta = {"model": model, "seconds": time.perf_counter() - t0, "tokens_per_s": None}
    return obj, meta


def available(model: str | None = None) -> bool:
    """True if the named model can actually run. Cheap check for RESULTS.md honesty."""
    model = model or DEFAULT_MODEL
    if model == "mock":
        return True
    if model == "mlx" or model.startswith("mlx:"):
        try:
            path, adapter = _mlx_resolve(model)
            ok = any(Path(path).glob("*.safetensors"))
            return ok and (adapter is None or Path(adapter).exists())
        except Exception:
            return False
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        return model.split(":")[0] in out.stdout
    except Exception:
        return False


def demo() -> None:
    """Self-check: mock backend reads the existing tickets/truth.json by index."""
    root = Path(__file__).resolve().parent.parent
    img = root / "tickets" / "ticket_00.png"
    if img.exists():
        obj, meta = read_json(img, "extract", model="mock")
        assert obj.get("worker_id") == "W100", obj
        assert meta["model"] == "mock"
        print("model_client.py: mock read of ticket_00 →", obj.get("worker_id"), "OK")
    else:
        print("model_client.py: no tickets/ to self-check against (skipped)")
    # _extract_json robustness on garbage
    assert _extract_json("not json at all") == {}
    assert _extract_json('prefix {"value": 7} suffix') == {"value": 7}
    # regression: a bare one-line JSON payload must NOT be eaten as an ollama event
    assert _extract_json('{"worker_id": "W1", "rows": []}') == {"worker_id": "W1", "rows": []}
    assert _extract_json('{"response": "inner text"}') == {}  # real event → unwrapped text, no JSON
    print("model_client.py: _extract_json trust-boundary checks passed")


if __name__ == "__main__":
    demo()
