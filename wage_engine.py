"""Boleto wage-law engine — CA piece-rate pay reconstruction (smoke test).

Computes what a piece-rate farmworker is OWED for a workweek under CA Labor Code
226.2 (separate rest-break pay + nonproductive time) plus daily/weekly overtime
and a minimum-wage make-up floor. Compares against what was PAID to surface a
discrepancy. This is the deterministic half of Boleto: the LLM only extracts the
numbers from tickets/stubs; ALL arithmetic that carries a legal conclusion lives
here, where it is unit-tested and auditable.

Scope ceiling (deferred edges, add when a real stub exercises them):
  # ponytail: no 7th-consecutive-day rule; no >12h double-time; no meal-period
  # premium; rest pay not folded into the OT regular rate (DLSE nuance). These
  # change owed pay in rarer stubs — encode when a labeled case needs them.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

# Money as Decimal cents — never float for legal arithmetic.
def usd(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def hrs(x) -> Decimal:
    return Decimal(str(x))


@dataclass
class Day:
    piece_earnings: Decimal      # units * rate, already multiplied
    productive_hours: Decimal    # hours doing piece work
    nonproductive_hours: Decimal = field(default_factory=lambda: hrs(0))  # employer-controlled non-piece time
    rest_hours: Decimal = field(default_factory=lambda: hrs(0))           # paid rest/recovery periods

    @property
    def total_hours(self) -> Decimal:
        # Rest periods are hours worked for OT thresholds; productive+nonproductive+rest.
        return self.productive_hours + self.nonproductive_hours + self.rest_hours


@dataclass
class Workweek:
    days: list[Day]
    minimum_wage: Decimal        # applicable CA min wage (config; verify per year/locality)


def _daily_ot_hours(total: Decimal) -> Decimal:
    """OT hours in a single day: >8 and <=12 count. (>12 double-time deferred.)"""
    return max(hrs(0), min(total, hrs(12)) - hrs(8))


def reconstruct(week: Workweek) -> dict:
    mw = week.minimum_wage
    piece = sum((d.piece_earnings for d in week.days), Decimal(0))
    prod_h = sum((d.productive_hours for d in week.days), Decimal(0))
    nonprod_h = sum((d.nonproductive_hours for d in week.days), Decimal(0))
    rest_h = sum((d.rest_hours for d in week.days), Decimal(0))
    total_h = sum((d.total_hours for d in week.days), Decimal(0))

    # 1. Minimum-wage make-up on productive time: piece earnings must average >= MW
    #    across productive hours (CA: every hour at least min wage).
    prod_floor = prod_h * mw
    mw_makeup = max(Decimal(0), prod_floor - piece)
    productive_comp = piece + mw_makeup  # straight-time comp for piece work

    # 2. Nonproductive time: >= min wage (LC 226.2).
    nonproductive_pay = nonprod_h * mw

    # 3. Rest/recovery pay (LC 226.2): higher of MW or the weekly average hourly
    #    rate = (productive_comp + nonproductive_pay) / (productive + nonproductive hours),
    #    excluding rest hours from both numerator and denominator.
    worked_ex_rest = prod_h + nonprod_h
    avg_rate = (productive_comp + nonproductive_pay) / worked_ex_rest if worked_ex_rest else Decimal(0)
    rest_rate = max(avg_rate, mw)
    rest_pay = rest_h * rest_rate

    # 4. Overtime premium. Regular rate = straight-time comp / total hours worked.
    #    Piece rate already pays straight time for all hours, so premium is 0.5x
    #    for OT hours (half-time method). Daily OT (>8) and weekly OT (>40), no
    #    double-count: weekly OT applies only to non-daily-OT hours over 40.
    straight_comp = productive_comp + nonproductive_pay + rest_pay
    regular_rate = straight_comp / total_h if total_h else Decimal(0)

    daily_ot = sum((_daily_ot_hours(d.total_hours) for d in week.days), Decimal(0))
    weekly_ot = max(Decimal(0), total_h - hrs(40))
    ot_hours = max(daily_ot, weekly_ot)  # greater-of avoids double counting
    ot_premium = usd(Decimal("0.5") * regular_rate * ot_hours)

    owed = usd(straight_comp + ot_premium)
    return {
        "piece_earnings": usd(piece),
        "mw_makeup": usd(mw_makeup),
        "nonproductive_pay": usd(nonproductive_pay),
        "rest_pay": usd(rest_pay),
        "rest_rate": usd(rest_rate),
        "regular_rate": usd(regular_rate),
        "ot_hours": ot_hours,
        "ot_premium": ot_premium,
        "total_owed": owed,
    }


def audit(week: Workweek, amount_paid) -> dict:
    r = reconstruct(week)
    paid = usd(amount_paid)
    r["amount_paid"] = paid
    r["shortfall"] = usd(r["total_owed"] - paid)  # positive = underpaid
    return r


# ---- smoke test: 6 hand-computed known-answer cases ----
def demo():
    MW = usd("16.00")  # clean value for hand-calc; real deploy configures per year/locality

    # 1. Piece only, exactly 8h, rate above MW, no rest/nonprod.
    w = Workweek([Day(usd(200), hrs(8))], MW)
    assert reconstruct(w)["total_owed"] == usd(200), reconstruct(w)

    # 2. Rest-break separate pay (the 226.2 core): 7.5h prod + 0.5h rest, 8h total.
    #    avg = 300/7.5 = 40 -> rest_pay = 0.5*40 = 20. Owed 320. Employer paid 300 -> owed 20.
    w = Workweek([Day(usd(300), hrs(7.5), rest_hours=hrs(0.5))], MW)
    r = audit(w, "300.00")
    assert r["rest_pay"] == usd(20) and r["total_owed"] == usd(320) and r["shortfall"] == usd(20), r

    # 3. Nonproductive time at MW: 6h prod + 2h nonprod. nonprod_pay = 2*16 = 32. Owed 232.
    w = Workweek([Day(usd(200), hrs(6), nonproductive_hours=hrs(2))], MW)
    assert reconstruct(w)["total_owed"] == usd(232), reconstruct(w)

    # 4. Daily OT: 10h day, piece 250. reg = 250/10 = 25. OT 2h * 0.5*25 = 25. Owed 275.
    w = Workweek([Day(usd(250), hrs(10))], MW)
    r = reconstruct(w)
    assert r["ot_hours"] == hrs(2) and r["total_owed"] == usd(275), r

    # 5. Weekly OT, no daily OT: 6 days * 7h = 42h, piece 140/day = 840. reg = 840/42 = 20.
    #    weekly OT 2h * 0.5*20 = 20. Owed 860.
    w = Workweek([Day(usd(140), hrs(7)) for _ in range(6)], MW)
    r = reconstruct(w)
    assert r["ot_hours"] == hrs(2) and r["total_owed"] == usd(860), r

    # 6. MW make-up + rest floor: piece 50 over 3.5h prod (below MW) + 0.5h rest.
    #    prod floor 3.5*16=56 -> makeup 6. productive_comp 56, rate 16. rest 0.5*16=8. Owed 64.
    w = Workweek([Day(usd(50), hrs(3.5), rest_hours=hrs(0.5))], MW)
    r = reconstruct(w)
    assert r["mw_makeup"] == usd(6) and r["rest_pay"] == usd(8) and r["total_owed"] == usd(64), r

    print("all 6 known-answer cases passed")
    # show one full receipt
    w = Workweek([Day(usd(300), hrs(7.5), rest_hours=hrs(0.5))], MW)
    print("sample receipt (case 2):", audit(w, "300.00"))


if __name__ == "__main__":
    demo()
