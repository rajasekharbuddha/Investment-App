"""
test_multibagger.py
===================
Unit tests for the multi-bagger screener.
Tests cover MB-score computation, hard filters, gap matching, and conviction tiers.
No network I/O — all tests use synthetic data.
"""

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from multibagger_screener import (
    mb_score,
    _passes_hard_filter,
    _check_technical_gate,
    _score_acceleration,
    _score_cap_position,
    conviction_tier,
    screen_multibaggers,
    mb_flags,
)
from sector_gaps import match_gap, GAP_CATALOGUE, SectorGap


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fund(
    revenue_cagr_3yr=0.25,
    revenue_growth=0.28,
    operating_margin=0.20,
    market_cap=50_000_000_000,   # ₹500 Cr equivalent
    debt_equity=0.40,
    eps_growth=0.30,
    sector="Industrials",
    industry="defence",
    **kwargs,
) -> dict:
    d = dict(
        ticker="TEST",
        name="Test Co",
        sector=sector,
        industry=industry,
        currency="INR",
        pe=25.0,
        pb=3.0,
        roe=0.18,
        net_margin=0.12,
        fcf_yield=0.03,
        market_cap=market_cap,
        debt_equity=debt_equity,
        revenue_cagr_3yr=revenue_cagr_3yr,
        revenue_growth=revenue_growth,
        operating_margin=operating_margin,
        eps_growth=eps_growth,
        _error=None,
    )
    d.update(kwargs)
    return d


def _price_df(sma50=110.0, sma200=100.0, close=115.0, sma50_prev=108.0) -> pd.DataFrame:
    """Minimal price DataFrame with SMA columns for the technical gate."""
    rows = [{"Close": 90.0, "SMA_50": sma50_prev, "SMA_200": sma200}] * 11
    rows.append({"Close": close, "SMA_50": sma50, "SMA_200": sma200})
    return pd.DataFrame(rows)


# ── MB-score ──────────────────────────────────────────────────────────────────

class TestMBScore:
    def test_high_quality_scores_above_60(self):
        score, _ = mb_score(_fund(), "IN")
        assert score >= 60.0

    def test_poor_fundamentals_score_low(self):
        d = _fund(
            revenue_cagr_3yr=0.01,
            revenue_growth=0.01,
            operating_margin=0.02,
            eps_growth=0.0,
            debt_equity=3.5,
            market_cap=200_000_000_000,
        )
        score, _ = mb_score(d, "IN")
        assert score < 50.0

    def test_returns_components_dict(self):
        _, c = mb_score(_fund(), "IN")
        for key in ["rev_growth_abs", "rev_acceleration", "operating_margin",
                    "cap_position", "debt_equity", "eps_growth"]:
            assert key in c

    def test_score_bounded_0_100(self):
        score, _ = mb_score(_fund(), "IN")
        assert 0.0 <= score <= 100.0

    def test_missing_metrics_handled(self):
        d = _fund(revenue_cagr_3yr=None, operating_margin=None)
        score, c = mb_score(d, "IN")
        assert isinstance(score, float)
        assert c["rev_growth_abs"] is None or c["rev_growth_abs"] >= 0
        assert c["operating_margin"] is None

    def test_high_growth_scores_10_on_rev(self):
        d = _fund(revenue_cagr_3yr=0.55, revenue_growth=0.55)
        _, c = mb_score(d, "IN")
        assert c["rev_growth_abs"] == 10

    def test_negative_growth_scores_0(self):
        d = _fund(revenue_cagr_3yr=-0.10, revenue_growth=-0.10)
        _, c = mb_score(d, "IN")
        assert c["rev_growth_abs"] == 0


# ── Revenue acceleration ──────────────────────────────────────────────────────

class TestRevenueAcceleration:
    def test_strong_acceleration(self):
        assert _score_acceleration(0.40, 0.20) == 10

    def test_moderate_acceleration(self):
        s = _score_acceleration(0.30, 0.22)
        assert 6 <= s <= 10

    def test_on_trend_neutral(self):
        assert _score_acceleration(0.25, 0.25) == 5

    def test_deceleration_low_score(self):
        s = _score_acceleration(0.10, 0.25)
        assert s <= 3

    def test_none_inputs_return_none(self):
        assert _score_acceleration(None, 0.20) is None
        assert _score_acceleration(0.20, None) is None
        assert _score_acceleration(None, None) is None


# ── Cap position ──────────────────────────────────────────────────────────────

