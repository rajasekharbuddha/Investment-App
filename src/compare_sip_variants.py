"""
compare_sip_variants.py
=======================
Compares four SIP strategy variants back-to-back on the same dataset.

Variants
--------
  A  Original          100% deployed each month, max 5 picks
  B  Small reserve     5% held back; deployed when stock drops -10% from avg cost
  C  Regime reserve    20% held back; deployed in bulk when index SMA_200 breaks
                       (concentrate dry powder at actual market lows, not random dips)
  D  More picks        100% deployed, max 8 picks per region
                       (wider diversification = built-in averaging effect)

Run:
  python src/compare_sip_variants.py --top-n 20 --start 2016-01-01
"""
from __future__ import annotations

import argparse, math, os, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent


# ── Shared helpers ────────────────────────────────────────────────────────────

def _sma200_of_index(bench_df, as_of) -> tuple[float, float]:
    """Return (close, sma200) for the index as of `as_of`.  Both NaN if missing."""
    import numpy as np
    sub = bench_df.loc[:as_of]
    if len(sub) < 200:
        return float("nan"), float("nan")
    close  = float(sub["Close"].iloc[-1])
    sma200 = float(sub["Close"].iloc[-200:].mean())
    return close, sma200


# ── Core variant runner ───────────────────────────────────────────────────────

