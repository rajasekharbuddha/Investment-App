"""
adaptive_tuner.py
=================
STRICT / BASE / SOFT / ULTRA_SOFT state machine.

Signal-density EMA per market drives mode transitions:
  density = ENTER / (ENTER + WAIT + SKIP)
  EMA = alpha * density + (1-alpha) * prev_EMA

  EMA < 2% for 3 consecutive days → loosen one step
  EMA > 6% for 3 consecutive days → tighten one step

No lookahead: today's adjustment applies to tomorrow's scan.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from config import (
    TUNER_MODES, TUNER_PARAMS,
    TUNER_EMA_ALPHA, TUNER_DENSITY_LOOSEN, TUNER_DENSITY_TIGHTEN,
    TUNER_DAYS_TO_TRANSITION,
)


class AdaptiveTuner:
    def __init__(self) -> None:
        self.mode: str = "BASE"
        self._ema: Dict[str, float] = {}
        self._days_low: Dict[str, int] = {}
        self._days_high: Dict[str, int] = {}
        self.history: list = []

    def get_params(self) -> Dict[str, float]:
        return dict(TUNER_PARAMS[self.mode])

    def update(self, market: str, n_enter: int, n_wait: int, n_skip: int) -> Dict[str, Any]:
        total   = n_enter + n_wait + n_skip
        density = n_enter / total if total > 0 else 0.0

        prev_ema = self._ema.get(market, density)
        ema = TUNER_EMA_ALPHA * density + (1.0 - TUNER_EMA_ALPHA) * prev_ema
        self._ema[market] = ema

        if ema < TUNER_DENSITY_LOOSEN:
            self._days_low[market]  = self._days_low.get(market, 0) + 1
            self._days_high[market] = 0
        elif ema > TUNER_DENSITY_TIGHTEN:
            self._days_high[market] = self._days_high.get(market, 0) + 1
            self._days_low[market]  = 0
        else:
            self._days_low[market]  = 0
            self._days_high[market] = 0

        old_mode = self.mode
        idx = TUNER_MODES.index(self.mode)

        if self._days_low.get(market, 0) >= TUNER_DAYS_TO_TRANSITION:
            if idx < len(TUNER_MODES) - 1:
                self.mode = TUNER_MODES[idx + 1]
            self._days_low[market] = 0

        elif self._days_high.get(market, 0) >= TUNER_DAYS_TO_TRANSITION:
            if idx > 0:
                self.mode = TUNER_MODES[idx - 1]
            self._days_high[market] = 0

        changed = self.mode != old_mode
        record = {
            "market": market, "density": round(density, 4),
            "ema": round(ema, 4), "mode": self.mode,
            "old_mode": old_mode, "changed": changed,
        }
        self.history.append(record)
        return record

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode":      self.mode,
            "ema":       self._ema,
            "days_low":  self._days_low,
            "days_high": self._days_high,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AdaptiveTuner":
        t = cls()
        t.mode       = d.get("mode", "BASE")
        t._ema       = d.get("ema", {})
        t._days_low  = d.get("days_low", {})
        t._days_high = d.get("days_high", {})
        return t

    def save(self, path: str = "tuner_state.json") -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str = "tuner_state.json") -> "AdaptiveTuner":
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return cls.from_dict(json.load(f))
            except Exception:
                pass
        return cls()

    def reset(self) -> None:
        self.__init__()
