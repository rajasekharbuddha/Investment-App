"""
compare_sip_exits.py
====================
Tests whether skipping SMA-breakdown exit (and trim) when momentum is
still positive ("fundamentally strong" proxy) improves XIRR vs the
original strategy.

Runs two variants back-to-back on the same data and prints a comparison.

Usage:
    python src/compare_sip_exits.py --top-n 20 --start 2016-01-01
"""
from __future__ import annotations

import argparse, math, os, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent

# ── Shared backtest infrastructure (copy-free: patch the exit logic via flag) ─

def _run_variant(
    data_map: dict,
    benchmark_dfs: dict,
    start: str,
    region_budget: dict,
    max_picks: int,
    markets: list,
    quality_override: bool,          # <── the flag under test
    max_position_pct: float = 0.15,  # exit 3: trim threshold
    trim_to_pct: float      = 0.10,
) -> dict:
    """
    Run the SIP backtest with optional quality-override exits.

    quality_override=True:
        Before executing exit #1 (SMA breakdown) or exit #3 (trim),
        check if the stock's momentum score (avg 1M/3M/6M/12M) is
        positive.  If yes → skip that exit; the position is considered
        'fundamentally strong' and allowed to ride through the dip.

    quality_override=False:
        Original behaviour: exit on SMA breakdown regardless of momentum;
        trim whenever position > max_position_pct.
    """
    from backtest_sip import (
        _momentum, _breakdown_days, _price_at, _get_market,
        _max_drawdown, _xirr, _year_returns, _DEFAULT_PERIODS,
    )
    import numpy as np
    import pandas as pd
    from datetime import date

    periods     = _DEFAULT_PERIODS
    cost        = 0.001 + 0.001          # commission + slippage
    sma_days    = 10
    active_mkts = [m.upper() for m in markets]

    _CURRENCY = {"US": "USD", "EU": "EUR", "IN": "INR"}
    _SYMBOL   = {"US": "$",   "EU": "€",   "IN": "₹"}
    _DEFAULT_RMA = {"US": 200.0, "EU": 200.0, "IN": 2000.0}
    rb  = {m: region_budget.get(m, 2000.0) for m in active_mkts}
    rma = {m: _DEFAULT_RMA.get(m, 200.0)   for m in active_mkts}

    _bench_map = dict(benchmark_dfs or {})

    start_dt = pd.Timestamp(start)
    end_dt   = pd.Timestamp.now().normalize()

    all_dates: set = set()
    for df in data_map.values():
        all_dates.update(df.index)
    trading_days = sorted(d for d in all_dates if start_dt <= d <= end_dt)
    if len(trading_days) < 63:
        return {"error": "Insufficient data"}

    tickers_by_mkt: dict[str, list[str]] = {m: [] for m in active_mkts}
    for t, df in data_map.items():
        m = _get_market(t)
        if m in tickers_by_mkt:
            tickers_by_mkt[m].append(t)

    monthly_dates = trading_days[::21]

    region_portfolio: dict[str, dict]  = {m: {} for m in active_mkts}
    region_cash:      dict[str, float] = {m: 0.0 for m in active_mkts}
    region_invested:  dict[str, float] = {m: 0.0 for m in active_mkts}
    region_cfs:       dict[str, list]  = {m: [] for m in active_mkts}
    region_nav_hist:  dict[str, list]  = {m: [] for m in active_mkts}

    bench_shares: dict[str, float] = {m: 0.0 for m in active_mkts}
    bench_cfs:    dict[str, list]  = {m: [] for m in active_mkts}

    nav_history: list[dict] = []
    exits_skipped = 0
    trims_skipped = 0

    for cycle_date in monthly_dates:
        py_date = cycle_date.date()

        # ── Deposit + benchmark
        for mkt in active_mkts:
            bgt = rb[mkt]
            region_cash[mkt]     += bgt
            region_invested[mkt] += bgt
            region_cfs[mkt].append((-bgt, py_date))

            bdf = _bench_map.get(mkt)
            if bdf is not None:
                bp = _price_at(bdf, cycle_date, offset=cost)
                if bp and bp > 0:
                    bench_shares[mkt] += bgt / bp
                    bench_cfs[mkt].append((-bgt, py_date))

        for mkt in active_mkts:
            port    = region_portfolio[mkt]
            tickers = tickers_by_mkt[mkt]
            bgt     = rb[mkt]
            _rma    = rma[mkt]

            # ── Compute current region NAV (needed for position-pct trim)
            region_nav_now = region_cash[mkt]
            for tk, pos in port.items():
                df  = data_map.get(tk)
                sub = df.loc[:cycle_date] if df is not None else None
                if sub is not None and not sub.empty:
                    region_nav_now += pos["shares"] * float(sub.iloc[-1]["Close"])

            # ── Exit 1: SMA breakdown
            for ticker in list(port.keys()):
                df  = data_map.get(ticker)
                if df is None:
                    continue
                sub = df.loc[:cycle_date]
                if _breakdown_days(sub) < sma_days:
                    continue

                # Quality override: skip if momentum still positive
                if quality_override:
                    mom = _momentum(sub, periods)
                    if not math.isnan(mom) and mom > 0:
                        exits_skipped += 1
                        continue

                sell_px = _price_at(sub, cycle_date, offset=-cost)
                if sell_px and sell_px > 0:
                    region_cash[mkt] += port[ticker]["shares"] * sell_px
                del port[ticker]

            # ── Exit 3: Trim oversized positions
            for ticker in list(port.keys()):
                if region_nav_now <= 0:
                    continue
                df  = data_map.get(ticker)
                if df is None:
                    continue
                sub = df.loc[:cycle_date]
                if sub.empty:
                    continue
                cur_px  = float(sub.iloc[-1]["Close"])
                pos_val = port[ticker]["shares"] * cur_px
                pos_pct = pos_val / region_nav_now

                if pos_pct <= max_position_pct:
                    continue

                # Quality override: skip trim if momentum still positive
                if quality_override:
                    mom = _momentum(sub, periods)
                    if not math.isnan(mom) and mom > 0:
                        trims_skipped += 1
                        continue

                target_val  = region_nav_now * trim_to_pct
                trim_val    = pos_val - target_val
                trim_shares = trim_val / cur_px if cur_px > 0 else 0
                if trim_shares > 0:
                    sell_px = _price_at(sub, cycle_date, offset=-cost)
                    if sell_px and sell_px > 0:
                        proceeds = trim_shares * sell_px
                        region_cash[mkt] += proceeds
                        port[ticker]["shares"] -= trim_shares
                        port[ticker]["total_cost"] -= trim_val
                        if port[ticker]["shares"] <= 0:
                            del port[ticker]

            # ── Select & buy
            from backtest_sip import _select_picks
            picks  = _select_picks(data_map, tickers, cycle_date, max_picks,
                                   sma_days, port, periods)
            deploy = min(bgt, region_cash[mkt])
            if picks and deploy >= _rma:
                n         = min(len(picks), max(1, int(deploy / _rma)))
                picks     = picks[:n]
                alloc_per = deploy / len(picks)
                for ticker in picks:
                    df     = data_map[ticker]
                    sub    = df.loc[:cycle_date]
                    buy_px = _price_at(sub, cycle_date, offset=cost)
                    if not buy_px or buy_px <= 0:
                        continue
                    shares = alloc_per / buy_px
                    region_cash[mkt] -= alloc_per
                    if ticker in port:
                        prev = port[ticker]
                        tot  = prev["shares"] + shares
                        port[ticker] = {"shares": tot,
                                        "avg_cost": (prev["total_cost"]+alloc_per)/tot,
                                        "total_cost": prev["total_cost"]+alloc_per}
                    else:
                        port[ticker] = {"shares": shares,
                                        "avg_cost": buy_px,
                                        "total_cost": alloc_per}

            # ── NAV snapshot
            region_nav = region_cash[mkt]
            for tk, pos in port.items():
                df  = data_map.get(tk)
                sub = df.loc[:cycle_date] if df is not None else None
                if sub is not None and not sub.empty:
                    region_nav += pos["shares"] * float(sub.iloc[-1]["Close"])
            region_nav_hist[mkt].append({"date": str(py_date), "nav": region_nav,
                                          "invested": region_invested[mkt]})

        nav_history.append({
            "date": str(py_date),
            "nav":  sum(region_nav_hist[m][-1]["nav"] for m in active_mkts),
            "positions": sum(len(region_portfolio[m]) for m in active_mkts),
        })

    # ── Final liquidation
    final_date = end_dt.date()
    region_final: dict[str, float] = {}
    region_gain:  dict[str, float] = {}
    region_benchmark_xirr: dict[str, float] = {}

    for mkt in active_mkts:
        port  = region_portfolio[mkt]
        r_nav = region_cash[mkt]
        for ticker, pos in port.items():
            df  = data_map.get(ticker)
            sub = df.loc[:end_dt] if df is not None else None
            if sub is not None and not sub.empty:
                r_nav += pos["shares"] * float(sub.iloc[-1]["Close"])
        region_final[mkt] = round(r_nav, 2)
        region_gain[mkt]  = round(r_nav - region_invested[mkt], 2)
        region_cfs[mkt].append((r_nav, final_date))

        bdf = _bench_map.get(mkt)
        if bdf is not None and bench_cfs[mkt]:
            bsub = bdf.loc[:end_dt]
            if not bsub.empty:
                bench_fin = bench_shares[mkt] * float(bsub.iloc[-1]["Close"])
                bench_cfs[mkt].append((bench_fin, final_date))
                region_benchmark_xirr[mkt] = _xirr(bench_cfs[mkt])
            else:
                region_benchmark_xirr[mkt] = float("nan")
        else:
            region_benchmark_xirr[mkt] = float("nan")

    region_xirr = {m: _xirr(region_cfs[m]) for m in active_mkts}
    nav_vals    = [h["nav"] for h in nav_history]
    max_dd      = _max_drawdown(nav_vals)
    avg_pos     = float(sum(h["positions"] for h in nav_history) / len(nav_history)) if nav_history else 0.0

    return {
        "region_xirr":           region_xirr,
        "region_benchmark_xirr": region_benchmark_xirr,
        "region_invested":       region_invested,
        "region_final":          region_final,
        "region_gain":           region_gain,
        "max_drawdown":          round(max_dd * 100, 2),
        "n_cycles":              len(monthly_dates),
        "avg_positions":         round(avg_pos, 1),
        "exits_skipped":         exits_skipped,
        "trims_skipped":         trims_skipped,
        "region_nav_hist":       region_nav_hist,
        "region_symbol":         _SYMBOL,
        "region_currency":       _CURRENCY,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",      default="2016-01-01")
    parser.add_argument("--markets",    default="US,EU,IN")
    parser.add_argument("--top-n",      type=int, default=20)
    parser.add_argument("--max-picks",  type=int, default=5)
    parser.add_argument("--budget-us",  type=float, default=2000.0)
    parser.add_argument("--budget-eu",  type=float, default=2000.0)
    parser.add_argument("--budget-in",  type=float, default=20000.0)
    args = parser.parse_args()

    markets       = [m.strip().upper() for m in args.markets.split(",")]
    region_budget = {"US": args.budget_us, "EU": args.budget_eu, "IN": args.budget_in}
    YEARS         = 11

    print(f"\n{'='*68}")
    print(f"  SIP Exit Strategy Comparison")
    print(f"  Period: {args.start} to today  |  Markets: {', '.join(markets)}")
    print(f"  Universe: top {args.top_n}/market  |  Max picks: {args.max_picks}")
    print(f"{'='*68}")

    # ── 1. Universe & data ────────────────────────────────────────────────────
    print("\n[1/3] Building universe + fetching price history...")
    from universe import get_dynamic_watchlist
    from data import fetch_history, CACHE_DIR
    from indicators import calculate_all
    import pandas as pd

    wl      = get_dynamic_watchlist(markets, top_n_map={m: args.top_n for m in markets})
    tickers = [t for m in markets for t in wl.get(m, [])]
    print(f"      {len(tickers)} tickers")

    start_ts = pd.Timestamp(args.start)
    data_map: dict = {}
    for i, ticker in enumerate(tickers):
        if i % 10 == 0:
            print(f"      {i}/{len(tickers)}...", end="\r", flush=True)
        try:
            safe, cache = ticker.replace("/","_"), CACHE_DIR / f"{ticker.replace('/','_')}.parquet"
            use_cache = True
            if cache.exists():
                try:
                    cdf = pd.read_parquet(cache)
                    if cdf.empty or cdf.index[0] > start_ts:
                        use_cache = False
                except Exception:
                    use_cache = False
            df = fetch_history(ticker, years=YEARS, use_cache=use_cache)
            if df is not None and len(df) >= 250:
                data_map[ticker] = calculate_all(df)
        except Exception:
            pass
    print(f"      {len(data_map)} tickers ready              ")

    # ── 2. Benchmarks ─────────────────────────────────────────────────────────
    print("\n[2/3] Fetching benchmarks...")
    _BTICKERS = {"US": "^GSPC", "EU": "^STOXX50E", "IN": "^NSEI"}
    benchmark_dfs: dict = {}
    for mkt, bt in _BTICKERS.items():
        if mkt not in markets:
            continue
        try:
            bdf = fetch_history(bt, years=YEARS)
            if bdf is not None:
                benchmark_dfs[mkt] = bdf
                print(f"      {bt} ({mkt}) — {len(bdf)} days")
        except Exception:
            pass

    # ── 3. Run both variants ──────────────────────────────────────────────────
    print("\n[3/3] Running variants...")

    common = dict(data_map=data_map, benchmark_dfs=benchmark_dfs,
                  start=args.start, region_budget=region_budget,
                  max_picks=args.max_picks, markets=markets)

    print("      Variant A — original exits (SMA breakdown + trim always active)...")
    res_a = _run_variant(**common, quality_override=False)

    print("      Variant B — quality override (skip SMA exit + trim when momentum > 0)...")
    res_b = _run_variant(**common, quality_override=True)

    # ── Print comparison ──────────────────────────────────────────────────────
    _SYM = {"US":"$","EU":"€","IN":"₹"}
    _CUR = {"US":"USD","EU":"EUR","IN":"INR"}
    sep  = "=" * 68
    dash = "-" * 44

    def pct(v):
        return f"{v*100:+.2f}%" if not math.isnan(v) else "N/A"

    print(f"\n{sep}")
    print(f"  RESULTS COMPARISON")
    print(f"{sep}")
    print(f"  {'Market':<4}  {'Metric':<22}  {'Original':>10}  {'Quality OVR':>11}  {'Delta':>8}")
    print(f"  {dash}")

    for mkt in markets:
        sym  = _SYM.get(mkt, "")
        cur  = _CUR.get(mkt, "")
        xa   = res_a["region_xirr"].get(mkt, float("nan"))
        xb   = res_b["region_xirr"].get(mkt, float("nan"))
        bxa  = res_a["region_benchmark_xirr"].get(mkt, float("nan"))
        fa   = res_a["region_final"].get(mkt, 0.0)
        fb   = res_b["region_final"].get(mkt, 0.0)
        ga   = res_a["region_gain"].get(mkt, 0.0)
        gb   = res_b["region_gain"].get(mkt, 0.0)
        inv  = res_a["region_invested"].get(mkt, 0.0)

        print(f"\n  {mkt} ({cur})  benchmark XIRR: {pct(bxa)}")
        print(f"  {'':4}  {'XIRR':<22}  {pct(xa):>10}  {pct(xb):>11}  {pct(xb-xa) if not math.isnan(xa) and not math.isnan(xb) else 'N/A':>8}")
        print(f"  {'':4}  {'Final NAV':<22}  {sym}{fa:>9,.0f}  {sym}{fb:>10,.0f}  {sym}{fb-fa:>+7,.0f}")
        print(f"  {'':4}  {'Total Gain':<22}  {sym}{ga:>9,.0f}  {sym}{gb:>10,.0f}  {sym}{gb-ga:>+7,.0f}")
        print(f"  {'':4}  {'Gain % on invested':<22}  {ga/inv*100:>+9.1f}%  {gb/inv*100:>+10.1f}%  {(gb-ga)/inv*100:>+7.1f}%")

    print(f"\n  {'Metric':<26}  {'Original':>10}  {'Quality OVR':>11}")
    print(f"  {dash}")
    print(f"  {'Max Drawdown':<26}  {res_a['max_drawdown']:>+9.2f}%  {res_b['max_drawdown']:>+10.2f}%")
    print(f"  {'Avg open positions':<26}  {res_a['avg_positions']:>10.1f}  {res_b['avg_positions']:>11.1f}")
    print(f"  {'SMA exits skipped (B only)':<26}  {'':>10}  {res_b['exits_skipped']:>11}")
    print(f"  {'Trims skipped (B only)':<26}  {'':>10}  {res_b['trims_skipped']:>11}")
    print(f"\n{sep}\n")

    # ── Verdict ───────────────────────────────────────────────────────────────
    wins_b = sum(
        1 for m in markets
        if not math.isnan(res_b["region_xirr"].get(m, float("nan")))
        and not math.isnan(res_a["region_xirr"].get(m, float("nan")))
        and res_b["region_xirr"][m] > res_a["region_xirr"][m]
    )
    print(f"  VERDICT: Quality override {'IMPROVES' if wins_b == len(markets) else 'IMPROVES SOME, HURTS OTHERS' if wins_b > 0 else 'DOES NOT IMPROVE'} XIRR")
    print(f"           ({wins_b}/{len(markets)} markets show higher XIRR with quality override)")
    print(f"           Max DD change: {res_b['max_drawdown'] - res_a['max_drawdown']:+.2f}pp\n")


if __name__ == "__main__":
    main()
