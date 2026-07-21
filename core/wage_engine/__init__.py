"""Boleto wage engine + WS-B legal/history layer.

The arithmetic core is the repo-root wage_engine.py (canonical, ported per platform).
This package adds the CA-labor-law layer on top of it: audit_session (the sole
receipt entry point), minimum-wage config, and hash-chained local history.
"""
from core.wage_engine.config import minimum_wage_for
from core.wage_engine.history import ReceiptHistory
from core.wage_engine.session_audit import audit_session, build_session
from core.wage_engine.wage_engine import Day, Workweek, audit, hrs, reconstruct, usd

__all__ = [
    "audit_session",
    "build_session",
    "minimum_wage_for",
    "ReceiptHistory",
    "Day",
    "Workweek",
    "reconstruct",
    "audit",
    "usd",
    "hrs",
]
