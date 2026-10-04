"""
test_semimap.py
===============
Consistency checks for the Semiconductor Dependency Map data files and the
price-refresh helper. No network — quotes are stubbed.
"""

import copy
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from semimap_prices import refresh_prices

NODES = json.loads((ROOT / "semimap" / "nodes.json").read_text(encoding="utf-8"))
PORTS = json.loads((ROOT / "semimap" / "portfolios.json").read_text(encoding="utf-8"))


class TestNodes:
    def test_ids_unique(self):
        ids = [n["id"] for n in NODES["nodes"]]
        assert len(ids) == len(set(ids))

    def test_tiers_and_moats_known(self):
        tiers = {t["tier"] for t in NODES["tiers"]}
        for n in NODES["nodes"]:
            assert n["tier"] in tiers, n["id"]
            assert n["moat"] in NODES["moats"], n["id"]

    def test_every_tier_has_nodes(self):
        for t in NODES["tiers"]:
            assert any(n["tier"] == t["tier"] for n in NODES["nodes"]), t["tier"]

    def test_listed_nodes_have_price_fields(self):
        for n in NODES["nodes"]:
            if n["ticker"]:
                assert n["price"] and n["currency"] and n["exchange"], n["id"]

    def test_partners_exist(self):
        ids = {n["id"] for n in NODES["nodes"]}
        for n in NODES["nodes"]:
            for p in n.get("partners", []):
                assert p in ids, (n["id"], p)


class TestPortfolios:
    @pytest.mark.parametrize("pid", list(PORTS["portfolios"]))
    def test_weights_total_100(self, pid):
        p = PORTS["portfolios"][pid]
        assert sum(p["weights"].values()) + p["core"]["weight"] == pytest.approx(100)

    @pytest.mark.parametrize("pid", list(PORTS["portfolios"]))
    def test_weights_and_core_reference_nodes(self, pid):
        ids = {n["id"] for n in NODES["nodes"]}
        p = PORTS["portfolios"][pid]
        assert set(p["weights"]) <= ids
        assert set(p["core"]["holds"]) <= ids

    def test_default_exists(self):
        assert PORTS["default"] in PORTS["portfolios"]


class TestRefreshPrices:
    def test_updates_shared_ticker_once(self):
        data, calls = copy.deepcopy(NODES), []

        def fetch(t):
            calls.append(t)
            return 100.0

        failed = refresh_prices(data, fetch, today=date(2026, 10, 4))
        assert failed == []
        assert calls.count("005930.KS") == 1 and calls.count("INTC") == 1
        assert all(n["price"] == 100.0 for n in data["nodes"] if n["ticker"])
        assert not any("priceApprox" in n for n in data["nodes"])
        assert data["meta"]["pricesAsOf"] == "2026-10-04"

    def test_failures_keep_old_price(self):
        data = copy.deepcopy(NODES)
        old = {n["id"]: n["price"] for n in data["nodes"]}
        failed = refresh_prices(data, lambda t: None if t == "ASML" else 1.0,
                                today=date(2026, 10, 4))
        assert failed == ["ASML"]
        assert next(n for n in data["nodes"] if n["id"] == "asml")["price"] == old["asml"]
        assert "1 kept from earlier" in data["meta"]["pricesSource"]

    def test_all_failed_keeps_as_of(self):
        data = copy.deepcopy(NODES)
        refresh_prices(data, lambda t: None, today=date(2026, 10, 4))
        assert data["meta"]["pricesAsOf"] == NODES["meta"]["pricesAsOf"]
