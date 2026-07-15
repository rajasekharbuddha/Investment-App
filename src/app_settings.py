"""
app_settings.py
================
Shared persisted GUI settings. Both the desktop (app.py) and browser
(app_web.py) editions read/write the same app_settings.json so account
size, risk caps, and universe sizing stay in sync across UIs.
"""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS_FILE = Path(__file__).parent.parent / "app_settings.json"

DEFAULTS: dict = {
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


def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text()))
        except Exception:
            pass
    return s


def save_settings(s: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))
