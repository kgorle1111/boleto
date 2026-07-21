"""Boleto WS-A extraction package — photo(s) → SessionExtraction (§10.2 contract).

Public API (import from here, not from submodules):
    extract_session, extract_ticket, extract_stub, reconcile,
    quality_gate, dedup_groups, suggest_single_digit_repair,
    ticket_values, ticket_piece_total, flag_rate
"""
from extraction.pipeline import (  # noqa: F401
    dedup_groups,
    extract_session,
    extract_stub,
    extract_ticket,
    flag_rate,
    quality_gate,
    reconcile,
    suggest_single_digit_repair,
    ticket_piece_total,
    ticket_values,
)

__all__ = [
    "extract_session", "extract_ticket", "extract_stub", "reconcile",
    "quality_gate", "dedup_groups", "suggest_single_digit_repair",
    "ticket_values", "ticket_piece_total", "flag_rate",
]
