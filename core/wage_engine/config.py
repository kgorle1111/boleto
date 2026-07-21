"""Minimum-wage config: versioned, effective-dated, date-selected (WS-B item 6).

The engine must NOT hardcode a wage constant — the applicable minimum wage depends
on the pay-period date (and, later, locality). This selects the state figure whose
effective date is the latest one on/before the pay period, and takes the higher of
state vs local when a locality ordinance is present (CA requires the higher).

kn: values live in minimum_wage.json and are Kannishk's to verify (§10.6 item 5).
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

_CONFIG_PATH = pathlib.Path(__file__).with_name("minimum_wage.json")


def load_config(path: str | pathlib.Path | None = None) -> dict:
    p = pathlib.Path(path) if path else _CONFIG_PATH
    return json.loads(p.read_text())


def _latest_on_or_before(schedule: list[dict], period_date: str) -> Decimal:
    """schedule: [{effective: YYYY-MM-DD, amount: '16.00'}, ...]. ISO dates sort as strings."""
    eligible = [row for row in schedule if row["effective"] <= period_date]
    if not eligible:
        raise ValueError(
            f"no minimum wage on file for pay period {period_date}; "
            f"earliest configured is {min(r['effective'] for r in schedule)}. "
            "kn: extend minimum_wage.json backward if auditing older periods."
        )
    row = max(eligible, key=lambda r: r["effective"])
    return Decimal(row["amount"])


def minimum_wage_for(
    period_date: str, locality: str | None = None, config: dict | None = None
) -> Decimal:
    """Applicable minimum wage for a pay period (YYYY-MM-DD). Higher of state vs local."""
    cfg = config or load_config()
    state = _latest_on_or_before(cfg["state_minimum_wage"], period_date)
    if locality:
        local_sched = cfg.get("local_ordinances", {}).get(locality)
        if local_sched:
            return max(state, _latest_on_or_before(local_sched, period_date))
    return state


def demo() -> None:
    assert minimum_wage_for("2024-06-01") == Decimal("16.00")
    assert minimum_wage_for("2025-01-01") == Decimal("16.50")
    assert minimum_wage_for("2023-12-31") == Decimal("15.50")
    try:
        minimum_wage_for("2020-01-01")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for pre-schedule date")
    print("config.py: minimum_wage_for self-checks passed")


if __name__ == "__main__":
    demo()
