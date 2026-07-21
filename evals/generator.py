"""Boleto WS-C v2 synthetic data engine — handwritten punch tickets + degradation.

Evolves htr_harness.py's `gen`: instead of one printed font, it renders each ticket
with MIXED handwriting fonts, per-digit stroke jitter (offset + micro-rotation +
size wobble), across 3 handwritten layout FORMATS (whose crop-region boxes are
written to core/extraction/formats/format_1..3.json — the same specs WS-A crops
against, so generator and extractor never disagree). Every image gets an exact
ground-truth JSON row and a corruption vector, collected into one manifest.json.

Everything is seeded off a single Random(seed): no time, no unseeded randomness —
regenerate the identical set anywhere. `gen_dataset(n)` makes thousands on demand.

The degradation suite is severity-parameterized (0.0 clean .. 1.0 worst): smudge,
sun glare, skew, shadow, crumple-warp, motion blur, low-light noise, pencil fade.
One corruption per image so the eval can plot accuracy per (type × severity) —
the robustness curve. Baseline severity 0.0 = clean handwriting.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

DIR = Path(__file__).parent
OUT = DIR / "tickets_synthetic"
FORMATS_DIR = DIR.parent / "core" / "extraction" / "formats"

# Handwriting fonts present on macOS. Filtered to those that actually load so the
# generator degrades gracefully on a machine missing a face.
_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    "/System/Library/Fonts/Supplemental/Chalkduster.ttf",
    "/System/Library/Fonts/Supplemental/Trattatello.ttf",
    "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
    "/System/Library/Fonts/Noteworthy.ttc",
    "/System/Library/Fonts/MarkerFelt.ttc",
    "/System/Library/Fonts/ChalkboardSE.ttc",
]

# kn: diversified 2026-07-19 after LoRA v1/v2 lost to base — homogeneous data taught
# memorization, not reading. Arbitrary cent-rates + varied ids force real digit OCR.
CROPS = ["strawberry", "raspberry", "blackberry", "blueberry", "cherry", "grape",
         "tomato", "lettuce", "broccoli", "celery", "fresa", "mora", "uva", "berry"]
RATES = [round(1.10 + 0.05 * i, 2) for i in range(58)]  # 1.10 .. 3.95 in 5¢ steps

CORRUPTIONS = ["smudge", "glare", "skew", "shadow", "warp", "motion", "lowlight", "pencilfade"]
SEVERITIES = [0.0, 0.25, 0.5, 0.75, 1.0]


def _fonts(sizes: tuple[int, ...] = (20, 22, 24)) -> list[ImageFont.FreeTypeFont]:
    """Every available handwriting face at each size; ground for per-glyph font swaps."""
    out = []
    for path in _FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        for s in sizes:
            try:
                out.append(ImageFont.truetype(path, s))
            except OSError:
                pass
    if not out:  # last-resort so the harness still runs headless
        out = [ImageFont.load_default()]
    return out


_FONTS = _fonts()


# ── layout formats (data → written to formats/format_N.json) ──────────────────
# Each layout: image size + named field boxes [x0,y0,x1,y1]. rows are templated by
# a start_y + index*stride_y stride (matching format_0's convention) so WS-A crops
# rows[i] the same way it crops the printed set.
LAYOUTS = {
    "format_1": {  # single-column, roomy hand ledger
        "image_size": [560, 420],
        "header": {"box": [16, 14, 544, 46], "contains": ["worker_id", "date"]},
        "rows": {"start_y": 52, "stride_y": 30, "max_rows": 4,
                 "cell_boxes": {"crop": [24, 0, 200, 28],
                                "units": [200, 0, 360, 28],
                                "rate": [360, 0, 520, 28]}},
        "productive_hours": {"box": [24, 180, 420, 208]},
        "nonproductive_hours": {"box": [24, 208, 420, 236]},
        "rest_hours": {"box": [24, 236, 420, 264]},
    },
    "format_2": {  # tighter grid, labels on right
        "image_size": [600, 400],
        "header": {"box": [18, 12, 582, 42], "contains": ["worker_id", "date"]},
        "rows": {"start_y": 48, "stride_y": 26, "max_rows": 4,
                 "cell_boxes": {"crop": [20, 0, 190, 24],
                                "units": [190, 0, 330, 24],
                                "rate": [330, 0, 470, 24]}},
        "productive_hours": {"box": [20, 160, 460, 184]},
        "nonproductive_hours": {"box": [20, 184, 460, 208]},
        "rest_hours": {"box": [20, 208, 460, 232]},
    },
    "format_3": {  # wide, larger hand, 3 rows
        "image_size": [640, 380],
        "header": {"box": [20, 16, 620, 50], "contains": ["worker_id", "date"]},
        "rows": {"start_y": 58, "stride_y": 34, "max_rows": 3,
                 "cell_boxes": {"crop": [28, 0, 230, 32],
                                "units": [230, 0, 410, 32],
                                "rate": [410, 0, 600, 32]}},
        "productive_hours": {"box": [28, 176, 500, 206]},
        "nonproductive_hours": {"box": [28, 206, 500, 236]},
        "rest_hours": {"box": [28, 236, 500, 266]},
    },
}


def write_format_specs() -> None:
    """Persist each layout as formats/format_N.json (shared crop spec with WS-A).
    format_0.json is FROZEN and never touched here."""
    FORMATS_DIR.mkdir(parents=True, exist_ok=True)
    for fid, lay in LAYOUTS.items():
        spec = {
            "format_id": fid,
            "description": f"WS-C handwritten layout {fid}. Boxes [x0,y0,x1,y1] px. "
                           "rows[*] boxes are relative to start_y + index*stride_y "
                           "(add that offset to cell_box y0/y1), matching format_0.",
            "image_size": lay["image_size"],
            "header": lay["header"],
            "rows": lay["rows"],
            "productive_hours": lay["productive_hours"],
            "nonproductive_hours": lay["nonproductive_hours"],
            "rest_hours": lay["rest_hours"],
            "kn_note": "kn: generated by evals/generator.write_format_specs from LAYOUTS; "
                       "regenerate if the generator layout changes — do not hand-edit.",
        }
        (FORMATS_DIR / f"{fid}.json").write_text(json.dumps(spec, indent=2))


# ── handwriting rendering ─────────────────────────────────────────────────────
def _draw_hand(img: Image.Image, xy: tuple[int, int], text: str,
               rng: random.Random, mix: list[ImageFont.FreeTypeFont]) -> None:
    """Draw text char-by-char with per-glyph jitter: horizontal drift, vertical
    wobble, micro-rotation, occasional font swap. This is the stroke-jitter that a
    single ImageDraw.text call cannot produce — it makes two '3's look different."""
    x, y0 = xy
    for ch in text:
        font = rng.choice(mix) if rng.random() < 0.35 else mix[0]
        # render glyph on its own transparent tile so it can rotate independently
        try:
            bbox = font.getbbox(ch)
        except Exception:
            bbox = (0, 0, 12, 20)
        w = max(1, bbox[2] - bbox[0])
        h = max(1, bbox[3] - bbox[1])
        tile = Image.new("RGBA", (w + 8, h + 8), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        ink = rng.randint(0, 60)  # near-black pencil/pen, slight variation
        td.text((4 - bbox[0], 4 - bbox[1]), ch, fill=(ink, ink, ink, 255), font=font)
        if ch.strip():
            tile = tile.rotate(rng.uniform(-7, 7), expand=True, resample=Image.BICUBIC)
        dy = rng.randint(-2, 2)
        img.paste(tile, (int(x), int(y0 + dy)), tile)
        x += w + rng.randint(-1, 3)  # variable kerning; can touch/overlap like real hand


def _rand_record(i: int, rng: random.Random, max_rows: int) -> dict:
    n_rows = rng.randint(1, max_rows)
    rows = [{"crop": rng.choice(CROPS),
             "units": rng.randint(5, 999),          # 1–3 digit counts, no safe range
             "rate": rng.choice(RATES)} for _ in range(n_rows)]
    # varied id shapes: W123 / 4821 / AB-1234 / E-77 — the model must READ, not guess
    styles = [f"W{rng.randint(10, 9999)}",
              str(rng.randint(1000, 99999)),
              f"{chr(rng.randint(65, 90))}{chr(rng.randint(65, 90))}-{rng.randint(100, 9999)}",
              f"{chr(rng.randint(65, 90))}-{rng.randint(10, 999)}"]
    return {
        "worker_id": rng.choice(styles),
        "date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        "rows": rows,
        "productive_hours": rng.choice([4, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5, 10, 11]),
        "nonproductive_hours": rng.choice([0, 0, 0, 0.25, 0.5, 0.75, 1, 1.5]),
        "rest_hours": rng.choice([0, 0.25, 0.33, 0.5, 0.66]),
    }


def render_ticket(rec: dict, fid: str, rng: random.Random) -> Image.Image:
    """Render one handwritten ticket for layout `fid` from ground-truth `rec`."""
    lay = LAYOUTS[fid]
    W, H = lay["image_size"]
    # faint off-white paper with slight tint so glare/shadow read naturally
    tint = rng.randint(244, 253)
    img = Image.new("RGB", (W, H), (tint, tint, tint - rng.randint(0, 4)))
    mix = rng.sample(_FONTS, k=min(3, len(_FONTS)))  # this ticket's hand(s)

    hx0, hy0 = lay["header"]["box"][0] + 4, lay["header"]["box"][1] + 4
    _draw_hand(img, (hx0, hy0), f"{rec['worker_id']}  {rec['date']}", rng, mix)

    r = lay["rows"]
    for idx, row in enumerate(rec["rows"][: r["max_rows"]]):
        ry = r["start_y"] + idx * r["stride_y"]
        cb = r["cell_boxes"]
        _draw_hand(img, (cb["crop"][0] + 4, ry + 2), row["crop"], rng, mix)
        _draw_hand(img, (cb["units"][0] + 4, ry + 2), str(row["units"]), rng, mix)
        _draw_hand(img, (cb["rate"][0] + 4, ry + 2), f"${row['rate']:.2f}", rng, mix)

    for f in ("productive_hours", "nonproductive_hours", "rest_hours"):
        bx = lay[f]["box"]
        label = {"productive_hours": "prod", "nonproductive_hours": "nonprod",
                 "rest_hours": "rest"}[f]
        _draw_hand(img, (bx[0] + 4, bx[1] + 2), f"{label} {rec[f]}", rng, mix)
    return img


# ── degradation suite (severity 0..1) ─────────────────────────────────────────
def degrade(img: Image.Image, kind: str, sev: float, rng: random.Random) -> Image.Image:
    """Apply one corruption at severity `sev`. sev=0 returns the image unchanged."""
    if sev <= 0:
        return img
    W, H = img.size
    if kind == "smudge":
        out = img.copy()
        d = ImageDraw.Draw(out, "RGBA")
        for _ in range(int(1 + sev * 6)):
            cx, cy = rng.randint(0, W), rng.randint(0, H)
            rr = int(20 + sev * 60)
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                      fill=(90, 90, 90, int(60 + sev * 120)))
        return out.filter(ImageFilter.GaussianBlur(sev * 1.5))
    if kind == "glare":
        arr = np.asarray(img).astype(np.float32)
        yy, xx = np.mgrid[0:H, 0:W]
        cx, cy = rng.randint(0, W), rng.randint(0, H)
        d2 = ((xx - cx) ** 2 + (yy - cy) ** 2) / (max(W, H) ** 2)
        glow = np.clip(1.0 - d2 * 4, 0, 1)[..., None] * (200 * sev)
        return Image.fromarray(np.clip(arr + glow, 0, 255).astype(np.uint8))
    if kind == "skew":
        ang = sev * rng.uniform(6, 16) * rng.choice([-1, 1])
        shear = sev * 0.25 * rng.choice([-1, 1])
        out = img.rotate(ang, resample=Image.BICUBIC, fillcolor=(250, 250, 250), expand=False)
        return out.transform((W, H), Image.AFFINE, (1, shear, 0, 0, 1, 0),
                             resample=Image.BICUBIC, fillcolor=(250, 250, 250))
    if kind == "shadow":
        arr = np.asarray(img).astype(np.float32)
        grad = np.linspace(1.0, 1.0 - 0.7 * sev, W)[None, :, None]
        if rng.random() < 0.5:
            grad = grad[:, ::-1, :]
        return Image.fromarray(np.clip(arr * grad, 0, 255).astype(np.uint8))
    if kind == "warp":
        arr = np.asarray(img).astype(np.float32)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        amp = sev * 8.0
        xs = xx + amp * np.sin(2 * math.pi * yy / (40 + rng.randint(0, 30)))
        ys = yy + amp * np.cos(2 * math.pi * xx / (50 + rng.randint(0, 30)))
        xs = np.clip(xs, 0, W - 1).astype(np.int32)
        ys = np.clip(ys, 0, H - 1).astype(np.int32)
        return Image.fromarray(arr[ys, xs].astype(np.uint8))
    if kind == "motion":
        # horizontal streak via a length-k box average along x (PIL Kernel caps at 5x5)
        k = max(3, int(3 + sev * 12)) | 1  # odd
        arr = np.asarray(img).astype(np.float32)
        acc = np.zeros_like(arr)
        for off in range(-(k // 2), k // 2 + 1):
            acc += np.roll(arr, off, axis=1)
        return Image.fromarray(np.clip(acc / k, 0, 255).astype(np.uint8))
    if kind == "lowlight":
        arr = np.asarray(img).astype(np.float32) * (1.0 - 0.55 * sev)
        noise = rng.random() and np.random.default_rng(rng.randint(0, 2**31)).normal(
            0, 25 * sev, arr.shape)
        return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))
    if kind == "pencilfade":
        arr = np.asarray(img).astype(np.float32)
        faded = 255 - (255 - arr) * (1.0 - 0.7 * sev)  # pull ink toward paper white
        return Image.fromarray(np.clip(faded, 0, 255).astype(np.uint8))
    return img


# ── dataset assembly ──────────────────────────────────────────────────────────
def gen_dataset(n: int, seed: int = 42, spread: bool = True) -> list[dict]:
    """Generate n handwritten tickets across formats × corruptions × severities.
    Writes images + manifest.json (inline ground truth + corruption vector) and the
    format specs. Returns the manifest list. Deterministic in (n, seed)."""
    OUT.mkdir(parents=True, exist_ok=True)
    write_format_specs()
    rng = random.Random(seed)
    fids = list(LAYOUTS.keys())
    manifest = []
    for i in range(n):
        trng = random.Random(seed * 100003 + i)  # per-ticket stream, still seed-derived
        fid = fids[i % len(fids)]
        rec = _rand_record(i, trng, LAYOUTS[fid]["rows"]["max_rows"])
        if spread:  # sweep the whole (type × severity) grid for a clean curve
            kind = CORRUPTIONS[i % len(CORRUPTIONS)]
            sev = SEVERITIES[(i // len(CORRUPTIONS)) % len(SEVERITIES)]
        else:
            kind, sev = trng.choice(CORRUPTIONS), trng.choice(SEVERITIES)
        base = render_ticket(rec, fid, trng)
        out = degrade(base, kind, sev, trng)
        name = f"t{i:05d}.png"
        out.save(OUT / name)
        manifest.append({"image": name, "format": fid, "truth": rec,
                         "corruption": {"type": kind if sev > 0 else "clean",
                                        "severity": sev}})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def gallery(manifest: list[dict], cols: int = 5, cell: tuple[int, int] = (260, 200),
            out_path: Path | None = None, k: int = 20) -> Path:
    """Grid PNG of the first k tickets (thumbnails) for the RESULTS gallery."""
    out_path = out_path or (DIR / "results" / "generator_gallery.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sel = manifest[:k]
    rows = math.ceil(len(sel) / cols)
    cw, ch = cell
    canvas = Image.new("RGB", (cols * cw, rows * ch), (30, 30, 30))
    d = ImageDraw.Draw(canvas)
    f = ImageFont.load_default()
    for idx, m in enumerate(sel):
        thumb = Image.open(OUT / m["image"]).convert("RGB")
        thumb.thumbnail((cw - 12, ch - 30))
        r, c = divmod(idx, cols)
        canvas.paste(thumb, (c * cw + 6, r * ch + 6))
        tag = f"{m['corruption']['type']} {m['corruption']['severity']}"
        d.text((c * cw + 6, r * ch + ch - 20), tag, fill=(230, 230, 230), font=f)
    canvas.save(out_path)
    return out_path


def demo() -> None:
    """Self-check: determinism + degradation monotonicity (worse sev = fewer ink px)."""
    m1 = gen_dataset(6, seed=7)
    b1 = (OUT / "t00000.png").read_bytes()
    m2 = gen_dataset(6, seed=7)
    assert (OUT / "t00000.png").read_bytes() == b1, "generator not deterministic"
    assert m1 == m2 and len(m1) == 6
    # pencilfade at rising severity must lighten the ink monotonically
    rng = random.Random(1)
    rec = _rand_record(0, rng, 3)
    base = render_ticket(rec, "format_1", random.Random(2))
    ink = [int((255 - np.asarray(degrade(base, "pencilfade", s, random.Random(3)))
                .astype(np.float32)).sum()) for s in (0.0, 0.5, 1.0)]
    assert ink[0] > ink[1] > ink[2], f"pencilfade not monotonic: {ink}"
    assert (FORMATS_DIR / "format_1.json").exists()
    print(f"generator.py: deterministic ✓  formats written ✓  pencilfade ink {ink} ✓")


if __name__ == "__main__":
    demo()