class TestCapPosition:
    def test_tiny_cap_scores_10(self):
        mc = 500_000_000  # ₹5 Cr → 0.1% of ceiling
        assert _score_cap_position(mc, "IN") == 10

    def test_at_ceiling_scores_1(self):
        assert _score_cap_position(480_000_000_000, "IN") == 1

    def test_above_ceiling_scores_0(self):
        assert _score_cap_position(600_000_000_000, "IN") == 0

    def test_none_returns_none(self):
        assert _score_cap_position(None, "IN") is None

    def test_zero_returns_none(self):
        assert _score_cap_position(0, "IN") is None

    def test_different_markets(self):
        # Same absolute market cap should give different scores for different ceilings
        mc = 5_000_000_000
        s_in = _score_cap_position(mc, "IN")
        s_us = _score_cap_position(mc, "US")
        # IN ceiling is 500B → 5B/500B = 1% → score 10
        # US ceiling is 10B → 5B/10B = 50% → score 5
        assert s_in >= s_us


# ── Hard filter ───────────────────────────────────────────────────────────────

class TestHardFilter:
    def test_good_stock_passes(self):
        ok, _ = _passes_hard_filter(_fund(), "IN")
        assert ok is True

    def test_above_cap_ceiling_fails(self):
        ok, reason = _passes_hard_filter(_fund(market_cap=600_000_000_000), "IN")
        assert ok is False
        assert "ceiling" in reason.lower()

    def test_high_debt_fails(self):
        ok, reason = _passes_hard_filter(_fund(debt_equity=4.5), "IN")
        assert ok is False
        assert "D/E" in reason

    def test_de_exactly_4_passes(self):
        ok, _ = _passes_hard_filter(_fund(debt_equity=4.0), "IN")
        assert ok is True

    def test_revenue_decline_over_5pct_fails(self):
        ok, reason = _passes_hard_filter(
            _fund(revenue_cagr_3yr=-0.08, revenue_growth=-0.08), "IN"
        )
        assert ok is False
        assert "declining" in reason.lower() or "Revenue" in reason

    def test_small_decline_passes(self):
        ok, _ = _passes_hard_filter(
            _fund(revenue_cagr_3yr=-0.03, revenue_growth=-0.03), "IN"
        )
        assert ok is True

    def test_no_market_cap_passes(self):
        ok, _ = _passes_hard_filter(_fund(market_cap=None), "IN")
        assert ok is True


# ── Technical gate ────────────────────────────────────────────────────────────

class TestTechnicalGate:
    def test_healthy_trend_passes(self):
        df = _price_df(sma50=110, sma200=100, close=115, sma50_prev=108)
        assert _check_technical_gate(df) is True

    def test_sma50_below_sma200_fails(self):
        df = _price_df(sma50=95, sma200=100, close=115)
        assert _check_technical_gate(df) is False

    def test_close_below_sma200_fails(self):
        df = _price_df(sma50=110, sma200=100, close=98)
        assert _check_technical_gate(df) is False

    def test_sma50_declining_fails(self):
        df = _price_df(sma50=105, sma200=100, close=115, sma50_prev=110)
        assert _check_technical_gate(df) is False

    def test_none_input_returns_false(self):
        assert _check_technical_gate(None) is False

    def test_empty_df_returns_false(self):
        assert _check_technical_gate(pd.DataFrame()) is False


# ── Conviction tier ───────────────────────────────────────────────────────────

class TestConvictionTier:
    def _gap(self) -> SectorGap:
        return SectorGap(
            name="Test Gap", tailwind="STRONG", rationale="test",
            industry_keywords=[], sector_keywords=[],
        )

    def test_strong_with_gap_and_tech(self):
        assert conviction_tier(72, self._gap(), True) == "STRONG"

    def test_strong_with_gap_no_tech(self):
        assert conviction_tier(72, self._gap(), False) == "STRONG"

    def test_strong_high_score_with_tech(self):
        assert conviction_tier(67, None, True) == "STRONG"

    def test_moderate(self):
        assert conviction_tier(58, None, False) == "MODERATE"

    def test_watch(self):
        assert conviction_tier(42, None, False) == "WATCH"

    def test_skip(self):
        assert conviction_tier(30, None, False) == "SKIP"

    def test_boundary_moderate_to_watch(self):
        assert conviction_tier(55, None, False) == "MODERATE"
        assert conviction_tier(54, None, False) == "WATCH"


# ── Sector gap matching ───────────────────────────────────────────────────────

