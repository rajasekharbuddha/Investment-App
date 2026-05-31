#!/usr/bin/env python3
"""
Mastermind Pro — Desktop Application
Run this file to launch the GUI: python app.py
"""

import sys
import platform
import queue
import threading
import re
import json
from pathlib import Path
from datetime import datetime

# Cross-platform monospace font: Menlo on macOS, Consolas on Windows/Linux
_MONO = "Menlo" if platform.system() == "Darwin" else _MONO

import pandas as pd

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print("ERROR: tkinter not found. Reinstall Python and make sure 'tcl/tk' is checked.")
    sys.exit(1)

ROOT = Path(__file__).parent
SRC  = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ── ANSI → Tk tag parser ──────────────────────────────────────────────────────
_ANSI_COLORS = {
    "90": "muted",
    "91": "red", "92": "green", "93": "yellow",
    "94": "blue", "95": "magenta", "96": "cyan", "97": "white",
}
_ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")


def _parse_ansi(text: str):
    pos, fg, bold = 0, "", False
    for m in _ANSI_RE.finditer(text):
        if m.start() > pos:
            yield tuple(t for t in [fg, "bold" if bold else ""] if t), text[pos:m.start()]
        for code in m.group(1).split(";"):
            if code in ("0", ""):
                fg, bold = "", False
            elif code == "1":
                bold = True
            elif code in _ANSI_COLORS:
                fg = _ANSI_COLORS[code]
        pos = m.end()
    if pos < len(text):
        yield tuple(t for t in [fg, "bold" if bold else ""] if t), text[pos:]


# ── Settings helpers ──────────────────────────────────────────────────────────
_SETTINGS_FILE = ROOT / "app_settings.json"
_DEFAULTS = {
    "account_size":          100_000,
    "max_positions":         8,
    "max_per_sector":        8,          # 8/8 = 1.0 → no effective cap for IN (all Unknown sector)
    "max_high_vol":          4,
    "max_position_size_pct": 0.24,       # 24% baseline — Run 17 optimised
    "max_concentration_pct": 0.32,       # 32% ceiling for velocity-scaled leaders
    "quality_filter":        True,
    "dynamic_universe":      True,
    "momentum_exit":         True,
    "vol_penalty":           False,      # disable vol divisor in momentum ranking
    "momentum_grace":        7,          # days after entry before momentum exit can fire
    "momentum_periods":      "14,30,63", # focused momentum periods for early trend detection
    "top_n_us":              200,        # DYNAMIC_UNIVERSE universe fetch size
    "top_n_eu":              200,
    "top_n_in":              250,
    "rank_top_n_us":         10,         # RANKING bench-list top-N per market
    "rank_top_n_eu":         10,
    "rank_top_n_in":         10,
}


def _load_settings() -> dict:
    s = dict(_DEFAULTS)
    if _SETTINGS_FILE.exists():
        try:
            s.update(json.loads(_SETTINGS_FILE.read_text()))
        except Exception:
            pass
    return s


def _save_settings(s: dict):
    _SETTINGS_FILE.write_text(json.dumps(s, indent=2))


# ── Thread → UI bridge ────────────────────────────────────────────────────────
class _QWriter:
    def __init__(self, q: queue.Queue):
        self._q = q

    def write(self, text):
        if text:
            self._q.put(text)

    def flush(self):
        pass


class _TeeWriter:
    """Writes to both a _QWriter (GUI) and a StringIO buffer (for saving)."""
    def __init__(self, qwriter: "_QWriter", buf):
        self._q   = qwriter
        self._buf = buf

    def write(self, text):
        self._q.write(text)
        self._buf.write(text)

    def flush(self):
        pass


