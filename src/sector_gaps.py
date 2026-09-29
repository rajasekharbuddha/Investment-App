"""
sector_gaps.py
==============
Structural demand-gap catalogue for multi-bagger screening.

Each SectorGap maps a thematic market gap to:
  - keyword patterns for yfinance sector / industry fields
  - explicit ticker overrides (always match)
  - tailwind strength: STRONG / MODERATE / EMERGING

Update the GAP_CATALOGUE when macro conditions shift (typically annually).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SectorGap:
    name: str
    tailwind: str                      # "STRONG" | "MODERATE" | "EMERGING"
    rationale: str                     # one-line why this gap exists now
    industry_keywords: list[str]       # match against yfinance industry (case-insensitive)
    sector_keywords: list[str]         # match against yfinance sector (fallback)
    ticker_overrides: list[str] = field(default_factory=list)


GAP_CATALOGUE: dict[str, list[SectorGap]] = {

    # ── India ─────────────────────────────────────────────────────────────────
    "IN": [
        SectorGap(
            name="Defence Electronics",
            tailwind="STRONG",
            rationale="₹1.7L Cr defence budget; 70% import substitution target by 2028",
            industry_keywords=[
                "aerospace", "defence", "defense", "defense product", "drone",
                "radar", "ordnance", "ammunition", "military", "electronic warfare",
                "missile", "armament",
            ],
            sector_keywords=["Industrials"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="Data Centre Infrastructure",
            tailwind="STRONG",
            rationale="Hyperscaler India builds 2–3yr behind demand; AI compute wave accelerating",
            industry_keywords=[
                "data center", "data centre", "colocation", "network equipment",
                "uninterruptible power", "precision cooling", "server",
                "network infrastructure", "fiber", "fibre",
            ],
            sector_keywords=["Technology", "Industrials"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="Specialty Chemicals",
            tailwind="STRONG",
            rationale="China+1 supply shift; 25–35%/yr volume growth in fluorochemicals and intermediates",
            industry_keywords=[
                "specialty chemical", "agrochemical", "fluorochemical",
                "dye", "pigment", "pharmaceutical intermediate",
                "fine chemical", "polymer", "adhesive", "coating",
            ],
            sector_keywords=["Basic Materials"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="API & CDMO Pharma",
            tailwind="STRONG",
            rationale="Western buyers reducing China API dependency; CDMO capacity oversubscribed",
            industry_keywords=[
                "pharmaceutical", "generic drug", "active pharmaceutical",
                "contract manufacture", "drug manufacture", "biotechnology",
                "api", "injectables",
            ],
            sector_keywords=["Healthcare"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="EV Supply Chain",
            tailwind="MODERATE",
            rationale="2-wheeler EV penetration crossing 10%; cell manufacturing ramp starting",
            industry_keywords=[
                "electric vehicle", "battery", "ev component",
                "auto component", "motor", "battery management",
                "charging infrastructure", "power electronics",
            ],
            sector_keywords=["Consumer Cyclical", "Industrials"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="Capital Goods & Engineering",
            tailwind="MODERATE",
            rationale="India capex supercycle; infrastructure + PLI manufacturing orders expanding",
            industry_keywords=[
                "engineering", "industrial machine", "power equipment",
                "transformer", "switchgear", "pump", "compressor",
                "heavy equipment", "material handling", "conveyor",
            ],
            sector_keywords=["Industrials"],
            ticker_overrides=[],
        ),
    ],

    # ── United States ─────────────────────────────────────────────────────────
    "US": [
        SectorGap(
            name="AI Power Infrastructure",
            tailwind="STRONG",
            rationale="Data centre power demand tripling; grid upgrades required for AI workloads",
            industry_keywords=[
                "electrical equipment", "power component", "ups", "inverter",
                "data center", "semiconductor equipment", "power management",
                "transformer", "switchgear", "cooling", "power semiconductor",
            ],
            sector_keywords=["Technology", "Industrials", "Utilities"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="Nuclear Renaissance",
            tailwind="STRONG",
            rationale="SMR orders accelerating; hyperscalers signing direct nuclear PPAs",
            industry_keywords=[
                "nuclear", "uranium", "reactor", "fuel enrichment",
                "small modular reactor", "smr", "nuclear fuel",
            ],
            sector_keywords=["Energy", "Utilities", "Industrials"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="Defence Autonomy",
            tailwind="STRONG",
            rationale="Counter-drone, autonomous systems, software-defined radio; record DoD budgets",
            industry_keywords=[
                "aerospace", "defense", "autonomous", "drone",
                "cybersecurity", "electronic warfare", "radar",
                "unmanned", "software defined radio",
            ],
            sector_keywords=["Industrials", "Technology"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="GLP-1 Adjacent",
            tailwind="MODERATE",
            rationale="GLP-1 delivery device and CMO capacity shortage; excipient supply constrained",
            industry_keywords=[
                "drug delivery", "contract manufacture", "pharmaceutical",
                "medical device", "diagnostics", "peptide", "biologic",
                "specialty pharma",
            ],
            sector_keywords=["Healthcare"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="Semiconductor Packaging & OSAT",
            tailwind="MODERATE",
            rationale="Advanced packaging (CoWoS, HBM) bottleneck for AI chips; supply tight 2–3yr",
            industry_keywords=[
                "semiconductor", "chip packaging", "osat", "test equipment",
                "semiconductor equipment", "advanced packaging", "hbm",
            ],
            sector_keywords=["Technology"],
            ticker_overrides=[],
        ),
    ],

    # ── Europe ────────────────────────────────────────────────────────────────
    "EU": [
        SectorGap(
            name="Defence Re-armament",
            tailwind="STRONG",
            rationale="NATO 2% GDP target; European ammo, missile, radar component backlog years long",
            industry_keywords=[
                "aerospace", "defense", "defence", "ammunition",
                "missile", "armament", "radar", "electronic warfare",
            ],
            sector_keywords=["Industrials"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="Grid Storage & Smart Grid",
            tailwind="MODERATE",
            rationale="Renewables intermittency; EU grid upgrade mandate and storage capacity gap",
            industry_keywords=[
                "battery storage", "energy storage", "smart grid",
                "grid software", "power management", "inverter",
                "utility scale battery",
            ],
            sector_keywords=["Utilities", "Industrials", "Technology"],
            ticker_overrides=[],
        ),
        SectorGap(
            name="Water Technology",
            tailwind="MODERATE",
            rationale="Mediterranean water stress; EU water framework tightening",
            industry_keywords=[
                "water treatment", "desalination", "wastewater",
                "water utility", "filtration", "water management",
            ],
            sector_keywords=["Utilities", "Industrials"],
            ticker_overrides=[],
        ),
    ],
}


def match_gap(
    ticker: str,
    sector: str,
    industry: str,
    market: str,
) -> Optional[SectorGap]:
    """
    Return the best-matching SectorGap for a stock, or None.

    Priority
    --------
    1. Explicit ticker_overrides match (strongest signal)
    2. Industry keyword match (granular — preferred)
    3. Sector keyword match on STRONG gaps only (broadest — weak signal)
    """
    gaps       = GAP_CATALOGUE.get(market.upper(), [])
    sector_l   = (sector   or "").lower()
    industry_l = (industry or "").lower()

    for gap in gaps:
        if ticker in gap.ticker_overrides:
            return gap

    for gap in gaps:
        if any(kw in industry_l for kw in gap.industry_keywords):
            return gap

    # Sector-only is a weak signal — only return for STRONG tailwind gaps
    for gap in gaps:
        if gap.tailwind == "STRONG":
            if any(kw.lower() in sector_l for kw in gap.sector_keywords):
                return gap

    return None


def all_gaps(market: str) -> list[SectorGap]:
    """Return all SectorGap entries for a market."""
    return GAP_CATALOGUE.get(market.upper(), [])


def gap_summary(market: str) -> str:
    """One-line summary of all gaps for a market."""
    gaps = GAP_CATALOGUE.get(market.upper(), [])
    if not gaps:
        return f"  No gaps defined for {market}"
    lines = [f"\n  Structural gaps — {market.upper()} ({len(gaps)} categories)\n"]
    for g in gaps:
        lines.append(f"  [{g.tailwind:<8}]  {g.name:<30}  {g.rationale}")
    return "\n".join(lines)