class TestGapMatching:
    def test_defence_industry_match_india(self):
        gap = match_gap("HAL.NS", "Industrials", "aerospace & defense", "IN")
        assert gap is not None
        assert "Defence" in gap.name

    def test_pharma_match_india(self):
        gap = match_gap("SUN.NS", "Healthcare", "pharmaceutical", "IN")
        assert gap is not None

    def test_no_match_consumer_staples(self):
        gap = match_gap("HUL.NS", "Consumer Staples", "household products", "IN")
        assert gap is None

    def test_us_nuclear_match(self):
        gap = match_gap("NNE", "Energy", "nuclear energy", "US")
        assert gap is not None
        assert "Nuclear" in gap.name

    def test_eu_defence_match(self):
        gap = match_gap("RHM.DE", "Industrials", "aerospace & defense", "EU")
        assert gap is not None

    def test_unknown_market_returns_none(self):
        gap = match_gap("X", "Unknown", "unknown", "JP")
        assert gap is None

    def test_catalogue_has_all_markets(self):
        for market in ["IN", "US", "EU"]:
            assert market in GAP_CATALOGUE
            assert len(GAP_CATALOGUE[market]) > 0


# ── MB flags ─────────────────────────────────────────────────────────────────

class TestMBFlags:
    def test_acceleration_flag(self):
        d = _fund(revenue_cagr_3yr=0.15, revenue_growth=0.35)
        flags = mb_flags(d)
        assert any("accelerating" in f.lower() for f in flags)

    def test_high_eps_flag(self):
        d = _fund(eps_growth=0.40)
        flags = mb_flags(d)
        assert any("EPS" in f for f in flags)

    def test_high_pe_flag(self):
        d = _fund(pe=90)
        flags = mb_flags(d)
        assert any("P/E" in f for f in flags)

    def test_deceleration_flag(self):
        d = _fund(revenue_cagr_3yr=0.35, revenue_growth=0.10)
        flags = mb_flags(d)
        assert any("decelerat" in f.lower() for f in flags)

    def test_no_flags_for_clean_stock(self):
        d = _fund(
            revenue_cagr_3yr=0.22, revenue_growth=0.24,
            debt_equity=0.30, eps_growth=0.25,
            pe=22, fcf_yield=0.03,
        )
        risk_flags = [f for f in mb_flags(d) if any(
            w in f.lower() for w in ["risk", "dilution", "burning", "priced"]
        )]
        assert len(risk_flags) == 0


# ── Integration: screen_multibaggers ─────────────────────────────────────────

class TestScreenMultibaggers:
    def _make_fundamentals(self, tickers):
        return {t: _fund(market_cap=50_000_000_000) for t in tickers}

    def test_returns_sorted_by_score(self):
        tickers = ["A", "B", "C"]
        f = {
            "A": _fund(revenue_cagr_3yr=0.40, market_cap=10_000_000_000),
            "B": _fund(revenue_cagr_3yr=0.10, market_cap=10_000_000_000),
            "C": _fund(revenue_cagr_3yr=0.25, market_cap=10_000_000_000),
        }
        results = screen_multibaggers(tickers, "IN", fundamentals=f)
        scores = [r["mb_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_min_score_filter(self):
        tickers = ["A", "B"]
        f = {
            "A": _fund(
                revenue_cagr_3yr=0.01, revenue_growth=0.01, operating_margin=0.01,
                eps_growth=0.0, debt_equity=3.5, market_cap=50_000_000_000,
            ),
            "B": _fund(market_cap=50_000_000_000),
        }
        results = screen_multibaggers(tickers, "IN", fundamentals=f, min_score=50.0)
        assert all(r["mb_score"] >= 50.0 for r in results)

    def test_gap_only_excludes_non_gap(self):
        tickers = ["A", "B"]
        f = {
            "A": _fund(industry="defence", sector="Industrials", market_cap=50_000_000_000),
            "B": _fund(industry="household products", sector="Consumer Staples",
                       market_cap=50_000_000_000),
        }
        results = screen_multibaggers(tickers, "IN", fundamentals=f, gap_only=True)
        assert all(r["gap"] is not None for r in results)

    def test_result_has_required_keys(self):
        f = {"A": _fund(market_cap=50_000_000_000)}
        results = screen_multibaggers(["A"], "IN", fundamentals=f, min_score=0)
        assert results
        for key in ["ticker", "mb_score", "conviction", "gap", "technical_gate",
                    "components", "flags"]:
            assert key in results[0], f"Missing key: {key}"

    def test_skips_error_stocks(self):
        f = {
            "A": {"_error": "Network timeout"},
            "B": _fund(market_cap=50_000_000_000),
        }
        results = screen_multibaggers(["A", "B"], "IN", fundamentals=f, min_score=0)
        tickers_out = [r["ticker"] for r in results]
        assert "A" not in tickers_out

    def test_hard_filter_excludes(self):
        f = {
            "GIANT": _fund(market_cap=900_000_000_000),  # above IN ceiling
        }
        results = screen_multibaggers(["GIANT"], "IN", fundamentals=f, min_score=0)
        assert len(results) == 0
