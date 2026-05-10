import hashlib
from pathlib import Path
import difflib
from typing import List

import numpy as np
import pandas as pd

from data.lca_interface import LCADataProvider

DEFAULT_DATA_PATH = Path(__file__).parent.parent / "DataSet.xlsx"

# Realistic LCA baselines from material science literature (values per kg).
# More-specific phrases are listed first so they match before generic keywords
# (e.g. "recycled aluminum" wins over bare "aluminum" or "recycled").
BASELINE_LCA_PROFILES = {
    "recycled aluminum": {"environmental_impact": 1.5, "lifespan_years": 25.0},
    "bamboo composite":  {"environmental_impact": 0.8, "lifespan_years": 15.0},
    "recycled steel":    {"environmental_impact": 1.0, "lifespan_years": 30.0},
    "steel":             {"environmental_impact": 2.5, "lifespan_years": 30.0},
    "iron":              {"environmental_impact": 2.5, "lifespan_years": 25.0},
    "aluminum":          {"environmental_impact": 8.0, "lifespan_years": 20.0},
    "aluminium":         {"environmental_impact": 8.0, "lifespan_years": 20.0},
    "wood":              {"environmental_impact": 0.5, "lifespan_years": 15.0},
    "timber":            {"environmental_impact": 0.5, "lifespan_years": 15.0},
    "lumber":            {"environmental_impact": 0.5, "lifespan_years": 15.0},
    "bamboo":            {"environmental_impact": 0.5, "lifespan_years": 12.0},
    "cellulose":         {"environmental_impact": 0.5, "lifespan_years":  5.0},
    "polypropylene":     {"environmental_impact": 3.5, "lifespan_years":  8.0},
    "plastic":           {"environmental_impact": 3.5, "lifespan_years":  8.0},
    "polymer":           {"environmental_impact": 3.5, "lifespan_years":  8.0},
    "pvc":               {"environmental_impact": 3.5, "lifespan_years": 10.0},
    "nylon":             {"environmental_impact": 3.5, "lifespan_years":  7.0},
    "pet":               {"environmental_impact": 3.5, "lifespan_years":  5.0},
    "abs":               {"environmental_impact": 3.5, "lifespan_years":  8.0},
    "recycled":          {"environmental_impact": 1.2, "lifespan_years": 10.0},
    "bio":               {"environmental_impact": 1.0, "lifespan_years":  5.0},
    "hemp":              {"environmental_impact": 1.0, "lifespan_years":  8.0},
    "flax":              {"environmental_impact": 1.0, "lifespan_years":  8.0},
    "cotton":            {"environmental_impact": 1.0, "lifespan_years":  5.0},
    "copper":            {"environmental_impact": 4.5, "lifespan_years": 30.0},
    "brass":             {"environmental_impact": 4.5, "lifespan_years": 25.0},
    "glass":             {"environmental_impact": 1.5, "lifespan_years": 20.0},
    "concrete":          {"environmental_impact": 0.9, "lifespan_years": 40.0},
    "cement":            {"environmental_impact": 0.9, "lifespan_years": 40.0},
    "electricity":       {"environmental_impact": 0.5, "lifespan_years":  0.0},
    "heat":              {"environmental_impact": 0.5, "lifespan_years":  0.0},
    "steam":             {"environmental_impact": 0.5, "lifespan_years":  0.0},
}

# Fallback when no keyword matches: generic plastic profile.
_DEFAULT_PROFILE = {
    "environmental_impact": 3.5, "lifespan_years": 8.0,
}

def _derive_impact(material_id: str, flow_name: str) -> dict:
    """
    Return deterministic impact scores by matching flow_name against
    BASELINE_LCA_PROFILES keywords, then applying a ±5% supplier variance
    seeded by material_id so results are reproducible across runs.
    """
    name_lower = flow_name.lower()
    profile = _DEFAULT_PROFILE

    for keyword, candidate in BASELINE_LCA_PROFILES.items():
        if keyword in name_lower:
            profile = candidate
            break

    digest = int(hashlib.md5(material_id.encode()).hexdigest(), 16)
    noise = ((digest % 1000) / 1000.0 - 0.5) * 0.10  # ±5%

    return {
        "environmental_impact": round(profile["environmental_impact"] * (1 + noise), 3),
        "energy_mj":      0.0,
        "water_l":        0.0,
        "cost_tier":      0,
        "cost_per_kg":    0.0,
        "lifespan_years": profile["lifespan_years"],
    }


class CSVLcaClient(LCADataProvider):
    """LCA data provider backed by a local Excel file containing LCA scores."""

    def __init__(self, data_path: Path = DEFAULT_DATA_PATH) -> None:
        self._path = Path(data_path)
        if not self._path.exists():
            raise FileNotFoundError(f"LCA Dataset not found: {self._path}")
        
        self._df = pd.read_excel(self._path, dtype=str).fillna("")
        self._df.columns = [c.strip().lower() for c in self._df.columns]
        self._validate_schema()

        # Pre-compute lowercase flowname column for fast vectorized search.
        self._df["_flowname_lower"] = self._df["outputname"].str.lower()

        # Convert climatechangeimpact to float
        self._df["climatechangeimpact"] = pd.to_numeric(self._df["climatechangeimpact"], errors="coerce").fillna(0.0)

        # Simple instance-level query cache: {cache_key -> list[dict]}
        self._search_cache: dict[str, list[dict]] = {}

    def _validate_schema(self) -> None:
        required = {"id", "processname", "outputname", "location", "climatechangeimpact"}
        missing = required - set(self._df.columns)
        if missing:
            raise ValueError(f"Dataset is missing required columns: {missing}")

    async def search_materials(
        self, query: str, location: str = "Global"
    ) -> List[dict]:
        cache_key = f"{query.lower()}:{location.lower()}"
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        mask = self._df["_flowname_lower"].str.contains(
            query.lower(), na=False, regex=False
        )
        if location.lower() != "global":
            mask &= self._df["location"].str.contains(location, case=False, na=False)

        results = (
            self._df[mask]
            .head(5)
            .rename(columns={
                "id":           "id",
                "processname":  "providerName",
                "outputname":   "flowName",
                "location":     "location",
            })
            .to_dict(orient="records")
        )

        self._search_cache[cache_key] = results
        return results

    def find_closest_match(self, label: str, threshold: float = 0.5) -> dict | None:
        """Find the closest matching material in the dataset using difflib."""
        if self._df.empty:
            return None
        
        # Get all unique flow names in lowercase for matching
        unique_names = self._df["_flowname_lower"].unique()
        matches = difflib.get_close_matches(label.lower(), unique_names, n=1, cutoff=threshold)
        
        if matches:
            best_match = matches[0]
            # Get the first row matching this outputname
            row = self._df[self._df["_flowname_lower"] == best_match].iloc[0]
            return {
                "id": row["id"],
                "providerName": row["processname"],
                "flowName": row["outputname"],
                "location": row["location"],
                "environmental_impact": float(row["climatechangeimpact"])
            }
        return None

    async def get_impact_scores(self, material_id: str) -> dict:
        row = self._df[self._df["id"] == material_id]
        if row.empty:
            # Fall back to profile-derived scores for unknown / LLM-generated names.
            return _derive_impact(material_id, material_id)

        r = row.iloc[0]
        return {
            "environmental_impact": float(r["climatechangeimpact"]),
            "is_market": "market" in str(r["processname"]).lower(),
            "energy_mj":            0.0,
            "water_l":              0.0,
            "cost_tier":            0,
            "cost_per_kg":          0.0,
            "lifespan_years":       10.0,
        }
