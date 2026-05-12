from pathlib import Path
import difflib
from typing import List
import pandas as pd

from data.lca_interface import LCADataProvider

DEFAULT_DATA_PATH = Path(__file__).parent / "DataSet.xlsx"


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
        
        # Instance-level cache for difflib matching: {label_lower -> dict | None}
        self._match_cache: dict[str, dict | None] = {}

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

    def find_closest_match(self, label: str, location: str = None, threshold: float = 0.5) -> dict | None:
        """Find the closest matching material in the dataset using difflib and substring search."""
        label_lower = label.lower()
        loc_str = str(location).lower() if location else ""
        
        loc_map = {
            "cina": "china",
            "stati uniti": "united states",
            "usa": "united states",
            "united states of america": "united states",
            "europa": "europe",
            "europe": "europe",
            "rer": "europe",
            "mondo": "global",
            "world": "global",
            "globale": "global",
            "global": "global",
            "glo": "global"
        }
        mapped_loc = loc_map.get(loc_str, loc_str)
        
        cache_key = f"{label_lower}_{mapped_loc}"
        if cache_key in self._match_cache:
            return self._match_cache[cache_key]
            
        if self._df.empty:
            return None
            
        df_search = self._df
        if mapped_loc and mapped_loc not in ["not specified", ""]:
            mask_loc = df_search["location"].str.lower() == mapped_loc.lower()
            if not mask_loc.any():
                self._match_cache[cache_key] = None
                return None
            df_search = df_search[mask_loc]
        
        # First, try to find rows where the flowName CONTAINS the label perfectly
        mask = df_search["_flowname_lower"].str.contains(label_lower, regex=False, na=False)
        subset = df_search[mask]
        
        if not subset.empty:
            # Sort by length so we get the shortest match containing the word (e.g. "polypropylene, granulate" vs "waste polypropylene")
            market_subset = subset[subset["processname"].str.contains("market for", case=False, na=False)]
            if not market_subset.empty:
                subset = market_subset
                
            unique_names = subset["_flowname_lower"].unique()
            best_match = min(unique_names, key=len)
            row = subset[subset["_flowname_lower"] == best_match].iloc[0]
        else:
            unique_names = df_search["_flowname_lower"].unique()
            matches = difflib.get_close_matches(label_lower, unique_names, n=1, cutoff=threshold)
            if not matches:
                if mapped_loc and mapped_loc not in ["not specified", ""]:
                    # Do NOT fallback to search without location if location was explicitly required
                    self._match_cache[cache_key] = None
                    return None
                self._match_cache[cache_key] = None
                return None
            best_match = matches[0]
            match_rows = df_search[df_search["_flowname_lower"] == best_match]
            market_rows = match_rows[match_rows["processname"].str.contains("market for", case=False, na=False)]
            if not market_rows.empty:
                row = market_rows.iloc[0]
            else:
                row = match_rows.iloc[0]
            
        result = {
            "index": row.name + 2, # Adding 2 because pandas index is 0-based and excel header is 1
            "id": row["id"],
            "providerName": row["processname"],
            "flowName": row["outputname"],
            "location": row["location"],
            "environmental_impact": float(row["climatechangeimpact"]),
            "is_market": "market" in str(row["processname"]).lower(),
            "energy_mj": self._estimate_energy_mj(row),
            "cost_per_kg": self._estimate_cost_per_kg(row),
        }
        self._match_cache[cache_key] = result
        return result

    def _estimate_cost_per_kg(self, row: pd.Series) -> float:
        name = str(row.get("outputname", "")).lower()
        if "steel" in name or "iron" in name:
            return 0.8
        if "aluminum" in name or "aluminium" in name:
            return 2.5
        if "copper" in name:
            return 6.0
        if "polypropylene" in name or "pp " in name:
            return 1.2
        if "polyethylene" in name or "pe " in name or "hdpe" in name or "ldpe" in name:
            return 1.0
        if "nylon" in name or "polyamide" in name:
            return 3.0
        if "pet " in name or "polyethylene terephthalate" in name:
            return 1.3
        if "wood" in name or "timber" in name:
            return 0.5
        if "glass" in name:
            return 0.7
        if "carbon fiber" in name or "carbon fibre" in name:
            return 20.0
        return 1.0

    def _estimate_energy_mj(self, row: pd.Series) -> float:
        name = str(row.get("outputname", "")).lower()
        if "steel" in name or "iron" in name:
            return 30.0
        if "aluminum" in name or "aluminium" in name:
            return 200.0
        if "copper" in name:
            return 100.0
        if "polypropylene" in name or "pp " in name:
            return 80.0
        if "polyethylene" in name or "pe " in name or "hdpe" in name or "ldpe" in name:
            return 75.0
        if "nylon" in name or "polyamide" in name:
            return 120.0
        if "pet " in name or "polyethylene terephthalate" in name:
            return 85.0
        if "wood" in name or "timber" in name:
            return 15.0
        if "glass" in name:
            return 15.0
        if "carbon fiber" in name or "carbon fibre" in name:
            return 300.0
        return 50.0

    async def get_impact_scores(self, material_id: str) -> dict | None:
        """Return LCA impact scores from DataSet.xlsx. Returns None if material_id is not found."""
        row = self._df[self._df["id"] == material_id]
        if row.empty:
            return None

        r = row.iloc[0]
        cost = self._estimate_cost_per_kg(r)
        return {
            "environmental_impact": float(r["climatechangeimpact"]),
            "is_market": "market" in str(r["processname"]).lower(),
            "energy_mj":            self._estimate_energy_mj(r),
            "cost_tier":            1 if cost < 1.0 else (2 if cost < 3.0 else (3 if cost < 10.0 else 4)),
            "cost_per_kg":          cost,
            "lifespan_years":       10.0,
        }
