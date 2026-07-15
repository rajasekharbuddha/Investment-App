"""
compounding_simulator.py
=========================
Two-phase wealth-compounding simulator ("Matching Velocity" -> "Pure
Compounding"), independent of the live/backtest trading engines.

Phase 1 (Matching Velocity):
  The investor contributes a flat monthly amount equal to the *initial*
  annual organic growth of the lump sum (initial_capital * target_roi / 12),
  for `phase1_years`. Contribution is fixed at the start-of-phase value —
  it is not recalculated as the balance grows.

Phase 2 (Pure Compounding):
  Active contributions drop to zero; the portfolio compounds passively for
  `phase2_years`.

Monthly compounding throughout:
    monthly_rate = (1 + annual_rate) ** (1/12) - 1

Optional stress-test mode draws one random annual return per year from
N(target_roi, volatility), modelling sequence-of-returns risk the way a
real (SEBI-regulated) equity mutual fund would actually behave — a bad
year early in the timeline compounds worse than the same bad year late.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

MILESTONES: dict[str, float] = {
    "1 Cr":  1_00_00_000.0,
    "3 Cr":  3_00_00_000.0,
    "6 Cr":  6_00_00_000.0,
    "10 Cr": 10_00_00_000.0,
}

# Bounds on any single simulated year's return in stress-test mode — keeps
# an extreme draw from a fat-tailed normal from going non-physical.
_MIN_ANNUAL_RETURN = -0.60
_MAX_ANNUAL_RETURN = 1.00


def monthly_rate(annual_rate: float) -> float:
    """Convert an annual rate to its equivalent monthly compounding rate."""
    return (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0


def simulate(
    initial_capital: float = 1_00_00_000.0,
    target_roi: float = 0.12,
    phase1_years: int = 6,
    phase2_years: int = 10,
    phase1_monthly_contribution: Optional[float] = None,
    volatility: Optional[float] = None,
    rng: Optional[np.random.Generator] = None,
) -> pd.DataFrame:
    """
    Simulate the two-phase compounding strategy month by month.

    volatility=None -> flat `target_roi` every year (deterministic baseline).
    volatility=0.15  -> each year's annual return is drawn from
                        N(target_roi, volatility), clipped to
                        [_MIN_ANNUAL_RETURN, _MAX_ANNUAL_RETURN].

    Returns a DataFrame with one row per month: Year, Month, GlobalMonth,
    Phase, AnnualRateUsed, StartingBalance, Contribution, GrowthEarned,
    EndingBalance.
    """
    if phase1_monthly_contribution is None:
        phase1_monthly_contribution = initial_capital * target_roi / 12.0

    total_years = phase1_years + phase2_years
    if volatility is not None and rng is None:
        rng = np.random.default_rng()

    rows = []
    balance = initial_capital
    for year in range(1, total_years + 1):
        if volatility is None:
            annual_rate = target_roi
        else:
            assert rng is not None
            annual_rate = float(np.clip(
                rng.normal(target_roi, volatility),
                _MIN_ANNUAL_RETURN, _MAX_ANNUAL_RETURN,
            ))
        r = monthly_rate(annual_rate)
        phase = 1 if year <= phase1_years else 2
        contribution = phase1_monthly_contribution if phase == 1 else 0.0

        for month in range(1, 13):
            start  = balance
            growth = start * r
            end    = start + growth + contribution
            rows.append({
                "Year":            year,
                "Month":           month,
                "GlobalMonth":     (year - 1) * 12 + month,
                "Phase":           phase,
                "AnnualRateUsed":  annual_rate,
                "StartingBalance": start,
                "Contribution":    contribution,
                "GrowthEarned":    growth,
                "EndingBalance":   end,
            })
            balance = end

    return pd.DataFrame(rows)


def rule_of_72_check(target_roi: float) -> dict:
    """
    Compare the Rule-of-72 theoretical doubling time (72 / ROI%) against an
    actual monthly-compounded, contribution-free simulation of the same ROI.
    """
    theoretical_years = 72.0 / (target_roi * 100.0)

    r = monthly_rate(target_roi)
    balance = 1.0
    month = 0
    while balance < 2.0 and month < 1200:
        balance *= (1.0 + r)
        month += 1
    actual_years = month / 12.0

    return {
        "theoretical_years":  theoretical_years,
        "actual_years":       actual_years,
        "difference_years":   actual_years - theoretical_years,
    }


def milestone_crossings(df: pd.DataFrame, milestones: Optional[dict] = None) -> dict:
    """First GlobalMonth (converted to years) at which EndingBalance crosses
    each milestone (defaults to the module-level MILESTONES, in INR). Value
    is None if never reached."""
    milestones = milestones if milestones is not None else MILESTONES
    out = {}
    for label, amount in milestones.items():
        hit = df[df["EndingBalance"] >= amount]
        out[label] = round(hit["GlobalMonth"].iloc[0] / 12.0, 2) if len(hit) else None
    return out


def monte_carlo(
    initial_capital: float = 1_00_00_000.0,
    target_roi: float = 0.12,
    phase1_years: int = 6,
    phase2_years: int = 10,
    phase1_monthly_contribution: Optional[float] = None,
    volatility: float = 0.15,
    n_sims: int = 500,
    goal: float = 10_00_00_000.0,
    seed: Optional[int] = None,
) -> dict:
    """
    Run n_sims randomized simulations (stress-test mode) and report the
    distribution of final-balance outcomes plus the success rate of
    reaching `goal` within the full phase1+phase2 window.
    """
    rng = np.random.default_rng(seed)
    finals = np.empty(n_sims)
    years_to_goal: list[float] = []

    for i in range(n_sims):
        df = simulate(
            initial_capital, target_roi, phase1_years, phase2_years,
            phase1_monthly_contribution=phase1_monthly_contribution,
            volatility=volatility, rng=rng,
        )
        finals[i] = df["EndingBalance"].iloc[-1]
        hit = df[df["EndingBalance"] >= goal]
        if len(hit):
            years_to_goal.append(hit["GlobalMonth"].iloc[0] / 12.0)

    return {
        "n_sims":       n_sims,
        "success_rate": float((finals >= goal).mean()),
        "percentiles": {
            "p5":  float(np.percentile(finals, 5)),
            "p25": float(np.percentile(finals, 25)),
            "p50": float(np.percentile(finals, 50)),
            "p75": float(np.percentile(finals, 75)),
            "p95": float(np.percentile(finals, 95)),
        },
        "median_years_to_goal": float(np.median(years_to_goal)) if years_to_goal else None,
        "final_balances": finals,
    }


def executive_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Balances at Year 0, Year 6, Year 12, and Year 16 (or nearest
    available year if the simulation window differs)."""
    checkpoints = [0, 6, 12, 16]
    rows = []
    start_balance = df["StartingBalance"].iloc[0]
    max_year = int(df["Year"].max())
    for yr in checkpoints:
        if yr == 0:
            rows.append({"Year": 0, "Balance": start_balance})
            continue
        target_year = min(yr, max_year)
        year_rows = df[df["Year"] == target_year]
        rows.append({"Year": yr, "Balance": year_rows["EndingBalance"].iloc[-1]})
    return pd.DataFrame(rows)