def _run_variant(
    label:            str,
    data_map:         dict,
    benchmark_dfs:    dict,
    start:            str,
    region_budget:    dict,
    max_picks:        int,
    markets:          list,
    # -- reserve params
    reserve_pct:      float = 0.0,   # fraction of monthly budget held back
    # -- variant B: individual-stock dip trigger
    indiv_dip:        bool  = False,
    dip_threshold:    float = 0.10,
    # -- variant C: regime-shift trigger
    regime_deploy:    bool  = False,
) -> dict:
    """
    Unified runner.  Control which variant runs via the flags above.

    reserve_pct > 0 + indiv_dip=True   → Variant B  (stock-level dip)
    reserve_pct > 0 + regime_deploy=True → Variant C (index regime-shift)
    reserve_pct = 0 + higher max_picks → Variant D  (more picks)
    all defaults at 0 / False           → Variant A  (original)
    """
    from backtest_sip import (
        _breakdown_days, _price_at, _get_market,
        _max_drawdown, _xirr, _year_returns, _DEFAULT_PERIODS, _select_picks,
    )
    import pandas as pd

    periods  = _DEFAULT_PERIODS
    cost     = 0.001 + 0.001
    sma_days = 10
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
    for t in data_map:
        m = _get_market(t)
        if m in tickers_by_mkt:
            tickers_by_mkt[m].append(t)

    monthly_dates = trading_days[::21]

    # ── Per-region state ──────────────────────────────────────────────────────
    region_portfolio: dict[str, dict]  = {m: {} for m in active_mkts}
    region_cash:      dict[str, float] = {m: 0.0 for m in active_mkts}
    dip_reserve:      dict[str, float] = {m: 0.0 for m in active_mkts}
    region_invested:  dict[str, float] = {m: 0.0 for m in active_mkts}
    region_cfs:       dict[str, list]  = {m: [] for m in active_mkts}
    region_nav_hist:  dict[str, list]  = {m: [] for m in active_mkts}

    bench_shares: dict[str, float] = {m: 0.0 for m in active_mkts}
    bench_cfs:    dict[str, list]  = {m: [] for m in active_mkts}
    nav_history:  list[dict] = []

    # Stats
    reserve_deploy_events = 0
    regime_months: dict[str, int] = {m: 0 for m in active_mkts}  # months in downtrend

    for cycle_date in monthly_dates:
        py_date = cycle_date.date()

        # ── Deposit budget split ──────────────────────────────────────────────
        for mkt in active_mkts:
            bgt        = rb[mkt]
            to_invest  = bgt * (1.0 - reserve_pct)
            to_reserve = bgt * reserve_pct

            region_cash[mkt]     += to_invest
            dip_reserve[mkt]     += to_reserve
            region_invested[mkt] += bgt
            region_cfs[mkt].append((-bgt, py_date))

            bdf = _bench_map.get(mkt)
            if bdf is not None:
                bp = _price_at(bdf, cycle_date, offset=cost)
                if bp and bp > 0:
                    bench_shares[mkt] += bgt / bp
                    bench_cfs[mkt].append((-bgt, py_date))

        # ── Per-market logic ──────────────────────────────────────────────────
        for mkt in active_mkts:
            port    = region_portfolio[mkt]
            tickers = tickers_by_mkt[mkt]
            _rma    = rma[mkt]

            # ── SMA exit ─────────────────────────────────────────────────────
            for ticker in list(port.keys()):
                df = data_map.get(ticker)
                if df is None:
                    continue
                sub = df.loc[:cycle_date]
                if _breakdown_days(sub) >= sma_days:
                    sell_px = _price_at(sub, cycle_date, offset=-cost)
                    if sell_px and sell_px > 0:
                        region_cash[mkt] += port[ticker]["shares"] * sell_px
                    del port[ticker]

            # ── Variant B: stock-level dip reserve ───────────────────────────
            if indiv_dip and dip_reserve[mkt] > 0:
                dip_targets = []
                for ticker, pos in port.items():
                    df = data_map.get(ticker)
                    if df is None:
                        continue
                    sub = df.loc[:cycle_date]
                    if sub.empty:
                        continue
                    cur_px  = float(sub.iloc[-1]["Close"])
                    avg_cst = pos.get("avg_cost", cur_px)
                    if avg_cst > 0 and (cur_px / avg_cst - 1) <= -dip_threshold:
                        dip_targets.append(ticker)

                if dip_targets:
                    per_ticker = dip_reserve[mkt] / len(dip_targets)
                    for ticker in dip_targets:
                        df     = data_map.get(ticker)
                        sub    = df.loc[:cycle_date]
                        buy_px = _price_at(sub, cycle_date, offset=cost)
                        if not buy_px or buy_px <= 0:
                            continue
                        shares = per_ticker / buy_px
                        prev   = port[ticker]
                        tot_sh = prev["shares"] + shares
                        tot_ct = prev["total_cost"] + per_ticker
                        port[ticker] = {"shares": tot_sh,
                                        "avg_cost": tot_ct / tot_sh,
                                        "total_cost": tot_ct}
                        reserve_deploy_events += 1
                    dip_reserve[mkt] = 0.0

            # ── Variant C: regime-shift reserve ──────────────────────────────
            elif regime_deploy and dip_reserve[mkt] > 0:
                bdf = _bench_map.get(mkt)
                if bdf is not None:
                    idx_close, idx_sma200 = _sma200_of_index(bdf, cycle_date)
                    if not math.isnan(idx_close) and idx_close < idx_sma200:
                        # Index below SMA_200 → market in downtrend → deploy reserve
                        regime_months[mkt] += 1
                        region_cash[mkt] += dip_reserve[mkt]   # move to buyable cash
                        reserve_deploy_events += 1
                        dip_reserve[mkt] = 0.0

            # ── Regular monthly picks ─────────────────────────────────────────
            picks  = _select_picks(data_map, tickers, cycle_date, max_picks,
                                   sma_days, port, periods)
            deploy = min(rb[mkt] * (1.0 - reserve_pct), region_cash[mkt])
            if regime_deploy:
                # When reserve was just released into cash, deploy all available
                deploy = region_cash[mkt]

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

            # ── NAV snapshot ──────────────────────────────────────────────────
            r_nav = region_cash[mkt] + dip_reserve[mkt]
            for tk, pos in port.items():
                df  = data_map.get(tk)
                sub = df.loc[:cycle_date] if df is not None else None
                if sub is not None and not sub.empty:
                    r_nav += pos["shares"] * float(sub.iloc[-1]["Close"])
            region_nav_hist[mkt].append({
                "date": str(py_date), "nav": r_nav,
                "invested": region_invested[mkt],
            })

        nav_history.append({
            "date": str(py_date),
            "nav":  sum(region_nav_hist[m][-1]["nav"] for m in active_mkts),
            "positions": sum(len(region_portfolio[m]) for m in active_mkts),
        })

    # ── Final liquidation ─────────────────────────────────────────────────────
    final_date = end_dt.date()
    region_final: dict[str, float] = {}
    region_gain:  dict[str, float] = {}
    region_benchmark_xirr: dict[str, float] = {}

    for mkt in active_mkts:
        port  = region_portfolio[mkt]
        r_nav = region_cash[mkt] + dip_reserve[mkt]
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
    avg_pos     = sum(h["positions"] for h in nav_history) / len(nav_history) if nav_history else 0
    idle_at_end = {m: round(dip_reserve[m], 2) for m in active_mkts}

    return {
        "label":                 label,
        "region_xirr":           region_xirr,
        "region_benchmark_xirr": region_benchmark_xirr,
        "region_invested":       region_invested,
        "region_final":          region_final,
        "region_gain":           region_gain,
        "idle_at_end":           idle_at_end,
        "max_drawdown":          round(max_dd * 100, 2),
        "n_cycles":              len(monthly_dates),
        "avg_positions":         round(avg_pos, 1),
        "reserve_deploy_events": reserve_deploy_events,
        "regime_months":         regime_months,
        "region_nav_hist":       region_nav_hist,
        "region_symbol":         _SYMBOL,
        "region_currency":       _CURRENCY,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",      default="2016-01-01")
    parser.add_argument("--markets",    default="US,EU,IN")
    parser.add_argument("--top-n",      type=int,   default=20)
    parser.add_argument("--budget-us",  type=float, default=2000.0)
    parser.add_argument("--budget-eu",  type=float, default=2000.0)
    parser.add_argument("--budget-in",  type=float, default=20000.0)
    parser.add_argument("--picks-d",    type=int,   default=8,
                        help="Picks for Variant D — more picks (default 8)")
    args = parser.parse_args()

    markets       = [m.strip().upper() for m in args.markets.split(",")]
    region_budget = {"US": args.budget_us, "EU": args.budget_eu, "IN": args.budget_in}
    YEARS         = 11

    print(f"\n{'='*72}")
    print(f"  SIP Strategy Variants — 4-way Comparison")
    print(f"  Period  : {args.start} to today  |  Markets: {', '.join(markets)}")
    print(f"  Universe: top {args.top_n}/market")
    print(f"  A:  Original (100% deployed, 5 picks)")
    print(f"  B:  Small reserve (5% held, deploy on -10% individual dip)")
    print(f"  C:  Regime reserve (20% held, deploy when index < SMA_200)")
    print(f"  C2: Regime reserve (10% held, deploy when index < SMA_200)")
    print(f"  D:  More picks ({args.picks_d} picks, 100% deployed)")
    print(f"{'='*72}")

    # ── Data ──────────────────────────────────────────────────────────────────
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
            cache = CACHE_DIR / f"{ticker.replace('/','_')}.parquet"
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

    # ── Benchmarks ────────────────────────────────────────────────────────────
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

    # ── Run all 4 variants ────────────────────────────────────────────────────
    print("\n[3/3] Running variants...")

    common = dict(data_map=data_map, benchmark_dfs=benchmark_dfs,
                  start=args.start, region_budget=region_budget, markets=markets)

    print("      A — original...")
    res_a = _run_variant("A: Original (5 picks, 100%)",         **common, max_picks=5)

    print("      B — small reserve (5%, -10% dip trigger)...")
    res_b = _run_variant("B: Small reserve (5%, -10% dip)",     **common, max_picks=5,
                         reserve_pct=0.05, indiv_dip=True,  dip_threshold=0.10)

    print("      C — regime reserve (20%, index SMA_200 break)...")
    res_c = _run_variant("C: Regime reserve (20%, index break)", **common, max_picks=5,
                         reserve_pct=0.20, regime_deploy=True)

    print("      C2 — regime reserve (10%, index SMA_200 break)...")
    res_c2 = _run_variant("C2: Regime reserve (10%, index break)", **common, max_picks=5,
                          reserve_pct=0.10, regime_deploy=True)

    print(f"      D — more picks ({args.picks_d})...")
    res_d = _run_variant(f"D: More picks ({args.picks_d}, 100%)", **common,
                         max_picks=args.picks_d)

    results = [res_a, res_b, res_c, res_c2, res_d]

    # ── Print comparison ──────────────────────────────────────────────────────
    _SYM = {"US": "$", "EU": "€", "IN": "₹"}
    _CUR = {"US": "USD", "EU": "EUR", "IN": "INR"}
    sep  = "=" * 72
    dash = "-" * 60

    def pct(v):
        return f"{v*100:+.2f}%" if not math.isnan(v) else "N/A"

    def best_idx(vals):
        """Index of max value, ignoring NaN."""
        best = -1
        bv   = float("-inf")
        for i, v in enumerate(vals):
            if not math.isnan(v) and v > bv:
                bv, best = v, i
        return best

    # ── Regional XIRR summary table ───────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  XIRR SUMMARY (higher is better)")
    print(f"{sep}")
    hdr = f"  {'Market':<5} {'Benchmark':>11}  {'A Orig':>10} {'B Sm.Rsv':>10} {'C 20%':>10} {'C2 10%':>10} {'D Picks':>10}"
    print(hdr)
    print(f"  {dash}")

    for mkt in markets:
        bx   = res_a["region_benchmark_xirr"].get(mkt, float("nan"))
        xirrs = [r["region_xirr"].get(mkt, float("nan")) for r in results]
        bi   = best_idx(xirrs)
        cols = []
        for i, xi in enumerate(xirrs):
            s = pct(xi)
            cols.append(f"{'>>':>2}{s:<8}" if i == bi else f"  {s:<8}")
        print(f"  {mkt:<5} {pct(bx):>11}  {'  '.join(cols)}")

    # ── Final NAV summary ─────────────────────────────────────────────────────
    print(f"\n  TOTAL GAIN SUMMARY (local currency)")
    print(f"  {dash}")
    hdr2 = f"  {'Market':<5} {'Invested':>12}  {'A Orig':>12} {'B Sm.Rsv':>12} {'C 20%':>12} {'C2 10%':>12} {'D Picks':>12}"
    print(hdr2)
    print(f"  {dash}")
    for mkt in markets:
        sym  = _SYM.get(mkt, "")
        inv  = res_a["region_invested"].get(mkt, 0.0)
        gains = [r["region_gain"].get(mkt, 0.0) for r in results]
        bi   = max(range(len(gains)), key=lambda i: gains[i])
        cols = []
        for i, g in enumerate(gains):
            s = f"{sym}{g:,.0f}"
            cols.append(f"**{s:<10}**" if i == bi else f"  {s:<10}  ")
        print(f"  {mkt:<5} {sym}{inv:>10,.0f}  {'  '.join(cols)}")

    # ── Portfolio stats ───────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  PORTFOLIO STATS")
    print(f"{sep}")
    print(f"  {'Metric':<32} {'A':>10} {'B':>10} {'C':>10} {'C2':>10} {'D':>10}")
    print(f"  {'-'*32} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    print(f"  {'Max Drawdown':<32} "
          + "  ".join(f"{r['max_drawdown']:>+9.2f}%" for r in results))
    print(f"  {'Avg open positions':<32} "
          + "  ".join(f"{r['avg_positions']:>10.1f}" for r in results))
    print(f"  {'Reserve deploy events':<32} "
          + "  ".join(f"{r['reserve_deploy_events']:>10}" for r in results))
    print(f"  {'Regime-down months (US)':<32} "
          + "  ".join(f"{r['regime_months'].get('US', 0):>10}" for r in results))
    print(f"  {'Regime-down months (EU)':<32} "
          + "  ".join(f"{r['regime_months'].get('EU', 0):>10}" for r in results))
    print(f"  {'Regime-down months (IN)':<32} "
          + "  ".join(f"{r['regime_months'].get('IN', 0):>10}" for r in results))

    # Idle reserve at end
    for mkt in markets:
        sym = _SYM.get(mkt, "")
        idles = [f"{sym}{r['idle_at_end'].get(mkt,0):>8,.0f}" for r in results]
        print(f"  {'Idle reserve end — '+mkt:<32} "
              + "  ".join(f"{s:>10}" for s in idles))

    # ── Year-by-year per region ───────────────────────────────────────────────
    from backtest_sip import _year_returns

    print(f"\n{sep}")
    print(f"  YEAR-BY-YEAR RETURNS (Modified Dietz, local currency)")
    print(f"{sep}")

    for mkt in markets:
        sym = _SYM.get(mkt, "")
        cur = _CUR.get(mkt, "")
        bgt = region_budget.get(mkt, 0.0)

        yr_all = [_year_returns(r["region_nav_hist"].get(mkt, []), bgt) for r in results]
        if not yr_all[0]:
            continue

        print(f"\n  {mkt} ({cur})")
        print(f"  {'Year':<6} {'A Ret':>8} {'B Ret':>8} {'C Ret':>8} {'C2 Ret':>8} {'D Ret':>8}  Best  |  {sym}NAV-A  {sym}NAV-B  {sym}NAV-C  {sym}NAV-C2  {sym}NAV-D")
        print(f"  {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}  {'----'}  {'-------'} {'-------'} {'-------'} {'-------'} {'-------'}")

        for idx in range(len(yr_all[0])):
            rows = [yr[idx] for yr in yr_all if idx < len(yr)]
            if not rows:
                continue
            yr   = rows[0]["year"]
            rets = [r["return_pct"] for r in rows]
            navs = [r["nav_end"]    for r in rows]
            bi   = max(range(len(rets)), key=lambda i: rets[i])
            ret_strs = [f"{v:>+7.1f}%"  for v in rets]
            nav_strs = [f"{sym}{v:>8,.0f}" for v in navs]
            best_lbl = ["A","B","C","C2","D"][bi]
            print(f"  {yr:<6} {'  '.join(ret_strs)}  {best_lbl}  |  {'  '.join(nav_strs)}")

    # ── Final verdict ─────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  VERDICT — XIRR wins per variant (across {len(markets)} markets)")
    print(f"{sep}")

    win_counts = [0] * 4
    for mkt in markets:
        xirrs = [r["region_xirr"].get(mkt, float("nan")) for r in results]
        bi = best_idx(xirrs)
        if bi >= 0:
            win_counts[bi] += 1

    labels = ["A (Original)", "B (Small reserve)", "C (Regime 20%)", "C2 (Regime 10%)", "D (More picks)"]
    for i, (lbl, wc) in enumerate(zip(labels, win_counts)):
        bar = "█" * wc
        print(f"  {lbl:<28} {bar}  {wc}/{len(markets)} markets")

    # Best overall by average XIRR rank
    avg_xirrs = []
    for r in results:
        vals = [r["region_xirr"].get(m, float("nan")) for m in markets]
        valid = [v for v in vals if not math.isnan(v)]
        avg_xirrs.append(sum(valid)/len(valid) if valid else float("nan"))

    best_overall = best_idx(avg_xirrs)
    print(f"\n  Average XIRR across markets:")
    for i, (lbl, ax) in enumerate(zip(labels, avg_xirrs)):
        marker = " ◄ BEST" if i == best_overall else ""
        print(f"    {lbl:<28} {pct(ax)}{marker}")

    print(f"\n  Max drawdown comparison (lower absolute = better):")
    for i, (lbl, r) in enumerate(zip(labels, results)):
        print(f"    {lbl:<28} {r['max_drawdown']:+.2f}%")

    print()


if __name__ == "__main__":
    main()
