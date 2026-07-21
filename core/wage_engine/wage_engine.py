"""Re-export shim — the repo-root wage_engine.py is the canonical reference engine.

WS-B extends the engine via the legal layer in this package, but the arithmetic
core stays in ONE place (root) so the Kotlin/Rust ports (P1) have a single source
of truth to match against golden_cases.json. Importing from here or from the root
both yield the same Day/Workweek/reconstruct/audit/usd/hrs.

kn: this shim only bootstraps the root file onto sys.path; delete it if the engine
is ever properly packaged.
"""
from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from wage_engine import (  # noqa: E402  (path bootstrap must precede import)
    Day,
    Workweek,
    audit,
    hrs,
    reconstruct,
    usd,
)

__all__ = ["Day", "Workweek", "audit", "hrs", "reconstruct", "usd"]
