"""
compare_sip_dip_reserve.py
==========================
Tests a "dip-reserve" SIP variant against the original.

Logic under test
----------------
  Each month, instead of deploying 100% of regional budget:
    - 80% → deployed into top momentum picks (normal SIP)
    - 20% → parked as "dip reserve" in the broker account

  Dip-deploy rule (checked BEFORE each month's regular buys):
    For every held position:
      if current_price < avg_cost * (1 - dip_threshold)   [default: -20%]
        → buy more of that stock using the dip reserve
        → reserve is split equally across all dip-eligible positions

  Dip reserve accumulates month to month if unused.
  SMA exit (10 days below SMA_200) still clears positions regardless.

Variants compared
-----------------
  A — Original   : 100% deployed each month, no dip reserve
  B — Dip reserve: 80% deployed + 20% parked; dip-buy on -20% drops

Run:
  python src/compare_sip_dip_reserve.py --top-n 20 --start 2016-01-01
"""
from __future__ import annotations

import argparse, math, os, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent

# ── Variant runner ────────────────────────────────────────────────────────────

def _run_variant(
    data_map:         dict,
    benchmark_dfs:    dict,
    start:            str,
    region_budget:    dict,
    max_picks:        int,
    markets:          list,
    dip_reserve_pct:  float = 0.0,   # 0.0 = original; 0.20 = new variant
    dip_threshold:    float = 0.20,  # buy more when down this much from avg_cost
) -> dict:
    from backtest_sip import (
        _momentum, _breakdown_days, _price_at, _get_market,
        _max_drawdown, _xirr, _year_returns, _DEFAULT_PERIODS, _select_picks,
    )
    import numpy as np
    import pandas as pd

    periods     = _DEFAULT_PERIODS
    cost        = 0.001 + 0.001
    sma_days    = 10
    active_mkts = [m.upper() for m in markets]

    _CURRENCY = {"US": "USD", "EU": "EUR", "IN": "INR"}
    _SYMBOL   = {"US": "$",   "EU": "€",   "IN": "₹"}
    _DEFAULT_RMA = {"US": 200.0, "EU": 200.0, "IN": 2000.0}

    rb  = {m: region_budget.get(m, 2000.0)  for m in active_mkts}
    rma = {m: _DEFAULT_RMA.get(m, 200.0)    for m in active_mkts}

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

    # ── State per region ──────────────────────────────────────────────────────
    region_portfolio: dict[str, dict]  = {m: {} for m in active_mkts}
    region_cash:      dict[str, float] = {m: 0.0 for m in active_mkts}
    dip_reserve:      dict[str, float] = {m: 0.0 for m in active_mkts}  # NEW
    region_invested:  dict[str, float] = {m: 0.0 for m in active_mkts}
    region_cfs:       dict[str, list]  = {m: [] for m in active_mkts}
    region_nav_hist:  dict[str, list]  = {m: [] for m in active_mkts}

    bench_shares: dict[str, float] = {m: 0.0 for m in active_mkts}
    bench_cfs:    dict[str, list]  = {m: [] for m in active_mkts}

    nav_history: list[dict] = []

    # Stats for the dip variant
    dip_events:     int = 0  # times reserve was deployed
    dip_amount_tot: float = 0.0
    reserve_idle:   list[float] = []  # snapshot of idle reserve each cycle

    for cycle_date in monthly_dates:
        py_date = cycle_date.date()

        for mkt in active_mkts:
            bgt        = rb[mkt]
            deploy_bgt = bgt * (1.0 - dip_reserve_pct)   # 80% or 100%
            reserve    = bgt * dip_reserve_pct            # 20% or 0%

            region_cash[mkt]     += deploy_bgt
            dip_reserve[mkt]     += reserve
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

            # ── Step 1: SMA breakdown exits ───────────────────────────────────
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

            # ── Step 2: Dip-reserve deployment ────────────────────────────────
            if dip_reserve_pct > 0 and dip_reserve[mkt] > 0:
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
                    alloc_per_dip = dip_reserve[mkt] / len(dip_targets)
                    for ticker in dip_targets:
                        df     = data_map.get(ticker)
                        if df is None:
                            continue
                        sub    = df.loc[:cycle_date]
                        buy_px = _price_at(sub, cycle_date, offset=cost)
                        if not buy_px or buy_px <= 0:
                            continue
                        shares = alloc_per_dip / buy_px
                        prev   = port[ticker]
                        tot_sh = prev["shares"] + shares
                        tot_ct = prev["total_cost"] + alloc_per_dip
                        port[ticker] = {"shares": tot_sh,
                                        "avg_cost": tot_ct / tot_sh,
                                        "total_cost": tot_ct}
                        dip_events     += 1
                        dip_amount_tot += alloc_per_dip
                    dip_reserve[mkt] = 0.0  # fully deployed

                reserve_idle.append(dip_reserve[mkt])

            # ── Step 3: Regular monthly picks ─────────────────────────────────
            picks  = _select_picks(data_map, tickers, cycle_date, max_picks,
                                   sma_days, port, periods)
            deploy = min(rb[mkt] * (1 - dip_reserve_pct), region_cash[mkt])
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

            # ── NAV snapshot (cash + dip_reserve + positions) ─────────────────
            region_nav = region_cash[mkt] + dip_reserve[mkt]
            for tk, pos in port.items():
                df  = data_map.get(tk)
                sub = df.loc[:cycle_date] if df is not None else None
                if sub is not None and not sub.empty:
                    region_nav += pos["shares"] * float(sub.iloc[-1]["Close"])
            region_nav_hist[mkt].append({
                "date": str(py_date), "nav": region_nav,
                "invested": region_invested[mkt],
                "dip_reserve": dip_reserve[mkt],
            })

        nav_history.append({
            "date":      str(py_date),
            "nav":       sum(region_nav_hist[m][-1]["nav"] for m in active_mkts),
            "positions": sum(len(region_portfolio[m]) for m in active_mkts),
        })

    # ── Final liquidation ─────────────────────────────────────────────────────
    final_date = end_dt.date()
    region_final: dict[str, float] = {}
    region_gain:  dict[str, float] = {}
    region_benchmark_xirr: dict[str, float] = {}
    region_idle_reserve: dict[str, float] = {}

    for mkt in active_mkts:
        port  = region_portfolio[mkt]
        r_nav = region_cash[mkt] + dip_reserve[mkt]  # idle reserve is part of NAV
        for ticker, pos in port.items():
            df  = data_map.get(ticker)
            sub = df.loc[:end_dt] if df is not None else None
            if sub is not None and not sub.empty:
                r_nav += pos["shares"] * float(sub.iloc[-1]["Close"])
        region_final[mkt]        = round(r_nav, 2)
        region_gain[mkt]         = round(r_nav - region_invested[mkt], 2)
        region_idle_reserve[mkt] = round(dip_reserve[mkt], 2)
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
    avg_idle    = sum(reserve_idle) / len(reserve_idle) if reserve_idle else 0.0

    return {
        "region_xirr":           region_xirr,
        "region_benchmark_xirr": region_benchmark_xirr,
        "region_invested":       region_invested,
        "region_final":          region_final,
        "region_gain":           region_gain,
        "region_idle_reserve":   region_idle_reserve,
        "max_drawdown":          round(max_dd * 100, 2),
        "n_cycles":              len(monthly_dates),
        "avg_positions":         round(avg_pos, 1),
        "dip_events":            dip_events,
        "dip_amount_total":      round(dip_amount_tot, 2),
        "avg_idle_reserve":      round(avg_idle, 2),
        "region_nav_hist":       region_nav_hist,
        "region_symbol":         _SYMBOL,
        "region_currency":       _CURRENCY,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",         default="2016-01-01")
    parser.add_argument("--markets",       default="US,EU,IN")
    parser.add_argument("--top-n",         type=int,   default=20)
    parser.add_argument("--max-picks",     type=int,   default=5)
    parser.add_argument("--budget-us",     type=float, default=2000.0)
    parser.add_argument("--budget-eu",     type=float, default=2000.0)
    parser.add_argument("--budget-in",     type=float, default=20000.0)
    parser.add_argument("--reserve-pct",   type=float, default=0.20,
                        help="Fraction of monthly budget to park as dip reserve (default 0.20 = 20%%)")
    parser.add_argument("--dip-threshold", type=float, default=0.20,
                        help="Deploy reserve when stock drops this much from avg_cost (default 0.20 = 20%%)")
    args = parser.parse_args()

    markets       = [m.strip().upper() for m in args.markets.split(",")]
    region_budget = {"US": args.budget_us, "EU": args.budget_eu, "IN": args.budget_in}
    YEARS         = 11

    print(f"\n{'='*70}")
    print(f"  SIP Dip-Reserve Comparison")
    print(f"  Period : {args.start} to today  |  Markets: {', '.join(markets)}")
    print(f"  Reserve: {args.reserve_pct*100:.0f}% of monthly budget parked as dry powder")
    print(f"  Trigger: deploy reserve when stock drops {args.dip_threshold*100:.0f}% below avg cost")
    print(f"{'='*70}")

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

    # ── Run both variants ─────────────────────────────────────────────────────
    print("\n[3/3] Running variants...")

    common = dict(data_map=data_map, benchmark_dfs=benchmark_dfs,
                  start=args.start, region_budget=region_budget,
                  max_picks=args.max_picks, markets=markets)

    print("      Variant A — original (100% deployed, no reserve)...")
    res_a = _run_variant(**common, dip_reserve_pct=0.0)

    print(f"      Variant B — dip reserve ({args.reserve_pct*100:.0f}% held back, deployed on -{args.dip_threshold*100:.0f}% drops)...")
    res_b = _run_variant(**common, dip_reserve_pct=args.reserve_pct,
                         dip_threshold=args.dip_threshold)

    # ── Print results ─────────────────────────────────────────────────────────
    _SYM = {"US":"$","EU":"€","IN":"₹"}
    _CUR = {"US":"USD","EU":"EUR","IN":"INR"}
    sep  = "=" * 70
    dash = "-" * 50

    def pct(v):
        return f"{v*100:+.2f}%" if not math.isnan(v) else "N/A"

    def delta_str(a, b):
        if math.isnan(a) or math.isnan(b):
            return "N/A"
        d = b - a
        return f"{d*100:+.2f}pp"

    print(f"\n{sep}")
    print(f"  REGIONAL RESULTS")
    print(f"{sep}")

    for mkt in markets:
        sym  = _SYM.get(mkt, "")
        cur  = _CUR.get(mkt, "")
        xa   = res_a["region_xirr"].get(mkt, float("nan"))
        xb   = res_b["region_xirr"].get(mkt, float("nan"))
        bx   = res_a["region_benchmark_xirr"].get(mkt, float("nan"))
        fa   = res_a["region_final"].get(mkt, 0.0)
        fb   = res_b["region_final"].get(mkt, 0.0)
        ga   = res_a["region_gain"].get(mkt, 0.0)
        gb   = res_b["region_gain"].get(mkt, 0.0)
        inv  = res_a["region_invested"].get(mkt, 0.0)
        idle = res_b["region_idle_reserve"].get(mkt, 0.0)

        winner = "B (dip reserve)" if xb > xa else "A (original)" if xa > xb else "TIE"

        print(f"\n  {mkt} ({cur})  |  benchmark XIRR: {pct(bx)}  |  Winner: {winner}")
        print(f"  {dash}")
        print(f"  {'Metric':<30} {'Original':>12} {'Dip Reserve':>12} {'Delta':>10}")
        print(f"  {'-'*30} {'-'*12} {'-'*12} {'-'*10}")
        print(f"  {'XIRR':<30} {pct(xa):>12} {pct(xb):>12} {delta_str(xa,xb):>10}")
        print(f"  {'Final NAV':<30} {sym+f'{fa:,.0f}':>12} {sym+f'{fb:,.0f}':>12} {sym+f'{fb-fa:+,.0f}':>10}")
        print(f"  {'Total Gain':<30} {sym+f'{ga:,.0f}':>12} {sym+f'{gb:,.0f}':>12} {sym+f'{gb-ga:+,.0f}':>10}")
        print(f"  {'Gain % on invested':<30} {ga/inv*100:>+11.1f}% {gb/inv*100:>+11.1f}% {(gb-ga)/inv*100:>+9.1f}%")
        print(f"  {'Idle reserve at end':<30} {'—':>12} {sym+f'{idle:,.0f}':>12}")

    print(f"\n{sep}")
    print(f"  PORTFOLIO-LEVEL STATS")
    print(f"{sep}")
    print(f"  {'Metric':<35} {'Original':>12} {'Dip Reserve':>12}")
    print(f"  {'-'*35} {'-'*12} {'-'*12}")
    print(f"  {'Max Drawdown':<35} {res_a['max_drawdown']:>+11.2f}% {res_b['max_drawdown']:>+11.2f}%")
    print(f"  {'Avg open positions':<35} {res_a['avg_positions']:>12.1f} {res_b['avg_positions']:>12.1f}")
    print(f"  {'Total cycles':<35} {res_a['n_cycles']:>12} {res_b['n_cycles']:>12}")
    print(f"  {'Dip-deploy events':<35} {'—':>12} {res_b['dip_events']:>12}")
    print(f"  {'Total capital dip-deployed':<35} {'—':>12} {res_b['dip_amount_total']:>12,.0f}")
    print(f"  {'Avg idle reserve / cycle':<35} {'—':>12} {res_b['avg_idle_reserve']:>12,.1f}")

    # ── Year-by-year for each region ──────────────────────────────────────────
    from backtest_sip import _year_returns
    print(f"\n{sep}")
    print(f"  YEAR-BY-YEAR (combined NAV, Modified Dietz)")
    print(f"{sep}")

    for mkt in markets:
        sym  = _SYM.get(mkt, "")
        cur  = _CUR.get(mkt, "")
        bgt  = region_budget.get(mkt, 0.0)
        yra  = _year_returns(res_a["region_nav_hist"].get(mkt, []), bgt)
        yrb  = _year_returns(res_b["region_nav_hist"].get(mkt, []), bgt)
        if not yra:
            continue
        print(f"\n  {mkt} ({cur})")
        print(f"  {'Year':<6} {'Orig NAV':>12} {'Orig Ret':>9} {'Dip NAV':>12} {'Dip Ret':>9} {'Delta':>8}")
        print(f"  {'-'*6} {'-'*12} {'-'*9} {'-'*12} {'-'*9} {'-'*8}")
        for ra, rb_ in zip(yra, yrb):
            yr = ra["year"]
            diff = rb_["return_pct"] - ra["return_pct"]
            flag = " <" if diff < -1 else " >" if diff > 1 else ""
            print(f"  {yr:<6} "
                  f"{sym}{ra['nav_end']:>10,.0f}  "
                  f"{ra['return_pct']:>+8.1f}%  "
                  f"{sym}{rb_['nav_end']:>10,.0f}  "
                  f"{rb_['return_pct']:>+8.1f}%  "
                  f"{diff:>+7.1f}pp{flag}")

    # ── Verdict ───────────────────────────────────────────────────────────────
    wins_b = sum(
        1 for m in markets
        if not math.isnan(res_b["region_xirr"].get(m, float("nan")))
        and not math.isnan(res_a["region_xirr"].get(m, float("nan")))
        and res_b["region_xirr"][m] > res_a["region_xirr"][m]
    )

    total_gain_a = sum(res_a["region_gain"].get(m, 0) for m in markets)
    total_gain_b = sum(res_b["region_gain"].get(m, 0) for m in markets)

    print(f"\n{sep}")
    print(f"  VERDICT")
    print(f"{sep}")
    print(f"  Markets where dip reserve wins  : {wins_b}/{len(markets)}")
    print(f"  Max DD change                   : {res_b['max_drawdown']-res_a['max_drawdown']:+.2f}pp "
          f"({'worse' if res_b['max_drawdown'] < res_a['max_drawdown'] else 'better'})")
    print(f"  Dip events fired                : {res_b['dip_events']}  "
          f"(avg idle reserve: {res_b['avg_idle_reserve']:,.0f} / cycle)")
    if res_b["dip_events"] == 0:
        print(f"  NOTE: Dip reserve was NEVER deployed — the -{args.dip_threshold*100:.0f}% threshold")
        print(f"        was never triggered. Try --dip-threshold 0.10 or 0.15.")
    print(f"\n  RECOMMENDATION: "
          f"{'Adopt dip reserve' if wins_b == len(markets) and res_b['max_drawdown'] >= res_a['max_drawdown'] else 'Keep original strategy'}"
          f" — see year-by-year for where the difference is largest.\n")


if __name__ == "__main__":
    main()
