"""
fundamental.py
==============
Fetches, caches, and scores fundamental data for long-term investment screening.

Data source : yfinance Ticker.info + Ticker.financials (annual)
Cache       : fundamental_cache.json (7-day TTL, lives in project root)
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Scoring weights (must sum to 1.0) ────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "roe":              0.20,   # Core profitability — return on shareholders' equity
    "revenue_growth":   0.15,   # Top-line momentum (3yr CAGR preferred)
    "eps_growth":       0.12,   # Earnings conversion quality
    "debt_equity":      0.15,   # Balance-sheet safety (low leverage = resilient)
    "operating_margin": 0.10,   # Business moat (pricing power)
    "fcf_yield":        0.08,   # Real cash generation vs market cap
    "peg":              0.10,   # Valuation relative to growth
    "pb":               0.05,   # Asset backing / book value
    "net_margin":       0.05,   # Net profitability
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-6, "Weights must sum to 1.0"

_CACHE_FILE    = Path(__file__).parent.parent / "fundamental_cache.json"
_CACHE_TTL     = timedelta(days=7)
# Bump when the cached fields or their units change so stale entries are re-fetched.
# v2: debt_equity converted from yfinance's percent to a ratio; Graham fields added.
_CACHE_SCHEMA  = 2

# ── Graham defensive-investor thresholds (The Intelligent Investor, ch. 14) ──
GRAHAM: dict[str, float] = {
    "min_current_ratio": 2.0,    # current assets >= 2x current liabilities
    "max_pe_x_pb":       22.5,   # P/E x P/B <= 22.5 (15x earnings x 1.5x book)
}
# Banks/insurers have no meaningful current ratio — skip the balance-sheet tests
GRAHAM_FINANCIAL_SECTORS = ("Financial Services", "Financial")


# ── Utilities ────────────────────────────────────────────────────────────────

def _safe(val, fallback=None):
    """Return fallback for None / NaN / Inf values."""
    if val is None:
        return fallback
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return fallback
    return val


def _score_steps(val: Optional[float],
                 steps: list[tuple[float, int]]) -> Optional[int]:
    """
    Score val against a descending threshold list.
    steps = [(min_value, score), ...] — first match wins.
    Returns None if val is None (metric unavailable).
    """
    if val is None:
        return None
    for threshold, score in steps:
        if val >= threshold:
            return score
    return steps[-1][1]  # bottom bucket


# ── Cache ────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")


def _is_fresh(entry: dict) -> bool:
    if entry.get("_schema") != _CACHE_SCHEMA:
        return False
    ts = entry.get("_cached_at")
    if not ts:
        return False
    try:
        return datetime.now() - datetime.fromisoformat(str(ts)) < _CACHE_TTL
    except Exception:
        return False


# ── Fetcher ──────────────────────────────────────────────────────────────────

def fetch_fundamentals(ticker: str, use_cache: bool = True) -> dict:
    """
    Fetch raw fundamental data for a single ticker via yfinance.
    Results are cached for 7 days in fundamental_cache.json.

    Returned keys
    -------------
    ticker, name, sector, industry, currency
    pe, pb, ev_ebitda
    roe, debt_equity, operating_margin, net_margin
    revenue_growth  (YoY from info — fallback)
    revenue_cagr_3yr (computed from annual financials — preferred)
    eps_growth
    fcf_yield       (freeCashflow / marketCap)
    market_cap
    price, eps_ttm, book_value_ps
    current_ratio, current_assets, current_liabilities, long_term_debt
    _error          (None or error string)
    """
    result: dict = {
        "ticker":            ticker,
        "name":              ticker,
        "sector":            "Unknown",
        "industry":          "Unknown",
        "currency":          "",
        "pe":                None,
        "pb":                None,
        "ev_ebitda":         None,
        "roe":               None,
        "debt_equity":       None,
        "operating_margin":  None,
        "net_margin":        None,
        "revenue_growth":    None,
        "revenue_cagr_3yr":  None,
        "eps_growth":        None,
        "fcf_yield":         None,
        "market_cap":        None,
        "price":             None,
        "eps_ttm":           None,
        "book_value_ps":     None,
        "current_ratio":     None,
        "current_assets":    None,
        "current_liabilities": None,
        "long_term_debt":    None,
        "_cached_at":        datetime.now().isoformat(),
        "_schema":           _CACHE_SCHEMA,
        "_error":            None,
    }

    if use_cache:
        cache = _load_cache()
        if ticker in cache and _is_fresh(cache[ticker]):
            return cache[ticker]
    else:
        cache = {}

    try:
        import yfinance as yf
        t    = yf.Ticker(ticker)
        info = t.info or {}

        result["name"]            = _safe(info.get("longName") or info.get("shortName"), ticker)
        result["sector"]          = _safe(info.get("sector"), "Unknown")
        result["industry"]        = _safe(info.get("industry"), "Unknown")
        result["currency"]        = _safe(info.get("currency"), "")
        result["pe"]              = _safe(info.get("trailingPE") or info.get("forwardPE"))
        result["pb"]              = _safe(info.get("priceToBook"))
        result["ev_ebitda"]       = _safe(info.get("enterpriseToEbitda"))
        result["roe"]             = _safe(info.get("returnOnEquity"))
        result["operating_margin"]= _safe(info.get("operatingMargins"))
        result["net_margin"]      = _safe(info.get("profitMargins"))
        result["revenue_growth"]  = _safe(info.get("revenueGrowth"))
        result["eps_growth"]      = _safe(info.get("earningsGrowth"))
        result["market_cap"]      = _safe(info.get("marketCap"))
        # yfinance reports debtToEquity as a percentage (45.3 == 0.453x)
        de_pct = _safe(info.get("debtToEquity"))
        result["debt_equity"]     = de_pct / 100 if de_pct is not None else None
        result["price"]           = _safe(info.get("currentPrice") or info.get("regularMarketPrice"))
        result["eps_ttm"]         = _safe(info.get("trailingEps"))
        result["book_value_ps"]   = _safe(info.get("bookValue"))
        result["current_ratio"]   = _safe(info.get("currentRatio"))

        # FCF yield = freeCashflow / marketCap
        fcf = _safe(info.get("freeCashflow"))
        mc  = result["market_cap"]
        if fcf is not None and mc and mc > 0:
            result["fcf_yield"] = fcf / mc

        # 3yr revenue CAGR from annual financials (FY0 → FY-3)
        try:
            fins = t.financials   # columns = dates (newest first), rows = line items
            if fins is not None and not fins.empty:
                rev_key = next(
                    (k for k in ["Total Revenue", "Operating Revenue", "Revenue"]
                     if k in fins.index),
                    None,
                )
                if rev_key:
                    rev = fins.loc[rev_key].dropna().sort_index(ascending=False)
                    if len(rev) >= 2:
                        newest  = float(rev.iloc[0])
                        n_yrs   = min(3, len(rev) - 1)
                        oldest  = float(rev.iloc[n_yrs])
                        if oldest > 0 and newest > 0:
                            result["revenue_cagr_3yr"] = (newest / oldest) ** (1 / n_yrs) - 1
        except Exception:
            pass

        # Latest balance sheet — for Graham's "long-term debt <= net current assets"
        try:
            bs = t.balance_sheet
            if bs is not None and not bs.empty:
                latest = bs[bs.columns.max()]

                def _row(*keys):
                    for k in keys:
                        if k in latest.index and latest[k] == latest[k]:  # not NaN
                            return float(latest[k])
                    return None

                result["current_assets"]      = _row("Current Assets", "Total Current Assets")
                result["current_liabilities"] = _row("Current Liabilities", "Total Current Liabilities")
                result["long_term_debt"]      = _row("Long Term Debt",
                                                     "Long Term Debt And Capital Lease Obligation")
                if result["long_term_debt"] is None and _safe(info.get("totalDebt")) == 0:
                    result["long_term_debt"] = 0.0   # debt-free: yfinance omits the row
                ca, cl = result["current_assets"], result["current_liabilities"]
                if result["current_ratio"] is None and ca is not None and cl:
                    result["current_ratio"] = ca / cl
        except Exception:
            pass

    except Exception as exc:
        result["_error"] = str(exc)

    if use_cache:
        cache[ticker] = result
        _save_cache(cache)

    return result


def fetch_all_fundamentals(
    tickers: list[str],
    use_cache: bool = True,
    delay: float = 0.3,
) -> dict[str, dict]:
    """Fetch fundamentals for multiple tickers with rate-limiting and progress display."""
    cache    = _load_cache() if use_cache else {}
    results  = {}
    to_fetch = []

    for t in tickers:
        if use_cache and t in cache and _is_fresh(cache[t]):
            results[t] = cache[t]
        else:
            to_fetch.append(t)

    for i, ticker in enumerate(to_fetch):
        print(f"  [{i+1}/{len(to_fetch)}] Fetching {ticker}...")
        results[ticker] = fetch_fundamentals(ticker, use_cache=use_cache)
        if i < len(to_fetch) - 1 and delay > 0:
            time.sleep(delay)

    return results


# ── Scoring ──────────────────────────────────────────────────────────────────

def score_fundamentals(data: dict) -> tuple[float, dict[str, Optional[int]]]:
    """
    Score fundamental data on a 0–100 scale.
    Each component is scored 0–10 then weighted.
    Missing components are excluded from the weighted average (no penalty for N/A data).

    Returns
    -------
    (overall_score: float 0–100, components: dict metric → score 0–10 or None)
    """
    c: dict[str, Optional[int]] = {}

    # ── ROE ──────────────────────────────────────────────────────────────────
    c["roe"] = _score_steps(data.get("roe"), [
        (0.25, 10), (0.20, 8), (0.15, 6), (0.10, 4), (0.05, 2), (-99, 0),
    ])

    # ── Revenue growth — prefer 3yr CAGR over single-year YoY ───────────────
    rg = data.get("revenue_cagr_3yr") or data.get("revenue_growth")
    c["revenue_growth"] = _score_steps(rg, [
        (0.25, 10), (0.20, 8), (0.15, 7), (0.10, 6),
        (0.05, 4),  (0.0,  2), (-99, 0),
    ])

    # ── EPS growth ───────────────────────────────────────────────────────────
    c["eps_growth"] = _score_steps(data.get("eps_growth"), [
        (0.30, 10), (0.20, 8), (0.15, 7), (0.10, 6),
        (0.05, 4),  (0.0,  2), (-99, 0),
    ])

    # ── Debt / Equity (lower = better) ───────────────────────────────────────
    de = data.get("debt_equity")
    if de is not None:
        if   de <= 0.10: c["debt_equity"] = 10
        elif de <= 0.30: c["debt_equity"] = 9
        elif de <= 0.50: c["debt_equity"] = 8
        elif de <= 0.75: c["debt_equity"] = 7
        elif de <= 1.00: c["debt_equity"] = 5
        elif de <= 1.50: c["debt_equity"] = 3
        elif de <= 3.00: c["debt_equity"] = 2
        else:            c["debt_equity"] = 1
    else:
        c["debt_equity"] = None

    # ── Operating margin ─────────────────────────────────────────────────────
    c["operating_margin"] = _score_steps(data.get("operating_margin"), [
        (0.25, 10), (0.20, 8), (0.15, 7), (0.10, 6),
        (0.07, 5),  (0.05, 4), (0.03, 3), (-99, 1),
    ])

    # ── FCF yield ────────────────────────────────────────────────────────────
    c["fcf_yield"] = _score_steps(data.get("fcf_yield"), [
        (0.06, 10), (0.04, 8), (0.03, 7), (0.02, 6),
        (0.01, 4),  (0.0,  2), (-99, 0),
    ])

    # ── PEG ratio (P/E ÷ EPS-growth%) — lower = better ──────────────────────
    pe = data.get("pe")
    eg = data.get("eps_growth")
    if pe is not None and pe > 0:
        if eg and eg > 0.02:
            peg = pe / (eg * 100)
            if   peg <= 0.50: c["peg"] = 10
            elif peg <= 0.75: c["peg"] = 9
            elif peg <= 1.00: c["peg"] = 8
            elif peg <= 1.50: c["peg"] = 6
            elif peg <= 2.00: c["peg"] = 4
            elif peg <= 3.00: c["peg"] = 2
            else:             c["peg"] = 1
        else:
            # No reliable growth data — score P/E alone (conservative)
            if   pe <= 15: c["peg"] = 8
            elif pe <= 25: c["peg"] = 6
            elif pe <= 35: c["peg"] = 4
            elif pe <= 50: c["peg"] = 2
            else:          c["peg"] = 1
    else:
        c["peg"] = None

    # ── P/B (lower = better) ─────────────────────────────────────────────────
    pb = data.get("pb")
    if pb is not None and pb > 0:
        if   pb <= 1.5:  c["pb"] = 10
        elif pb <= 3.0:  c["pb"] = 8
        elif pb <= 5.0:  c["pb"] = 6
        elif pb <= 8.0:  c["pb"] = 4
        elif pb <= 12.0: c["pb"] = 2
        else:            c["pb"] = 1
    else:
        c["pb"] = None

    # ── Net margin ───────────────────────────────────────────────────────────
    c["net_margin"] = _score_steps(data.get("net_margin"), [
        (0.20, 10), (0.15, 8), (0.10, 7), (0.08, 6),
        (0.05, 5),  (0.03, 3), (-99, 1),
    ])

    # ── Weighted average (skip None, redistribute weight) ────────────────────
    total_w = sum(WEIGHTS[m] for m, s in c.items() if s is not None)
    total_s = sum(WEIGHTS[m] * s for m, s in c.items() if s is not None)

    final = min(100.0, (total_s / total_w * 10)) if total_w > 0 else 0.0
    return final, c


def fundamental_grade(score: float) -> str:
    if score >= 75: return "Strong"
    if score >= 60: return "Good"
    if score >= 45: return "Fair"
    return "Weak"


def graham_number(data: dict) -> Optional[float]:
    """Graham number = sqrt(22.5 x EPS x book value per share); None if either is <= 0."""
    eps, bvps = data.get("eps_ttm"), data.get("book_value_ps")
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return (22.5 * eps * bvps) ** 0.5


def graham_check(data: dict) -> dict:
    """
    Benjamin Graham's defensive-investor safety tests (The Intelligent Investor):

      current_ratio   current assets >= 2x current liabilities
      debt_vs_nca     long-term debt <= net current assets
      pe_x_pb         P/E x P/B <= 22.5

    Each test is True / False, or None when data is missing. The two
    balance-sheet tests are skipped (None) for financial-sector stocks.
    A stock "passes" only if every applicable test is True — missing data
    counts as a fail, in keeping with Graham's conservatism.

    Also returns the Graham number and the margin of safety
    (graham_number - price) / graham_number; positive means price is below it.
    """
    is_fin = data.get("sector") in GRAHAM_FINANCIAL_SECTORS
    tests: dict[str, Optional[bool]] = {}

    cr = data.get("current_ratio")
    tests["current_ratio"] = (None if is_fin or cr is None
                              else cr >= GRAHAM["min_current_ratio"])

    ca, cl, ltd = (data.get("current_assets"), data.get("current_liabilities"),
                   data.get("long_term_debt"))
    tests["debt_vs_nca"] = (None if is_fin or ca is None or cl is None or ltd is None
                            else ltd <= ca - cl)

    pe, pb = data.get("pe"), data.get("pb")
    tests["pe_x_pb"] = (None if pe is None or pb is None or pe <= 0 or pb <= 0
                        else pe * pb <= GRAHAM["max_pe_x_pb"])

    applicable = ["pe_x_pb"] if is_fin else list(tests)
    passed     = all(tests[k] is True for k in applicable)

    gn    = graham_number(data)
    price = data.get("price")
    mos   = (gn - price) / gn if gn and price else None

    return {
        "tests":         tests,
        "applicable":    applicable,
        "passed":        passed,
        "financial":     is_fin,
        "graham_number": gn,
        "margin_of_safety": mos,
    }


def red_flags(data: dict) -> list[str]:
    """Identify key fundamental risk signals worth reviewing."""
    flags = []

    fcf = data.get("fcf_yield")
    nm  = data.get("net_margin")
    if fcf is not None and nm is not None and nm > 0.02 and fcf < 0:
        flags.append("Negative FCF despite positive margins — check earnings quality")

    pe  = data.get("pe")
    eg  = data.get("eps_growth")
    if pe and pe > 60 and (eg is None or eg < 0.10):
        flags.append(f"P/E {pe:.0f}x with <10% EPS growth — stretched valuation")

    de = data.get("debt_equity")
    if de is not None and de > 2.0:
        flags.append(f"High D/E {de:.2f}x — elevated balance-sheet risk")

    roe = data.get("roe")
    if roe is not None and roe < 0:
        flags.append("Negative ROE — currently loss-making")

    rg = data.get("revenue_cagr_3yr") or data.get("revenue_growth")
    if rg is not None and rg < -0.03:
        flags.append(f"Revenue declining {rg*100:.1f}% YoY — top-line contraction")

    return flags