# ── Application ───────────────────────────────────────────────────────────────
class App(tk.Tk):
    BG      = "#1e1e2e"
    BG2     = "#181825"
    SURFACE = "#313244"
    TEXT    = "#cdd6f4"
    MUTED   = "#a6adc8"
    ACCENT  = "#89b4fa"
    GREEN   = "#a6e3a1"
    YELLOW  = "#f9e2af"
    RED     = "#f38ba8"
    CYAN    = "#89dceb"
    MAGENTA = "#cba6f7"
    WHITE   = "#ffffff"

    def __init__(self):
        super().__init__()
        self.title("Mastermind Pro — ATR-Dynamic Multi-Market")
        self.geometry("1200x820")
        self.minsize(960, 640)
        self.configure(bg=self.BG)

        self._settings = _load_settings()
        self._busy     = False
        self._q: queue.Queue      = queue.Queue()
        self._target: tk.Text | None = None

        self._styles()
        self._layout()
        self._poll()

    def _styles(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("TNotebook", background=self.BG2, borderwidth=0, tabmargins=0)
        st.configure("TNotebook.Tab",
                     background=self.SURFACE, foreground=self.MUTED,
                     padding=[14, 7], font=(_MONO, 10))
        st.map("TNotebook.Tab",
               background=[("selected", self.BG)],
               foreground=[("selected", self.ACCENT)])
        for orient in ("Vertical", "Horizontal"):
            st.configure(f"{orient}.TScrollbar",
                         background=self.SURFACE, troughcolor=self.BG2,
                         arrowcolor=self.MUTED, borderwidth=0, relief="flat")
        st.configure("TCombobox",
                     fieldbackground=self.SURFACE, background=self.SURFACE,
                     foreground=self.TEXT, arrowcolor=self.ACCENT, borderwidth=0)
        st.map("TCombobox",
               fieldbackground=[("readonly", self.SURFACE)],
               foreground=[("readonly", self.TEXT)])

    def _layout(self):
        hdr = tk.Frame(self, bg=self.BG2, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="MASTERMIND PRO",
                 bg=self.BG2, fg=self.ACCENT,
                 font=(_MONO, 17, "bold"), padx=20).pack(side="left")
        tk.Label(hdr, text="ATR-Dynamic Multi-Market Signal System",
                 bg=self.BG2, fg=self.MUTED,
                 font=(_MONO, 10)).pack(side="left")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        tabs = [
            ("daily",       "  Daily Scan  ",     self._tab_daily),
            ("universe",    "  Universe  ",        self._tab_universe),
            ("posttrade",   "  Post-Trade  ",      self._tab_posttrade),
            ("backtest",    "  Backtest  ",        self._tab_backtest),
            ("longterm",    "  Long-Term  ",       self._tab_longterm),
            ("portfolio",   "  Portfolio  ",       self._tab_portfolio),
            ("reports",     "  Reports  ",         self._tab_reports),
            ("replacement", "  Bench List  ",      self._tab_replacement),
            ("settings",    "  Settings  ",        self._tab_settings),
        ]
        for key, label, builder in tabs:
            f = tk.Frame(nb, bg=self.BG)
            nb.add(f, text=label)
            builder(f)

        self._status = tk.StringVar(value="Ready — select a tab to get started.")
        tk.Label(self, textvariable=self._status,
                 bg=self.BG2, fg=self.MUTED,
                 font=(_MONO, 9), anchor="w", padx=12, pady=4
                 ).pack(fill="x", side="bottom")

    # ──────────────────────────────── TABS ───────────────────────────────────

    def _tab_daily(self, parent):
        bar = tk.Frame(parent, bg=self.BG, padx=14, pady=12)
        bar.pack(fill="x")

        tk.Label(bar, text="Account:", bg=self.BG, fg=self.MUTED,
                 font=(_MONO, 10)).pack(side="left")
        self._acct_lbl = tk.Label(
            bar, text=f"€{self._settings['account_size']:,.0f}",
            bg=self.BG, fg=self.ACCENT, font=(_MONO, 10, "bold"))
        self._acct_lbl.pack(side="left", padx=(4, 20))

        self._daily_markets = self._combo(bar, "Markets:",
                                          ["US,EU,IN", "US", "EU", "IN", "US,EU", "US,IN"],
                                          "US,EU,IN", 10)

        # As-of date: default today; past date → historical simulation (no lookahead)
        self._daily_asof = self._entry(bar, "As of:",
                                       datetime.now().strftime("%Y-%m-%d"), 12)

        # Quality filter checkbox
        self._daily_qf = tk.BooleanVar(value=self._settings.get("quality_filter", True))
        tk.Checkbutton(bar, text="Quality filter", variable=self._daily_qf,
                       bg=self.BG, fg=self.MUTED, selectcolor=self.SURFACE,
                       activebackground=self.BG, activeforeground=self.ACCENT,
                       font=(_MONO, 9)).pack(side="left", padx=(0, 8))

        self._daily_btn = self._button(bar, "▶  Run Daily Scan", self._run_daily)
        self._daily_btn.pack(side="left")
        self._button(bar, "Clear", lambda: self._clear(self._daily_out), w=6
                     ).pack(side="left", padx=(8, 0))

        self._daily_out = self._terminal(parent)

    def _tab_universe(self, parent):
        bar = tk.Frame(parent, bg=self.BG, padx=14, pady=12)
        bar.pack(fill="x")

        tk.Label(bar,
                 text="Score all tickers by momentum velocity, SMA50 trend distance, and composite grade.",
                 bg=self.BG, fg=self.MUTED, font=(_MONO, 9)
                 ).pack(side="left")

        self._univ_btn = self._button(bar, "▶  Score Universe", self._run_universe)
        self._univ_btn.pack(side="right")
        self._button(bar, "Clear", lambda: self._clear(self._univ_out), w=6
                     ).pack(side="right", padx=(0, 8))

        self._univ_out = self._terminal(parent)

    def _tab_posttrade(self, parent):
        bar = tk.Frame(parent, bg=self.BG, padx=14, pady=12)
        bar.pack(fill="x")

        tk.Label(bar,
                 text="Enrich today's WAIT/ENTER/NEAR journal rows with sizing, "
                      "market-behaviour and Tier 3 reflection.",
                 bg=self.BG, fg=self.MUTED, font=(_MONO, 9)
                 ).pack(side="left")

        self._pt_btn = self._button(bar, "▶  Run Post-Trade Analysis", self._run_posttrade)
        self._pt_btn.pack(side="right")
        self._button(bar, "Clear", lambda: self._clear(self._pt_out), w=6
                     ).pack(side="right", padx=(0, 8))

        self._pt_out = self._terminal(parent)

    def _tab_backtest(self, parent):
        bar = tk.Frame(parent, bg=self.BG, padx=14, pady=12)
        bar.pack(fill="x")

        today_str = datetime.now().strftime("%Y-%m-%d")

        self._bt_market = self._combo(bar, "Market:", ["US", "EU", "IN", "ALL"], "ALL", 6)
        self._bt_start  = self._entry(bar, "Start:",  "2021-01-01", 12)
        self._bt_end    = self._entry(bar, "End:",    today_str,    12)

        # Years label — auto-calculated; read-only display
        tk.Label(bar, text="Years:", bg=self.BG, fg=self.MUTED,
                 font=(_MONO, 10)).pack(side="left")
        self._bt_years_lbl = tk.Label(bar, text="—", bg=self.BG, fg=self.ACCENT,
                                      font=(_MONO, 10, "bold"), width=5, anchor="w")
        self._bt_years_lbl.pack(side="left", padx=(4, 16))

        # Trace start/end to auto-update the years label
        def _update_years(*_):
            try:
                s = pd.Timestamp(self._bt_start.get())
                e_raw = self._bt_end.get().strip()
                e = pd.Timestamp(e_raw) if e_raw else pd.Timestamp.today()
                yrs = max(0.0, (e - s).days / 365.25)
                self._bt_years_lbl.configure(text=f"{yrs:.1f}y")
            except Exception:
                self._bt_years_lbl.configure(text="—")

        self._bt_start.trace_add("write", _update_years)
        self._bt_end.trace_add("write",   _update_years)
        _update_years()

        self._bt_btn = self._button(bar, "▶  Run Backtest", self._run_backtest)
        self._bt_btn.pack(side="left", padx=(8, 0))
        self._button(bar, "Clear", lambda: self._clear(self._bt_out), w=6
                     ).pack(side="left", padx=(8, 0))

        self._bt_out = self._terminal(parent)

    def _tab_reports(self, parent):
        pane = tk.PanedWindow(parent, orient="horizontal",
                              bg=self.BG2, sashwidth=3)
        pane.pack(fill="both", expand=True)

        left = tk.Frame(pane, bg=self.BG2, width=220)
        pane.add(left, minsize=180)

        tk.Label(left, text="Saved Reports",
                 bg=self.BG2, fg=self.ACCENT,
                 font=(_MONO, 10, "bold"),
                 padx=8, pady=8).pack(anchor="w")
        self._button(left, "↻  Refresh", self._refresh_reports
                     ).pack(padx=8, pady=(0, 6), anchor="w")

        self._rpt_lb = tk.Listbox(
            left, bg=self.BG, fg=self.TEXT, font=(_MONO, 9),
            selectbackground=self.SURFACE, selectforeground=self.ACCENT,
            relief="flat", bd=0, activestyle="none", highlightthickness=0)
        self._rpt_lb.pack(fill="both", expand=True, padx=2)
        self._rpt_lb.bind("<<ListboxSelect>>", self._view_report)

        right = tk.Frame(pane, bg=self.BG)
        pane.add(right, minsize=440)
        self._rpt_out = self._terminal(right)
        self._refresh_reports()

    def _tab_replacement(self, parent):
        bar = tk.Frame(parent, bg=self.BG, padx=14, pady=12)
        bar.pack(fill="x")

        self._repl_market = self._combo(bar, "Market:", ["ALL", "US", "EU", "IN"], "ALL", 6)
        self._repl_topn   = self._entry(bar, "Top N:", "20", 5)

        self._repl_qf = tk.BooleanVar(value=self._settings.get("quality_filter", True))
        tk.Checkbutton(bar, text="Quality sort", variable=self._repl_qf,
                       bg=self.BG, fg=self.MUTED, selectcolor=self.SURFACE,
                       activebackground=self.BG, activeforeground=self.ACCENT,
                       font=(_MONO, 9)).pack(side="left", padx=(0, 8))

        self._repl_btn = self._button(bar, "▶  Build Bench List", self._run_replacement)
        self._repl_btn.pack(side="left", padx=(8, 0))
        self._button(bar, "Clear", lambda: self._clear(self._repl_out), w=6
                     ).pack(side="left", padx=(8, 0))

        self._repl_out = self._terminal(parent)

    def _tab_longterm(self, parent):
        bar = tk.Frame(parent, bg=self.BG, padx=14, pady=12)
        bar.pack(fill="x")

        self._lt_markets = self._combo(bar, "Markets:",
                                       ["IN", "US,EU,IN", "US", "EU", "US,IN"], "IN", 10)
        self._lt_minq    = self._entry(bar, "Min-Q:", "55", 4)
        self._lt_topn    = self._entry(bar, "Top-N IN:", "250", 5)

        self._lt_near = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="Include NEAR", variable=self._lt_near,
                       bg=self.BG, fg=self.MUTED, selectcolor=self.SURFACE,
                       activebackground=self.BG, activeforeground=self.ACCENT,
                       font=(_MONO, 9)).pack(side="left", padx=(0, 8))

        self._lt_refresh = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text="Refresh cache", variable=self._lt_refresh,
                       bg=self.BG, fg=self.MUTED, selectcolor=self.SURFACE,
                       activebackground=self.BG, activeforeground=self.ACCENT,
                       font=(_MONO, 9)).pack(side="left", padx=(0, 16))

        self._lt_btn = self._button(bar, "▶  Run Long-Term Screen", self._run_longterm)
        self._lt_btn.pack(side="left")
        self._button(bar, "Clear", lambda: self._clear(self._lt_out), w=6
                     ).pack(side="left", padx=(8, 0))

        tk.Label(parent,
                 text="  Technical gates + Q-score pre-screen  ->  Fundamental scoring"
                      " (ROE, growth, D/E, FCF)  ->  Tiered report + Exit Watch per stock",
                 bg=self.BG, fg=self.MUTED, font=(_MONO, 9), anchor="w"
                 ).pack(fill="x", padx=14, pady=(0, 2))

        # ── Backtest control bar ──────────────────────────────────────────────
        sep = tk.Frame(parent, bg=self.SURFACE, height=1)
        sep.pack(fill="x", padx=14, pady=(4, 0))

        bar2 = tk.Frame(parent, bg=self.BG, padx=14, pady=10)
        bar2.pack(fill="x")

        tk.Label(bar2, text="Backtest:", bg=self.BG, fg=self.ACCENT,
                 font=(_MONO, 10, "bold")).pack(side="left", padx=(0, 10))

        today_str = datetime.now().strftime("%Y-%m-%d")
        self._ltbt_market = self._combo(bar2, "Market:", ["IN", "US", "EU"], "IN", 5)
        self._ltbt_start  = self._entry(bar2, "Start:", "2015-01-01", 12)
        self._ltbt_end    = self._entry(bar2, "End:", today_str, 12)
        self._ltbt_slots  = self._entry(bar2, "Slots:", "10", 4)
        self._ltbt_reb    = self._combo(bar2, "Rebalance:",
                                        ["21  (Monthly)", "63  (Quarterly)",
                                         "126 (Semi-Annual)", "252 (Annual)"],
                                        "63  (Quarterly)", 16)

        self._ltbt_mfloor = self._entry(bar2, "Mom.floor%:", "-5", 4)

        self._ltbt_breakdown = tk.BooleanVar(value=True)
        tk.Checkbutton(bar2, text="Breakdown exit", variable=self._ltbt_breakdown,
                       bg=self.BG, fg=self.MUTED, selectcolor=self.SURFACE,
                       activebackground=self.BG, activeforeground=self.ACCENT,
                       font=(_MONO, 9)).pack(side="left", padx=(0, 12))

        self._ltbt_btn = self._button(bar2, "▶  Run LT Backtest", self._run_lt_backtest)
        self._ltbt_btn.pack(side="left")
        self._button(bar2, "Clear", lambda: self._clear(self._lt_out), w=6
                     ).pack(side="left", padx=(8, 0))

        tk.Label(parent,
                 text="  Rebalance = rotate out of laggards.  Breakdown exit = sell on SMA_50 < SMA_200."
                      "  Mom.floor% = exit-watch signal: sell if avg momentum < N% (e.g. -5).  -99 = off.",
                 bg=self.BG, fg=self.MUTED, font=(_MONO, 9), anchor="w",
                 ).pack(fill="x", padx=14, pady=(0, 4))

        self._lt_out = self._terminal(parent)

    def _tab_portfolio(self, parent):
        _PORT_FILE = ROOT / "portfolio" / "positions.json"

        bar = tk.Frame(parent, bg=self.BG, padx=14, pady=12)
        bar.pack(fill="x")

        tk.Label(bar, text="Open Positions — Live P&L",
                 bg=self.BG, fg=self.ACCENT,
                 font=(_MONO, 11, "bold")).pack(side="left")

        self._port_last = tk.Label(bar, text="", bg=self.BG, fg=self.MUTED,
                                   font=(_MONO, 9))
        self._port_last.pack(side="right", padx=(0, 8))

        self._port_btn = self._button(bar, "↻  Refresh Prices", self._run_portfolio)
        self._port_btn.pack(side="right")

        # Global alerts strip (above sub-tabs)
        self._port_alert_lbl = tk.Label(parent, text="",
                                        bg=self.BG2, fg=self.MUTED,
                                        font=(_MONO, 9, "bold"),
                                        anchor="w", padx=14, pady=4)
        self._port_alert_lbl.pack(fill="x")

        # Style Treeview for dark theme
        pstyle = ttk.Style()
        pstyle.configure("Port.Treeview",
                         background=self.BG2, foreground=self.TEXT,
                         fieldbackground=self.BG2, font=(_MONO, 9),
                         rowheight=22, borderwidth=0)
        pstyle.configure("Port.Treeview.Heading",
                         background=self.SURFACE, foreground=self.ACCENT,
                         font=(_MONO, 9, "bold"), relief="flat")
        pstyle.map("Port.Treeview",
                   background=[("selected", self.SURFACE)],
                   foreground=[("selected", self.WHITE)])

        # Inner notebook — 4 regional sub-tabs
        inner_nb = ttk.Notebook(parent)
        inner_nb.pack(fill="both", expand=True, padx=4, pady=2)

        _CURR = {"IN": "Rs", "US": "$", "EU": "€"}
        cols = ("Ticker", "Mkt", "Days", "Entry", "Live", "P&L", "P&L%",
                "R×", "Stop", "Dist%", "Status")
        col_widths = {
            "Ticker": 110, "Mkt": 42, "Days": 42, "Entry": 82, "Live": 82,
            "P&L": 92, "P&L%": 62, "R×": 52, "Stop": 82, "Dist%": 58, "Status": 85,
        }

        # (tab_label, market_key)  — None key = Overview (all regions)
        _SUB_TABS = [
            ("🌍  Overview", None),
            ("🇺🇸  US",      "US"),
            ("🇪🇺  EU",      "EU"),
            ("🇮🇳  IN",      "IN"),
        ]

        self._port_tabs: dict = {}

        for tab_label, market in _SUB_TABS:
            frm = tk.Frame(inner_nb, bg=self.BG)
            inner_nb.add(frm, text=f"  {tab_label}  ")

            smry = tk.Frame(frm, bg=self.BG2, padx=14, pady=5)
            smry.pack(fill="x")

            n_lbl = tk.Label(smry, text="Positions: —",
                             bg=self.BG2, fg=self.TEXT, font=(_MONO, 9))
            n_lbl.pack(side="left", padx=(0, 20))

            cost_lbl = tk.Label(smry, text="Invested: —",
                                bg=self.BG2, fg=self.TEXT, font=(_MONO, 9))
            cost_lbl.pack(side="left", padx=(0, 20))

            pnl_lbl = tk.Label(smry, text="P&L: —",
                               bg=self.BG2, fg=self.MUTED, font=(_MONO, 9))
            pnl_lbl.pack(side="left")

            tv_frame = tk.Frame(frm, bg=self.BG, padx=8, pady=4)
            tv_frame.pack(fill="x")

            tree = ttk.Treeview(tv_frame, columns=cols, show="headings",
                                height=9, style="Port.Treeview",
                                selectmode="browse")
            for c in cols:
                tree.heading(c, text=c)
                tree.column(c, width=col_widths.get(c, 80),
                            anchor="center", stretch=False)

            vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side="left", fill="x", expand=True)
            vsb.pack(side="right", fill="y")

            tree.tag_configure("stop_hit",  foreground=self.RED)
            tree.tag_configure("near_stop", foreground=self.YELLOW)
            tree.tag_configure("safe",      foreground=self.GREEN)
            tree.tag_configure("nodata",    foreground=self.MUTED)

            sep = tk.Frame(frm, bg=self.SURFACE, height=1)
            sep.pack(fill="x", padx=8, pady=(4, 0))

            tk.Label(frm, text="  Per-position details — click Refresh to load live prices",
                     bg=self.BG, fg=self.MUTED, font=(_MONO, 9), anchor="w"
                     ).pack(fill="x", padx=8)

            out = self._terminal(frm)

            self._port_tabs[market] = {
                "n_lbl":    n_lbl,
                "cost_lbl": cost_lbl,
                "pnl_lbl":  pnl_lbl,
                "tree":     tree,
                "out":      out,
            }

        self._port_file = _PORT_FILE
        self._port_curr = _CURR
        self._port_load_static()

    def _port_load_static(self):
        if not self._port_file.exists():
            for tab in self._port_tabs.values():
                tab["n_lbl"].configure(text="Positions: 0  (no positions.json found)")
            return
        try:
            positions = json.loads(self._port_file.read_text())
        except Exception as e:
            for tab in self._port_tabs.values():
                tab["n_lbl"].configure(text=f"Error loading positions: {e}")
            return

        for tab in self._port_tabs.values():
            for item in tab["tree"].get_children():
                tab["tree"].delete(item)

        _CURR = self._port_curr
        today = datetime.now().date()
        mkt_counts: dict = {}

        for pos in positions:
            ticker   = pos.get("ticker", "?")
            market   = pos.get("market", "?")
            entry_px = float(pos.get("entry_price", 0))
            stop     = float(pos.get("stop_loss", 0))
            sym      = _CURR.get(market, "")
            try:
                ed   = datetime.strptime(pos.get("entry_date", ""), "%Y-%m-%d").date()
                days = (today - ed).days
            except Exception:
                days = "?"

            row = (ticker, market, days,
                   f"{sym}{entry_px:.2f}", "—", "—", "—", "—",
                   f"{sym}{stop:.2f}", "—", "—")

            self._port_tabs[None]["tree"].insert("", "end", values=row, tags=("nodata",))
            if market in self._port_tabs:
                self._port_tabs[market]["tree"].insert("", "end", values=row, tags=("nodata",))
            mkt_counts[market] = mkt_counts.get(market, 0) + 1

        n = len(positions)
        mkt_str = "  ".join(f"{m}:{c}" for m, c in sorted(mkt_counts.items()))
        self._port_tabs[None]["n_lbl"].configure(
            text=f"Positions: {n}  [{mkt_str}]  (click Refresh for live prices)")
        self._port_tabs[None]["cost_lbl"].configure(text="Invested: (see regional tabs)")
        self._port_tabs[None]["pnl_lbl"].configure(text="")

        for mkt, tab in self._port_tabs.items():
            if mkt is None:
                continue
            cnt = mkt_counts.get(mkt, 0)
            tab["n_lbl"].configure(
                text=f"Positions: {cnt}  (click Refresh for live prices)")

    def _run_portfolio(self):
        self._port_btn.configure(state="disabled", text="Fetching…")
        self._status.set("Fetching live portfolio prices…")
        threading.Thread(target=self._worker_portfolio, daemon=True).start()

    def _worker_portfolio(self):
        if not self._port_file.exists():
            self.after(0, lambda: self._port_btn.configure(
                state="normal", text="↻  Refresh Prices"))
            self.after(0, lambda: self._status.set("No positions.json found."))
            return

        try:
            positions = json.loads(self._port_file.read_text())
        except Exception as e:
            err = str(e)
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to load positions: {err}"))
            self.after(0, lambda: self._port_btn.configure(
                state="normal", text="↻  Refresh Prices"))
            return

        import yfinance as yf
        tickers = [p["ticker"] for p in positions]
        prices: dict = {}

        try:
            if tickers:
                raw = yf.download(tickers, period="3d", auto_adjust=True,
                                  progress=False, threads=True)
                close = raw["Close"] if "Close" in raw else raw
                for t in tickers:
                    try:
                        col = close[t] if t in close.columns else close.get(t)
                        if col is not None:
                            s = col.dropna()
                            if not s.empty:
                                prices[t] = float(s.iloc[-1])
                    except Exception:
                        pass
        except Exception:
            pass

        for pos in positions:
            t = pos["ticker"]
            if t not in prices:
                try:
                    hist = yf.Ticker(t).history(period="3d", auto_adjust=True)
                    if not hist.empty:
                        prices[t] = float(hist["Close"].iloc[-1])
                except Exception:
                    pass

        self.after(0, lambda: self._update_portfolio_ui(positions, prices))

    def _update_portfolio_ui(self, positions: list, prices: dict):
        for tab in self._port_tabs.values():
            for item in tab["tree"].get_children():
                tab["tree"].delete(item)

        _CURR = self._port_curr
        today = datetime.now().date()
        alerts: list[str] = []
        # detail parts per market key (None = overview)
        all_details: dict = {None: [], "US": [], "EU": [], "IN": []}
        # per-market accumulators
        mkt_acc: dict = {}

        for pos in positions:
            ticker    = pos.get("ticker", "?")
            market    = pos.get("market", "?")
            entry_px  = float(pos.get("entry_price", 0))
            stop      = float(pos.get("stop_loss", 0))
            stop_init = float(pos.get("stop_loss_initial", stop))
            shares    = float(pos.get("shares", 0))
            cost      = float(pos.get("cost", entry_px * shares))
            atr       = float(pos.get("atr_at_entry", 0))
            trail_m   = float(pos.get("trail_mult", 5.5))
            regime    = pos.get("regime", "—")
            sym       = _CURR.get(market, "")

            if market not in mkt_acc:
                mkt_acc[market] = {"cost": 0.0, "pnl": 0.0, "n": 0, "n_priced": 0}
            mkt_acc[market]["n"] += 1
            mkt_acc[market]["cost"] += cost

            try:
                ed   = datetime.strptime(pos.get("entry_date", ""), "%Y-%m-%d").date()
                days = (today - ed).days
            except Exception:
                days = "?"

            cur_px = prices.get(ticker)

            if cur_px is not None:
                pnl       = (cur_px - entry_px) * shares
                pnl_pct   = (cur_px - entry_px) / entry_px * 100
                init_risk  = entry_px - stop_init
                r_mul     = (cur_px - entry_px) / init_risk if init_risk > 0 else float("nan")
                stop_dist  = (cur_px - stop) / cur_px * 100 if cur_px > 0 else 0.0
                mkt_acc[market]["pnl"]      += pnl
                mkt_acc[market]["n_priced"] += 1

                if cur_px <= stop:
                    status = "STOP HIT"; tag = "stop_hit"
                    alerts.append(f"STOP HIT  {ticker}: live {sym}{cur_px:.2f} ≤ stop {sym}{stop:.2f}")
                elif stop_dist < 5.0:
                    status = "NEAR STOP"; tag = "near_stop"
                    alerts.append(f"Near stop  {ticker}: only {stop_dist:.1f}% cushion")
                else:
                    status = "Safe"; tag = "safe"

                r_str    = f"{r_mul:.2f}R" if r_mul == r_mul else "—"
                live_str = f"{sym}{cur_px:.2f}"
                pnl_str  = f"{sym}{pnl:+.0f}"
                pct_str  = f"{pnl_pct:+.1f}%"
                dist_str = f"{stop_dist:.1f}%"

                strategy = pos.get("strategy", "")
                lt_line  = ""
                if strategy == "longterm":
                    lt_line = (f"  LT Score: {pos.get('lt_combined','?')}  "
                               f"Fund: {pos.get('lt_fund_score','?')}  "
                               f"Grade: {pos.get('lt_grade','?')}  "
                               f"[Exit: SMA_200 cross]\n")

                detail = (
                    f"\n{'─'*58}\n"
                    f"  {ticker} ({market})  |  {days} days held  |  {status}"
                    f"{'  [LT]' if strategy == 'longterm' else ''}\n"
                    f"  Entry: {sym}{entry_px:.2f}   Live: {sym}{cur_px:.2f}   Stop: {sym}{stop:.2f}\n"
                    f"  P&L: {sym}{pnl:+.0f} ({pnl_pct:+.1f}%)   R: {r_str}   Cushion: {stop_dist:.1f}%\n"
                    f"  Shares: {shares:.0f}   Cost: {sym}{cost:,.0f}   "
                    f"ATR@entry: {sym}{atr:.2f}   Trail: {trail_m}×   Regime: {regime}\n"
                    + lt_line
                )
            else:
                live_str = pnl_str = pct_str = r_str = dist_str = "—"
                status = "No data"; tag = "nodata"
                detail = f"\n  {ticker}: no live price data available\n"

            row_vals = (ticker, market, days,
                        f"{sym}{entry_px:.2f}", live_str,
                        pnl_str, pct_str, r_str,
                        f"{sym}{stop:.2f}", dist_str, status)

            self._port_tabs[None]["tree"].insert("", "end", values=row_vals, tags=(tag,))
            if market in self._port_tabs:
                self._port_tabs[market]["tree"].insert("", "end", values=row_vals, tags=(tag,))

            all_details[None].append(detail)
            if market in all_details:
                all_details[market].append(detail)

        # Update summary labels — Overview
        n_total = sum(a["n"] for a in mkt_acc.values())
        mkt_str = "  ".join(f"{m}:{a['n']}" for m, a in sorted(mkt_acc.items()))
        ov = self._port_tabs[None]
        ov["n_lbl"].configure(text=f"Positions: {n_total}  [{mkt_str}]")
        ov["cost_lbl"].configure(text="Invested: (see regional tabs)")
        ov["pnl_lbl"].configure(text="")

        # Update summary labels — regional tabs
        for mkt, acc in mkt_acc.items():
            if mkt not in self._port_tabs:
                continue
            tab = self._port_tabs[mkt]
            sym = _CURR.get(mkt, "")
            tab["n_lbl"].configure(text=f"Positions: {acc['n']}")
            tab["cost_lbl"].configure(text=f"Invested: {sym}{acc['cost']:,.0f}")
            if acc["n_priced"] > 0:
                pnl_clr = self.GREEN if acc["pnl"] >= 0 else self.RED
                tab["pnl_lbl"].configure(text=f"P&L: {sym}{acc['pnl']:+,.0f}", fg=pnl_clr)
            else:
                tab["pnl_lbl"].configure(text="P&L: —", fg=self.MUTED)

        for mkt, tab in self._port_tabs.items():
            if mkt is None or mkt in mkt_acc:
                continue
            tab["n_lbl"].configure(text="Positions: 0")
            tab["cost_lbl"].configure(text="Invested: —")
            tab["pnl_lbl"].configure(text="P&L: —", fg=self.MUTED)

        # Alerts strip
        if alerts:
            preview = "  |  ".join(alerts[:3]) + ("  …" if len(alerts) > 3 else "")
            self._port_alert_lbl.configure(
                text=f"  ⚠  {len(alerts)} alert(s): {preview}", fg=self.RED)
        else:
            self._port_alert_lbl.configure(text="  ✓ All positions safe", fg=self.GREEN)

        now_str = datetime.now().strftime("%H:%M:%S")
        self._port_last.configure(text=f"Updated: {now_str}")

        # Populate detail terminals for each sub-tab
        for mkt, tab in self._port_tabs.items():
            parts = all_details.get(mkt, [])
            lbl   = "all positions" if mkt is None else mkt
            self._clear(tab["out"])
            hdr = f"Portfolio — {lbl} — fetched at {now_str}\n{'='*58}\n"
            if alerts and mkt is None:
                hdr += "\nALERTS:\n" + "".join(f"  {a}\n" for a in alerts)
            self._write(tab["out"], hdr + "".join(parts))

        self._port_btn.configure(state="normal", text="↻  Refresh Prices")
        self._status.set(f"Portfolio refreshed — {now_str}")

    def _tab_settings(self, parent):
        canvas = tk.Canvas(parent, bg=self.BG, highlightthickness=0)
        vsb    = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        f = tk.Frame(canvas, bg=self.BG, padx=36, pady=28)
        win = canvas.create_window((0, 0), window=f, anchor="nw")

        def _resize(event):
            canvas.itemconfig(win, width=event.width)
        canvas.bind("<Configure>", _resize)
        f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        tk.Label(f, text="Account & Risk Settings",
                 bg=self.BG, fg=self.ACCENT,
                 font=(_MONO, 13, "bold")
                 ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 18))

        self._sv: dict[str, tk.StringVar] = {}
        self._bv: dict[str, tk.BooleanVar] = {}

        numeric_rows = [
            ("Account Size:",                    "account_size"),
            ("Max Open Positions:",              "max_positions"),
            ("Entry Size Cap (0–1 fraction):",      "max_position_size_pct"),
            ("Max Concentration Cap (0–1 frac):", "max_concentration_pct"),
            ("Max Per Sector (slots):",          "max_per_sector"),
            ("Max High-Vol Per Market:",         "max_high_vol"),
            ("Momentum Exit Grace (days):",      "momentum_grace"),
            ("Momentum Periods (csv):",          "momentum_periods"),
            ("Top-N US (universe fetch):",       "top_n_us"),
            ("Top-N EU (universe fetch):",       "top_n_eu"),
            ("Top-N IN (universe fetch):",       "top_n_in"),
            ("Ranking Top-N US:",                "rank_top_n_us"),
            ("Ranking Top-N EU:",                "rank_top_n_eu"),
            ("Ranking Top-N IN:",                "rank_top_n_in"),
        ]
        for i, (lbl, key) in enumerate(numeric_rows, 1):
            tk.Label(f, text=lbl, bg=self.BG, fg=self.TEXT,
                     font=(_MONO, 10), anchor="e"
                     ).grid(row=i, column=0, sticky="e", padx=(0, 12), pady=5)
            sv = tk.StringVar(value=str(self._settings.get(key, 0)))
            tk.Entry(f, textvariable=sv, width=14,
                     bg=self.SURFACE, fg=self.TEXT, font=(_MONO, 10),
                     insertbackground=self.TEXT, relief="flat", bd=6
                     ).grid(row=i, column=1, sticky="w", pady=5)
            self._sv[key] = sv

        n = len(numeric_rows)

        # Boolean toggles
        bool_rows = [
            ("Enable quality filter:",    "quality_filter"),
            ("Enable dynamic universe:",  "dynamic_universe"),
            ("Enable momentum exit:",     "momentum_exit"),
            ("Volatility penalty:",       "vol_penalty"),
        ]
        for j, (lbl, key) in enumerate(bool_rows):
            tk.Label(f, text=lbl, bg=self.BG, fg=self.TEXT,
                     font=(_MONO, 10), anchor="e"
                     ).grid(row=n + 1 + j, column=0, sticky="e", padx=(0, 12), pady=5)
            bv = tk.BooleanVar(value=bool(self._settings.get(key, False)))
            tk.Checkbutton(f, variable=bv,
                           bg=self.BG, fg=self.TEXT, selectcolor=self.SURFACE,
                           activebackground=self.BG, activeforeground=self.ACCENT
                           ).grid(row=n + 1 + j, column=1, sticky="w", pady=5)
            self._bv[key] = bv

        save_row = n + 1 + len(bool_rows)
        self._button(f, "Save Settings", self._save_settings
                     ).grid(row=save_row, column=1, sticky="w", pady=16)

        tk.Label(f, text="To add/remove stocks edit  src/config.py  directly.",
                 bg=self.BG, fg=self.YELLOW, font=(_MONO, 9)
                 ).grid(row=save_row + 1, column=0, columnspan=2, sticky="w")

        tk.Label(f, text="Watchlist Preview:",
                 bg=self.BG, fg=self.ACCENT, font=(_MONO, 10, "bold")
                 ).grid(row=save_row + 2, column=0, columnspan=2, sticky="w", pady=(20, 6))

        wl = tk.Text(f, height=18, width=72, bg=self.SURFACE, fg=self.TEXT,
                     font=(_MONO, 9), relief="flat", state="disabled",
                     wrap="none", highlightthickness=0)
        wl.grid(row=save_row + 3, column=0, columnspan=2, sticky="ew")
        self._fill_watchlist(wl)

    # ─────────────────────── Widget helpers ──────────────────────────────────

    def _terminal(self, parent) -> tk.Text:
        outer = tk.Frame(parent, bg=self.BG, padx=8, pady=6)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=self.BG2)
        inner.pack(fill="both", expand=True)

        w = tk.Text(inner, bg=self.BG2, fg=self.TEXT, font=(_MONO, 10),
                    wrap="none", relief="flat", bd=0, state="disabled",
                    insertbackground=self.TEXT, highlightthickness=0)
        vsb = ttk.Scrollbar(inner, orient="vertical",   command=w.yview)
        hsb = ttk.Scrollbar(inner, orient="horizontal", command=w.xview)
        w.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        w.grid(row=0, column=0, sticky="nsew")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)

        w.tag_configure("muted",   foreground=self.MUTED)
        w.tag_configure("green",   foreground=self.GREEN)
        w.tag_configure("red",     foreground=self.RED)
        w.tag_configure("yellow",  foreground=self.YELLOW)
        w.tag_configure("blue",    foreground=self.ACCENT)
        w.tag_configure("cyan",    foreground=self.CYAN)
        w.tag_configure("magenta", foreground=self.MAGENTA)
        w.tag_configure("white",   foreground=self.WHITE)
        w.tag_configure("bold",    font=(_MONO, 10, "bold"))
        return w

    def _button(self, parent, text, cmd, w=None) -> tk.Button:
        kw = dict(text=text, command=cmd,
                  bg=self.SURFACE, fg=self.ACCENT,
                  activebackground=self.BG2, activeforeground=self.ACCENT,
                  font=(_MONO, 10), relief="flat", bd=0,
                  padx=12, pady=5, cursor="hand2")
        if w:
            kw["width"] = w
        b = tk.Button(parent, **kw)
        b.bind("<Enter>", lambda _: b.configure(bg=self.BG2))
        b.bind("<Leave>", lambda _: b.configure(bg=self.SURFACE))
        return b

    def _combo(self, parent, label, values, default, width) -> tk.StringVar:
        tk.Label(parent, text=label, bg=self.BG, fg=self.MUTED,
                 font=(_MONO, 10)).pack(side="left")
        sv = tk.StringVar(value=default)
        ttk.Combobox(parent, textvariable=sv, values=values,
                     width=width, state="readonly", font=(_MONO, 10)
                     ).pack(side="left", padx=(4, 16))
        return sv

    def _entry(self, parent, label, default, width) -> tk.StringVar:
        tk.Label(parent, text=label, bg=self.BG, fg=self.MUTED,
                 font=(_MONO, 10)).pack(side="left")
        sv = tk.StringVar(value=default)
        tk.Entry(parent, textvariable=sv, width=width,
                 bg=self.SURFACE, fg=self.TEXT, font=(_MONO, 10),
                 insertbackground=self.TEXT, relief="flat", bd=4
                 ).pack(side="left", padx=(4, 16))
        return sv

    # ─────────────────────── Output helpers ──────────────────────────────────

    def _write(self, widget: tk.Text, text: str):
        widget.configure(state="normal")
        for tags, seg in _parse_ansi(text):
            widget.insert("end", seg, tags)
        widget.see("end")
        widget.configure(state="disabled")

    def _clear(self, widget: tk.Text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                chunk = self._q.get_nowait()
                if self._target:
                    self._write(self._target, chunk)
        except queue.Empty:
            pass
        self.after(40, self._poll)

    def _check_busy(self) -> bool:
        if self._busy:
            messagebox.showinfo("Busy", "A task is already running. Please wait.")
            return True
        return False

    # ─────────────────────────── Actions ─────────────────────────────────────

    def _run_daily(self):
        if self._check_busy():
            return

        asof_raw = self._daily_asof.get().strip()
        try:
            asof_dt = pd.Timestamp(asof_raw).normalize() if asof_raw else pd.Timestamp.today().normalize()
        except Exception:
            messagebox.showerror("Invalid date",
                                 f"As-of date '{asof_raw}' is not a valid YYYY-MM-DD date.")
            return

        self._clear(self._daily_out)
        self._target = self._daily_out
        self._busy   = True
        self._daily_btn.configure(state="disabled", text="Running…")
        self._status.set("Running daily scan…")
        markets    = self._daily_markets.get()
        use_qf     = self._daily_qf.get()
        use_dyn    = self._settings.get("dynamic_universe", False)
        top_n_map  = {
            "US": self._settings.get("top_n_us", 200),
            "EU": self._settings.get("top_n_eu", 200),
            "IN": self._settings.get("top_n_in", 250),
        }
        threading.Thread(
            target=self._worker_daily,
            args=(markets, use_qf, use_dyn, top_n_map, asof_dt),
            daemon=True,
        ).start()

    def _apply_settings_to_config(self) -> None:
        """Patch config module in-process so all workers use GUI values, not hardcoded defaults."""
        import config as cfg
        s = self._settings
        max_pos  = int(s.get("max_positions", 8))
        max_sec  = int(s.get("max_per_sector", 8))
        max_hv   = int(s.get("max_high_vol", 4))
        sec_frac = round(max_sec / max_pos, 4) if max_pos > 0 else 0.55

        cfg.RISK["MAX_OPEN_POSITIONS"]      = max_pos
        cfg.RISK["MAX_PER_SECTOR"]          = {"US": sec_frac, "EU": sec_frac, "IN": sec_frac}
        cfg.RISK["MAX_HIGH_VOL_PER_MARKET"] = {"US": max_hv,   "EU": max_hv,   "IN": max_hv}
        cfg.MAX_OPEN_POSITIONS              = max_pos
        cfg.MAX_PER_SECTOR                  = sec_frac
        cfg.ACCOUNT["equity"]               = float(s.get("account_size", 100_000))

        cfg.RANKING["DEFAULT_TOP_N"] = {
            "US": int(s.get("rank_top_n_us", 10)),
            "EU": int(s.get("rank_top_n_eu", 10)),
            "IN": int(s.get("rank_top_n_in", 10)),
        }
        cfg.RANKING["VOLATILITY_PENALTY"] = bool(s.get("vol_penalty", False))

        raw_periods = str(s.get("momentum_periods", "14,30,63"))
        try:
            periods = [int(p.strip()) for p in raw_periods.split(",") if p.strip()]
            if periods:
                cfg.RANKING["MOMENTUM_PERIODS"] = periods
        except ValueError:
            pass

        cfg.MOMENTUM_EXIT["ENABLED"]    = bool(s.get("momentum_exit", True))
        cfg.MOMENTUM_EXIT["GRACE_DAYS"] = int(s.get("momentum_grace", 7))

        pos_size_pct = float(s.get("max_position_size_pct", 0.24))
        cfg.ACCOUNT["max_position_size"]       = pos_size_pct
        cfg.RISK["MAX_POSITION_SIZE_PCT"]      = pos_size_pct
        cfg.RISK["MAX_TOTAL_CONCENTRATION_PCT"] = float(s.get("max_concentration_pct", 0.32))

        cfg.DYNAMIC_UNIVERSE["SCORE_TOP_N"] = {
            "US": int(s.get("top_n_us", 200)),
            "EU": int(s.get("top_n_eu", 200)),
            "IN": int(s.get("top_n_in", 250)),
        }

    def _worker_daily(self, markets: str, use_qf: bool, use_dyn: bool, top_n_map: dict,
                      asof_dt=None):
        import contextlib
        import pandas as pd
        w = _QWriter(self._q)
        self._apply_settings_to_config()
        try:
            from config import WATCHLIST, WATCHLIST_FLAT, MARKETS, QUALITY_FILTER, DYNAMIC_UNIVERSE
            from data import fetch_all
            from indicators import calculate_all
            from adaptive_tuner import AdaptiveTuner
            from decision_engine import DecisionEngine
            from report import daily_report, portfolio_review_report
            from journal import update_journal
            import json
            from pathlib import Path

            today = (asof_dt if asof_dt is not None
                     else pd.Timestamp.today().normalize())

            acct       = self._settings["account_size"]
            root       = Path(__file__).parent
            tuner_path = root / "tuner_state.json"
            port_path  = root / "portfolio" / "positions.json"

            active_markets = [m.strip().upper() for m in markets.split(",")]

            with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                is_historical = today.date() < pd.Timestamp.today().normalize().date()
                date_note     = f" [historical as-of {today.date()}]" if is_historical else ""
                w.write(f"\n[1/5] Fetching EOD data ({markets}){date_note}...\n")

                # Build full universe first (before fetching data)
                if use_dyn:
                    from universe import get_dynamic_watchlist
                    score_top_n = {
                        m: (top_n_map.get(m) or DYNAMIC_UNIVERSE["SCORE_TOP_N"].get(m, 200))
                        for m in active_markets
                    }
                    w.write(f"  [universe] Downloading index constituents"
                            f" (US={score_top_n.get('US','--')},"
                            f" EU={score_top_n.get('EU','--')},"
                            f" IN={score_top_n.get('IN','--')})...\n")
                    active_wl = get_dynamic_watchlist(
                        active_markets, score_top_n,
                        max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7),
                    )
                else:
                    active_wl = {m: t for m, t in WATCHLIST.items() if m in active_markets}

                raw_data = fetch_all(active_wl, years=3)

                w.write(f"\n[2/5] Calculating indicators for {len(raw_data)} tickers...\n")
                data_map_full = {t: calculate_all(df) for t, df in raw_data.items()}

                # Enforce as-of date: slice data to simulate "no lookahead"
                data_map = {
                    t: df[df.index <= today]
                    for t, df in data_map_full.items()
                    if not df[df.index <= today].empty
                }

                # Rank full dynamic universe down to top-N per market
                if use_dyn:
                    from select_stocks import dynamic_watchlist
                    w.write("  [dynamic] Ranking universe by momentum + quality...\n")
                    active_wl = dynamic_watchlist(data_map, top_n_map, watchlist=active_wl)
                    total_sel = sum(len(v) for v in active_wl.values())
                    w.write(f"  [dynamic] Selected {total_sel} tickers after ranking\n")

                quality_scores: dict = {}
                quality_filtered: list = []
                if use_qf:
                    from select_stocks import quality_score_all, filter_by_quality
                    w.write("  [quality] Scoring universe...\n")
                    quality_scores = quality_score_all(data_map)
                    min_score = QUALITY_FILTER.get("MIN_SCORE", 35)
                    all_tickers_q = [t for tl in active_wl.values() for t in tl]
                    _, quality_filtered = filter_by_quality(all_tickers_q, quality_scores, min_score=min_score)
                    if quality_filtered:
                        w.write(f"  [quality] {len(quality_filtered)} Drag stocks filtered out\n")

                w.write("\n[3/5] Running DecisionEngine.run_day()...\n")
                portfolio = []
                if port_path.exists():
                    try:
                        portfolio = json.loads(port_path.read_text())
                    except Exception:
                        pass

                tuner  = AdaptiveTuner.load(str(tuner_path))
                engine = DecisionEngine(tuner=tuner)

                result = engine.run_day(
                    today=today,
                    data_map=data_map,
                    portfolio=portfolio,
                    equity=acct,
                    context="live",
                    watchlist=active_wl,
                    quality_scores=quality_scores if use_qf else None,
                )

                # Save updated portfolio (held positions + new entries)
                new_portfolio = list(result["held"])
                for entry_info in result["new_entries"] + result["replacement_queue"]:
                    ticker = entry_info["ticker"]
                    close  = (float(data_map[ticker].iloc[-1]["Close"])
                              if ticker in data_map else entry_info.get("price", 0))
                    if entry_info.get("shares", 0) > 0:
                        regime = entry_info.get("regime", {})
                        new_portfolio.append({
                            "ticker":            ticker,
                            "market":            entry_info.get("market", "US"),
                            "sector":            entry_info.get("sector", "Unknown"),
                            "entry_price":       close,
                            "entry_date":        today.strftime("%Y-%m-%d"),
                            "shares":            entry_info.get("shares", 0),
                            "stop_loss":         entry_info.get("stop_price", close * 0.95),
                            "stop_loss_initial": entry_info.get("stop_price", close * 0.95),
                            "trail_mult":        entry_info.get("trail_mult", 5.0),
                            "peak_price":        close,
                            "atr_at_entry":      entry_info.get("atr", 0),
                            "risk_pct":          (regime.get("risk_pct", 0.05)
                                                  if isinstance(regime, dict) else 0.05),
                            "regime":            (regime.get("label", "Normal")
                                                  if isinstance(regime, dict) else "Normal"),
                            "is_high_vol":       entry_info.get("is_high_vol", False),
                            "cost":              entry_info.get("cost", 0),
                        })
                port_path.parent.mkdir(exist_ok=True)
                port_path.write_text(json.dumps(new_portfolio, indent=2))
                n_held = len(result["held"])
                n_new  = len(result["new_entries"])
                n_repl = len(result["replacement_queue"])
                w.write(f"  Portfolio saved: {n_held} held"
                        f"{f', +{n_new} new' if n_new else ''}"
                        f"{f', +{n_repl} queued' if n_repl else ''}\n")

                w.write("\n[4/5] Generating report...\n")
                candidates = list(result["candidates"].values())
                for c in candidates:
                    sz = result["sizing"].get(c["ticker"])
                    if sz:
                        c["sizing"] = sz

                daily_report(
                    decisions=candidates,
                    account_eur=acct,
                    watchlist=active_wl,
                    markets=MARKETS,
                    tuner_mode=result["tuner_mode"],
                    risk_scale=result["risk_scale"],
                    quality_filtered=result.get("quality_filtered", quality_filtered),
                    quality_scores=result.get("quality_scores", quality_scores),
                )
                portfolio_review_report(result, acct)

                w.write("\n[5/5] Updating journal...\n")
                status = update_journal(candidates, WATCHLIST_FLAT, MARKETS)
                w.write(status + "\n")

                tuner.save(str(tuner_path))

        except Exception as exc:
            import traceback
            w.write(f"\n\033[91mError: {exc}\033[0m\n{traceback.format_exc()}")
        finally:
            self._busy = False
            self.after(0, lambda: self._daily_btn.configure(
                state="normal", text="▶  Run Daily Scan"))
            self.after(0, lambda: self._status.set(
                f"Daily scan complete — {datetime.now().strftime('%H:%M:%S')}"))

    def _run_universe(self):
        if self._check_busy():
            return
        self._clear(self._univ_out)
        self._target = self._univ_out
        self._busy   = True
        self._univ_btn.configure(state="disabled", text="Running…")
        self._status.set("Scoring universe…")
        threading.Thread(target=self._worker_universe, daemon=True).start()

    def _worker_universe(self):
        import contextlib
        w = _QWriter(self._q)
        self._apply_settings_to_config()
        try:
            from config import WATCHLIST, MARKETS, DYNAMIC_UNIVERSE
            from data import fetch_all
            from indicators import calculate_all
            from stock_selector import score_all
            from report import quality_report

            use_dyn = self._settings.get("dynamic_universe", DYNAMIC_UNIVERSE.get("ENABLED", False))
            step    = [0]  # mutable counter shared across branches

            def _step(msg: str):
                step[0] += 1
                w.write(f"\n[{step[0]}] {msg}\n")

            with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                if use_dyn:
                    from universe import get_dynamic_watchlist
                    score_top_n = DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {})
                    _step(f"Building dynamic universe "
                          f"(US={score_top_n.get('US','--')}, "
                          f"EU={score_top_n.get('EU','--')}, "
                          f"IN={score_top_n.get('IN','--')})...")
                    active_wl = get_dynamic_watchlist(
                        None, score_top_n,
                        max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7),
                    )
                else:
                    active_wl = WATCHLIST

                total = sum(len(v) for v in active_wl.values())
                _step(f"Fetching EOD data ({total} tickers)...")
                raw = fetch_all(active_wl, years=3)

                _step(f"Calculating indicators for {len(raw)} tickers...")
                data_map = {t: calculate_all(df) for t, df in raw.items()}

                _step("Scoring universe (momentum velocity + SMA50 trend)...")
                scores_df = score_all(data_map)
                quality_report(scores_df, top_n=len(scores_df))

        except Exception as exc:
            import traceback
            w.write(f"\n\033[91mError: {exc}\033[0m\n{traceback.format_exc()}")
        finally:
            self._busy = False
            self.after(0, lambda: self._univ_btn.configure(
                state="normal", text="▶  Score Universe"))
            self.after(0, lambda: self._status.set(
                f"Universe scoring complete — {datetime.now().strftime('%H:%M:%S')}"))

    def _run_posttrade(self):
        if self._check_busy():
            return
        self._clear(self._pt_out)
        self._target = self._pt_out
        self._busy   = True
        self._pt_btn.configure(state="disabled", text="Running…")
        self._status.set("Running post-trade analysis…")
        threading.Thread(target=self._worker_posttrade, daemon=True).start()

    def _worker_posttrade(self):
        import contextlib
        w = _QWriter(self._q)
        try:
            from post_trade import run as pt_run
            with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                pt_run()
        except Exception as exc:
            import traceback
            w.write(f"\n\033[91mError: {exc}\033[0m\n{traceback.format_exc()}")
        finally:
            self._busy = False
            self.after(0, lambda: self._pt_btn.configure(
                state="normal", text="▶  Run Post-Trade Analysis"))
            self.after(0, lambda: self._status.set(
                f"Post-trade complete — {datetime.now().strftime('%H:%M:%S')}"))

    def _run_backtest(self):
        if self._check_busy():
            return

        start = self._bt_start.get().strip()
        end   = self._bt_end.get().strip()

        try:
            pd.Timestamp(start)
        except Exception:
            messagebox.showerror("Invalid date", f"Start date '{start}' is not a valid YYYY-MM-DD date.")
            return

        if end:
            try:
                pd.Timestamp(end)
            except Exception:
                messagebox.showerror("Invalid date", f"End date '{end}' is not a valid YYYY-MM-DD date.")
                return

        self._clear(self._bt_out)
        self._target = self._bt_out
        self._busy   = True
        self._bt_btn.configure(state="disabled", text="Running…")
        self._status.set("Running backtest…")
        market = self._bt_market.get()
        threading.Thread(target=self._worker_bt,
                         args=(market, start, end or None), daemon=True).start()

    def _worker_bt(self, market: str, start: str, end: str | None):
        import contextlib
        import pandas as pd
        w = _QWriter(self._q)
        self._apply_settings_to_config()
        try:
            from config import WATCHLIST, ACCOUNT, DYNAMIC_UNIVERSE
            from backtest import run_backtest
            from report import backtest_report

            acct     = self._settings["account_size"]
            label    = "ALL_MARKETS" if market == "ALL" else f"{market}_only"
            end_disp = end or "today"

            active_markets = ([market] if market != "ALL" else list(WATCHLIST.keys()))
            use_dyn = self._settings.get("dynamic_universe",
                                         DYNAMIC_UNIVERSE.get("ENABLED", False))

            with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                w.write(f"\nBacktesting [{market}]  {start} to {end_disp}"
                        f"  dynamic={'yes' if use_dyn else 'no'}\n")

                if use_dyn:
                    from universe import get_dynamic_watchlist
                    score_top_n = DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {})
                    w.write(f"Building dynamic universe "
                            f"(US={score_top_n.get('US','--')}, "
                            f"EU={score_top_n.get('EU','--')}, "
                            f"IN={score_top_n.get('IN','--')})...\n")
                    watchlist_override = get_dynamic_watchlist(
                        active_markets, score_top_n,
                        max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7),
                    )
                else:
                    watchlist_override = {m: WATCHLIST[m]
                                          for m in active_markets if m in WATCHLIST}

                total_t = sum(len(v) for v in watchlist_override.values())
                w.write(f"Universe: {total_t} tickers across {list(watchlist_override.keys())}\n")

                result = run_backtest(
                    market=market,
                    start=start,
                    end=end,
                    initial_equity=acct,
                    watchlist_override=watchlist_override,
                )
                backtest_report(result, market_label=label)

        except Exception as exc:
            import traceback
            w.write(f"\n\033[91mError: {exc}\033[0m\n{traceback.format_exc()}")
        finally:
            self._busy = False
            self.after(0, lambda: self._bt_btn.configure(
                state="normal", text="▶  Run Backtest"))
            self.after(0, lambda: self._status.set(
                f"Backtest complete — {datetime.now().strftime('%H:%M:%S')}"))

    def _run_replacement(self):
        if self._check_busy():
            return
        self._clear(self._repl_out)
        self._target = self._repl_out
        self._busy   = True
        self._repl_btn.configure(state="disabled", text="Running…")
        self._status.set("Building bench list…")
        market = self._repl_market.get()
        try:
            top_n = int(self._repl_topn.get())
        except ValueError:
            top_n = 20
        use_qf = self._repl_qf.get()
        threading.Thread(target=self._worker_replacement,
                         args=(market, top_n, use_qf), daemon=True).start()

    def _worker_replacement(self, market: str, top_n: int, use_qf: bool):
        import contextlib
        w = _QWriter(self._q)
        self._apply_settings_to_config()
        try:
            from config import WATCHLIST, DYNAMIC_UNIVERSE
            from data import fetch_all
            from indicators import calculate_all
            from adaptive_tuner import AdaptiveTuner
            from replacement_list import build_replacement_list, format_bench_table
            from pathlib import Path

            root    = Path(__file__).parent
            active  = ["US", "EU", "IN"] if market == "ALL" else [market]
            use_dyn = self._settings.get("dynamic_universe", DYNAMIC_UNIVERSE.get("ENABLED", False))

            with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                w.write(f"\nBuilding bench list [{market}]  top_n={top_n}...\n")

                if use_dyn:
                    from universe import get_dynamic_watchlist
                    score_top_n = {m: DYNAMIC_UNIVERSE["SCORE_TOP_N"].get(m, 200) for m in active}
                    w.write("  [universe] Building dynamic universe...\n")
                    wl = get_dynamic_watchlist(
                        active, score_top_n,
                        max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7),
                    )
                else:
                    wl = {m: WATCHLIST[m] for m in active if m in WATCHLIST}

                raw      = fetch_all(wl, years=3)
                data_map = {t: calculate_all(df) for t, df in raw.items()}

                quality_scores: dict = {}
                if use_qf:
                    from select_stocks import quality_score_all
                    w.write("  [quality] Scoring candidates...\n")
                    quality_scores = quality_score_all(data_map)

                tuner = AdaptiveTuner.load(str(root / "tuner_state.json"))

                for mk in active:
                    bench = build_replacement_list(
                        mk, data_map,
                        tuner_mode=tuner.mode,
                        top_n=top_n,
                        quality_scores=quality_scores,
                    )
                    w.write(f"\n{'='*88}\n")
                    w.write(f"  REPLACEMENT LIST -- {mk}  (tuner: {tuner.mode})  {len(bench)} candidates\n")
                    w.write(f"{'='*88}\n")
                    w.write(format_bench_table(bench) + "\n")

        except Exception as exc:
            import traceback
            w.write(f"\n\033[91mError: {exc}\033[0m\n{traceback.format_exc()}")
        finally:
            self._busy = False
            self.after(0, lambda: self._repl_btn.configure(
                state="normal", text="▶  Build Bench List"))
            self.after(0, lambda: self._status.set(
                f"Bench list complete — {datetime.now().strftime('%H:%M:%S')}"))

    def _run_longterm(self):
        if self._check_busy():
            return
        try:
            min_q = int(self._lt_minq.get().strip())
        except ValueError:
            messagebox.showerror("Invalid input", "Min-Q must be an integer (e.g. 55)")
            return
        try:
            top_n = int(self._lt_topn.get().strip())
        except ValueError:
            messagebox.showerror("Invalid input", "Top-N IN must be an integer (e.g. 250)")
            return

        self._clear(self._lt_out)
        self._target = self._lt_out
        self._busy   = True
        self._lt_btn.configure(state="disabled", text="Running...")
        self._status.set("Running long-term screener...")
        threading.Thread(
            target=self._worker_longterm,
            args=(
                self._lt_markets.get(),
                min_q,
                self._lt_near.get(),
                self._lt_refresh.get(),
                top_n,
            ),
            daemon=True,
        ).start()

    def _worker_longterm(self, markets: str, min_q: int, include_near: bool,
                         refresh_cache: bool, top_n_in: int):
        import contextlib, io, re as _re
        w = _QWriter(self._q)
        self._apply_settings_to_config()
        try:
            from run_longterm import run_longterm_screen
            buf = io.StringIO()
            tee = _TeeWriter(w, buf)
            with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
                run_longterm_screen(
                    markets       = markets,
                    min_q         = min_q,
                    include_near  = include_near,
                    refresh_cache = refresh_cache,
                    top_n_in      = top_n_in,
                )
            # Save plain-text copy to reports/
            plain = _re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
            rdir  = ROOT / "reports"
            rdir.mkdir(exist_ok=True)
            fname = rdir / f"longterm-screen-{datetime.now():%Y-%m-%d}-{markets.replace(',','-')}.txt"
            fname.write_text(plain, encoding="utf-8")
            w.write(f"\n  Report saved -> reports/{fname.name}\n")
            self.after(0, self._refresh_reports)
        except Exception as exc:
            import traceback
            w.write(f"\n\033[91mError: {exc}\033[0m\n{traceback.format_exc()}")
        finally:
            self._busy = False
            self.after(0, lambda: self._lt_btn.configure(
                state="normal", text="▶  Run Long-Term Screen"))
            self.after(0, lambda: self._status.set(
                f"Long-term screen complete — {datetime.now().strftime('%H:%M:%S')}"))

    def _run_lt_backtest(self):
        if self._check_busy():
            return

        start = self._ltbt_start.get().strip()
        end   = self._ltbt_end.get().strip()

        try:
            pd.Timestamp(start)
        except Exception:
            messagebox.showerror("Invalid date", f"Start date '{start}' is not valid (use YYYY-MM-DD).")
            return
        if end:
            try:
                pd.Timestamp(end)
            except Exception:
                messagebox.showerror("Invalid date", f"End date '{end}' is not valid (use YYYY-MM-DD).")
                return

        try:
            slots = int(self._ltbt_slots.get().strip())
        except ValueError:
            messagebox.showerror("Invalid input", "Slots must be an integer (e.g. 10)")
            return

        reb_raw = self._ltbt_reb.get()
        try:
            reb_days = int(reb_raw.split()[0])
        except (ValueError, IndexError):
            reb_days = 63

        try:
            mf_pct = float(self._ltbt_mfloor.get().strip())
        except ValueError:
            mf_pct = -5.0
        momentum_floor = mf_pct / 100.0  # -5 -> -0.05

        self._clear(self._lt_out)
        self._target = self._lt_out
        self._busy   = True
        self._ltbt_btn.configure(state="disabled", text="Running...")
        self._status.set("Running long-term backtest...")
        threading.Thread(
            target=self._worker_lt_backtest,
            args=(
                self._ltbt_market.get(),
                start,
                end or pd.Timestamp.today().strftime("%Y-%m-%d"),
                slots,
                reb_days,
                self._ltbt_breakdown.get(),
                momentum_floor,
            ),
            daemon=True,
        ).start()

    def _worker_lt_backtest(self, market: str, start: str, end: str,
                            slots: int, rebalance_days: int, exit_on_breakdown: bool,
                            momentum_floor: float = -0.05):
        import contextlib
        w = _QWriter(self._q)
        self._apply_settings_to_config()
        try:
            from config import WATCHLIST, DYNAMIC_UNIVERSE
            from data import fetch_all
            from indicators import calculate_all
            from backtest_longterm import run_longterm_backtest, longterm_backtest_report

            use_dyn = self._settings.get("dynamic_universe",
                                         DYNAMIC_UNIVERSE.get("ENABLED", False))

            with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                from backtest_longterm import REBALANCE_LABEL
                reb_lbl = REBALANCE_LABEL.get(rebalance_days, f"every {rebalance_days}d")
                mf_disp = (f"{momentum_floor*100:.0f}%"
                           if momentum_floor > -1.0 else "OFF")
                w.write(f"\nLong-Term Backtest  [{market}]  {start} to {end}\n")
                w.write(f"Slots: {slots}  |  Rebalance: {reb_lbl}"
                        f"  |  Breakdown exit: {'ON' if exit_on_breakdown else 'OFF'}"
                        f"  |  Mom.floor: {mf_disp}\n\n")

                if use_dyn:
                    from universe import get_dynamic_watchlist
                    score_top_n = DYNAMIC_UNIVERSE.get("SCORE_TOP_N", {})
                    top_n_mkt   = {market: score_top_n.get(
                                       market, 250 if market == "IN" else 200)}
                    w.write(f"[1/3] Building dynamic universe (top-{top_n_mkt[market]})...\n")
                    wl = get_dynamic_watchlist(
                        [market], top_n_mkt,
                        max_age_days=DYNAMIC_UNIVERSE.get("MAX_AGE_DAYS", 7))
                else:
                    wl = {market: WATCHLIST.get(market, [])}

                total_t = sum(len(v) for v in wl.values())
                w.write(f"[1/3] Universe: {total_t} tickers\n")

                import pandas as pd
                start_ts  = pd.Timestamp(start)
                extra_yrs = max(3, (pd.Timestamp(end) - start_ts).days // 365 + 3)
                w.write(f"[2/3] Fetching price data ({extra_yrs} years of history)...\n")
                raw_data = fetch_all(wl, years=extra_yrs)
                w.write(f"      Loaded {len(raw_data)} tickers\n")

                w.write(f"[3/3] Calculating indicators + running simulation...\n")
                data_map = {t: calculate_all(df) for t, df in raw_data.items()}

                result = run_longterm_backtest(
                    market              = market,
                    data_map            = data_map,
                    start               = start,
                    end                 = end,
                    equity              = float(self._settings.get("account_size", 100_000)),
                    max_positions       = slots,
                    rebalance_days      = rebalance_days,
                    exit_on_breakdown   = exit_on_breakdown,
                    momentum_floor      = momentum_floor,
                )

                report_text = longterm_backtest_report(result)
                w.write(report_text)

                # Save plain-text copy to reports/
                import re as _re
                plain = _re.sub(r"\x1b\[[0-9;]*m", "", report_text)
                rdir  = ROOT / "reports"
                rdir.mkdir(exist_ok=True)
                fname = rdir / f"longterm-backtest-{datetime.now():%Y-%m-%d}-{market}.txt"
                fname.write_text(plain, encoding="utf-8")
                w.write(f"\n  Report saved -> reports/{fname.name}\n")
                self.after(0, self._refresh_reports)

        except Exception as exc:
            import traceback
            w.write(f"\n\033[91mError: {exc}\033[0m\n{traceback.format_exc()}")
        finally:
            self._busy = False
            self.after(0, lambda: self._ltbt_btn.configure(
                state="normal", text="▶  Run LT Backtest"))
            self.after(0, lambda: self._status.set(
                f"LT backtest complete — {datetime.now().strftime('%H:%M:%S')}"))

    def _refresh_reports(self):
        self._rpt_lb.delete(0, "end")
        rd = ROOT / "reports"
        if rd.exists():
            for f in sorted(rd.glob("*.txt"), reverse=True):
                self._rpt_lb.insert("end", f.name)

    def _view_report(self, _=None):
        sel = self._rpt_lb.curselection()
        if not sel:
            return
        path = ROOT / "reports" / self._rpt_lb.get(sel[0])
        if path.exists():
            self._clear(self._rpt_out)
            self._write(self._rpt_out, path.read_text(encoding="utf-8"))

    def _save_settings(self):
        str_keys   = {"momentum_periods"}
        float_keys = {"max_position_size_pct", "max_concentration_pct"}
        try:
            new = {}
            for k, sv in self._sv.items():
                val = sv.get()
                if k in str_keys:
                    new[k] = val
                elif k in float_keys:
                    new[k] = float(val)
                else:
                    new[k] = int(val)
            for k, bv in self._bv.items():
                new[k] = bv.get()
            self._settings = new
            _save_settings(self._settings)
            self._acct_lbl.configure(
                text=f"€{self._settings['account_size']:,.0f}")
            messagebox.showinfo("Saved", "Settings saved successfully.")
        except ValueError:
            messagebox.showerror("Error", "Numeric fields must be valid numbers.")

    def _fill_watchlist(self, widget: tk.Text):
        try:
            from config import WATCHLIST, MARKETS
            widget.configure(state="normal")
            for mk in ["US", "EU", "IN"]:
                m    = MARKETS.get(mk, {})
                note = "" if m.get("tradeable", True) else "  [analysis only]"
                widget.insert("end",
                              f"\n[{mk}] {m.get('name','?')} ({m.get('currency','?')}){note}\n")
                for ticker in WATCHLIST.get(mk, []):
                    widget.insert("end", f"  {ticker}\n")
        except Exception as exc:
            widget.configure(state="normal")
            widget.insert("end", f"Could not read config.py: {exc}\n")
        finally:
            widget.configure(state="disabled")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    App().mainloop()
