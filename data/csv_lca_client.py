from pathlib import Path
import difflib
from typing import List, Optional, Tuple
import pandas as pd

from data.lca_interface import LCADataProvider

DEFAULT_DATA_PATH = Path(__file__).parent / "DataSet.xlsx"

# ---------------------------------------------------------------------------
# Geographic Fallback Hierarchy
# ---------------------------------------------------------------------------
# The dataset uses ecoinvent location codes. When an exact country is not
# found we escalate through progressively broader regions.
#
#   1. Exact location (e.g. "IT", "Italy")
#   2. RER  – Regional Europe
#   3. GLO  – Global
#   4. RoW  – Rest of World (last resort)
#
GEOGRAPHIC_FALLBACK_CHAIN: List[str] = ["Europe without Switzerland", "Global", "Rest-of-World"]

# Normalisation map: user-facing strings → canonical dataset codes/names
_LOCATION_NORMALISE: dict[str, str] = {
    # Italian names
    "italia":               "Italy",
    "italy":                "Italy",
    "cina":                 "China",
    "china":                "China",
    "stati uniti":          "United States of America",
    "usa":                  "United States of America",
    "united states":        "United States of America",
    "united states of america": "United States of America",
    "germania":             "Germany",
    "germany":              "Germany",
    "francia":              "France",
    "france":               "France",
    "spagna":               "Spain",
    "spain":                "Spain",
    "regno unito":          "United Kingdom",
    "uk":                   "United Kingdom",
    "united kingdom":       "United Kingdom",
    "svizzera":             "Switzerland",
    "switzerland":          "Switzerland",
    # Regional / global aliases
    "europa":               "Europe without Switzerland",
    "europe":               "Europe without Switzerland",
    "rer":                  "Europe without Switzerland",
    "mondo":                "Global",
    "world":                "Global",
    "globale":              "Global",
    "global":               "Global",
    "glo":                  "Global",
    "row":                  "Rest-of-World",
    "rest of world":        "Rest-of-World",
    "resto del mondo":      "Rest-of-World",
}

# Category-level energy (MJ/kg) and cost (€/kg) defaults used when the
# dataset lacks the column or returns 0 for a material.
_CATEGORY_ENERGY: List[Tuple[Tuple[str, ...], float]] = [
    (("carbon fiber", "carbon fibre"),             300.0),
    (("alumin",),                                   200.0),
    (("copper", "rame"),                            100.0),
    (("nylon", "polyamide"),                        120.0),
    (("pet ", "polyethylene terephthalate"),         85.0),
    (("polypropylene", "pp "),                       80.0),
    (("polyethylene", "hdpe", "ldpe", "pe "),        75.0),
    (("steel", "iron", "acciaio", "ferro"),          30.0),
    (("glass", "vetro"),                             15.0),
    (("wood", "timber", "legno"),                   15.0),
]
_DEFAULT_ENERGY_MJ = 50.0  # fallback when no category matches

_CATEGORY_COST: List[Tuple[Tuple[str, ...], float]] = [
    (("carbon fiber", "carbon fibre"),             20.0),
    (("copper", "rame"),                            6.0),
    (("nylon", "polyamide"),                        3.0),
    (("alumin",),                                   2.5),
    (("pet ", "polyethylene terephthalate"),         1.3),
    (("polypropylene", "pp "),                      1.2),
    (("polyethylene", "hdpe", "ldpe", "pe "),       1.0),
    (("steel", "iron", "acciaio", "ferro"),         0.8),
    (("glass", "vetro"),                            0.7),
    (("wood", "timber", "legno"),                  0.5),
]
_DEFAULT_COST_PER_KG = 1.0  # fallback when no category matches


def _normalise_location(raw: Optional[str]) -> str:
    """Return the canonical dataset location code for a user-supplied string.

    Returns an empty string if *raw* is None / empty, which callers treat as
    "no location filter".
    """
    if not raw:
        return ""
    return _LOCATION_NORMALISE.get(raw.strip().lower(), raw.strip())


_EUROPEAN_CODES = {
    "Italy", "Germany", "France", "Spain", "United Kingdom", "Switzerland", 
    "Europe without Switzerland", "Europe"
}

def _get_regional_bin(canonical_location: str) -> List[str]:
    """Return the allowed regional fallback bin based on the initial location."""
    loc = canonical_location.strip() if canonical_location else ""
    # Use case-insensitive check for European codes or exact string matches
    if not loc or loc.lower() in ("global", "rest-of-world", "glo", "row"):
        return ["Global", "Rest-of-World"]
    if any(loc.lower() == ec.lower() for ec in _EUROPEAN_CODES):
        return ["Europe without Switzerland"]
    return ["Global", "Rest-of-World"]

def _get_geometry(name: str) -> Optional[str]:
    """Extract geometry keyword from a material name."""
    name = name.lower()
    if any(k in name for k in ["block", "blocco"]): return "block"
    if any(k in name for k in ["slab", "board", "lastra", "pannello"]): return "slab"
    if any(k in name for k in ["tile", "piastrella"]): return "tile"
    if any(k in name for k in ["brick", "mattone"]): return "brick"
    return None



class CSVLcaClient(LCADataProvider):
    """LCA data provider backed by a local Excel file containing LCA scores."""

    def __init__(self, data_path: Path = DEFAULT_DATA_PATH) -> None:
        self._path = Path(data_path)
        if not self._path.exists():
            raise FileNotFoundError(f"LCA Dataset not found: {self._path}")

        self._df = pd.read_excel(self._path, dtype=str).fillna("")
        self._df.columns = [c.strip().lower() for c in self._df.columns]
        self._validate_schema()

        # Pre-compute lowercase flowname column for fast vectorised search.
        self._df["_flowname_lower"] = self._df["outputname"].str.lower()

        # Convert climatechangeimpact to float
        self._df["climatechangeimpact"] = pd.to_numeric(
            self._df["climatechangeimpact"], errors="coerce"
        ).fillna(0.0)

        # Simple instance-level query cache: {cache_key -> list[dict]}
        self._search_cache: dict[str, list[dict]] = {}

        # Instance-level cache for difflib matching: {label_lower -> dict | None}
        self._match_cache: dict[str, dict | None] = {}

    # ------------------------------------------------------------------
    # Schema validation
    # ------------------------------------------------------------------

    def _validate_schema(self) -> None:
        required = {"id", "processname", "outputname", "location", "climatechangeimpact"}
        missing = required - set(self._df.columns)
        if missing:
            raise ValueError(f"Dataset is missing required columns: {missing}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
                "id":          "id",
                "processname": "providerName",
                "outputname":  "flowName",
                "location":    "location",
            })
            .to_dict(orient="records")
        )

        self._search_cache[cache_key] = results
        return results

    def find_closest_match(
        self,
        label: str,
        location: Optional[str] = None,
        threshold: float = 0.85,
    ) -> Optional[dict]:
        """Find the closest matching material using hierarchical strict search.
        
        FASE A: Exact Match
        Step 1: Local
        Step 2: Regional Bin
        
        FASE B: Fuzzy Match (0.85)
        Step 3: Fuzzy Local (with geometry constraint)
        Step 4: Fuzzy Regional (with geometry constraint)
        
        FASE C: Hard Stop
        """
        label_lower = label.lower().strip()
        canonical_loc = _normalise_location(location)

        cache_key = f"{label_lower}__{canonical_loc}_strict"
        if cache_key in self._match_cache:
            return self._match_cache[cache_key]

        if self._df.empty:
            return None

        # Determine the correct geographic bin
        regional_bins = _get_regional_bin(canonical_loc)

        result: Optional[dict] = None
        
        # FASE A: Ricerca Prodotto Esatto (Match 1.0)
        # Step 1 (Locale): exact match in requested geography
        row = self._search_exact(label_lower, canonical_loc)
        if row is not None:
            result = self._build_result(row, location_fallback_used=False)
            
        # Step 2 (Binario Regionale): exact match in regional bin
        if result is None:
            for r_bin in regional_bins:
                row = self._search_exact(label_lower, r_bin)
                if row is not None:
                    result = self._build_result(row, location_fallback_used=True)
                    break
                    
        # FASE B: Ricerca Prodotto Simile (Fuzzy Match 0.85)
        if result is None:
            # Step 3 (Fuzzy Locale): fuzzy match in requested geography
            row = self._search_fuzzy(label_lower, canonical_loc, threshold)
            if row is not None:
                result = self._build_result(row, location_fallback_used=False)
                
            # Step 4 (Fuzzy Regionale): fuzzy match in regional bin
            if result is None:
                for r_bin in regional_bins:
                    row = self._search_fuzzy(label_lower, r_bin, threshold)
                    if row is not None:
                        result = self._build_result(row, location_fallback_used=True)
                        break

        # FASE C: Hard Stop - Se non trovato, ritorna None
        self._match_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # Internal search helpers
    # ------------------------------------------------------------------

    def _get_location_subset(self, loc: str) -> pd.DataFrame:
        df_search = self._df
        if loc:
            mask_exact = df_search["location"].str.upper() == loc.upper()
            if mask_exact.any():
                return df_search[mask_exact]
            mask_partial = df_search["location"].str.contains(loc, case=False, na=False, regex=False)
            if mask_partial.any():
                return df_search[mask_partial]
            # If location not in dataset at all, return empty dataframe
            return df_search.iloc[0:0] 
        return df_search

    def _search_exact(self, label_lower: str, loc: str) -> Optional[pd.Series]:
        """Return the best-matching DataFrame row for an exact substring match."""
        df_search = self._get_location_subset(loc)
        if df_search.empty:
            return None

        mask_sub = df_search["_flowname_lower"].str.contains(label_lower, regex=False, na=False)
        subset = df_search[mask_sub]

        if not subset.empty:
            market = subset[subset["processname"].str.contains("market for", case=False, na=False)]
            if not market.empty:
                subset = market
            unique_names = subset["_flowname_lower"].unique()
            best = min(unique_names, key=len)
            return subset[subset["_flowname_lower"] == best].iloc[0]
            
        return None

    def _search_fuzzy(self, label_lower: str, loc: str, threshold: float) -> Optional[pd.Series]:
        """Return the best-matching DataFrame row for a fuzzy match with geometry constraint."""
        df_search = self._get_location_subset(loc)
        if df_search.empty:
            return None

        unique_names = df_search["_flowname_lower"].unique()
        matches = difflib.get_close_matches(label_lower, unique_names, n=10, cutoff=threshold)
        
        orig_geom = _get_geometry(label_lower)
        
        for match in matches:
            match_geom = _get_geometry(match)
            # VINCOLO GEOMETRIA: scarta se le geometrie sono diverse o se una manca e l'altra no
            if orig_geom != match_geom:
                continue

            match_rows = df_search[df_search["_flowname_lower"] == match]
            market_rows = match_rows[match_rows["processname"].str.contains("market for", case=False, na=False)]
            return market_rows.iloc[0] if not market_rows.empty else match_rows.iloc[0]
            
        return None

    def _build_result(self, row: pd.Series, location_fallback_used: bool) -> dict:
        """Construct the result dict from a matched DataFrame row."""
        return {
            "index":                 row.name + 2,  # 0-based index → Excel row
            "id":                    row["id"],
            "providerName":          row["processname"],
            "flowName":              row["outputname"],
            "location":              row["location"],
            "environmental_impact":  float(row["climatechangeimpact"]),
            "is_market":             "market" in str(row["processname"]).lower(),
            "energy_mj":             self._estimate_energy_mj(row),
            "cost_per_kg":           self._estimate_cost_per_kg(row),
            "location_fallback_used": location_fallback_used,
        }

    # ------------------------------------------------------------------
    # MCDA value estimators – never return 0
    # ------------------------------------------------------------------

    def _estimate_energy_mj(self, row: pd.Series) -> float:
        """Return energy intensity (MJ/kg) for *row*.

        First checks dataset columns ``energy_mj`` / ``energy``; falls back to
        a category lookup; final fallback is ``_DEFAULT_ENERGY_MJ``.
        """
        # Prefer real dataset column if available and non-zero
        for col in ("energy_mj", "energy"):
            if col in row.index:
                val = pd.to_numeric(row[col], errors="coerce")
                if pd.notna(val) and val > 0:
                    return float(val)

        name = str(row.get("outputname", "")).lower()
        for keywords, energy in _CATEGORY_ENERGY:
            if any(kw in name for kw in keywords):
                return energy
        return _DEFAULT_ENERGY_MJ

    def _estimate_cost_per_kg(self, row: pd.Series) -> float:
        """Return cost estimate (€/kg) for *row*.

        First checks dataset columns ``cost_per_kg`` / ``cost``; falls back to
        a category lookup; final fallback is ``_DEFAULT_COST_PER_KG``.
        """
        # Prefer real dataset column if available and non-zero
        for col in ("cost_per_kg", "cost"):
            if col in row.index:
                val = pd.to_numeric(row[col], errors="coerce")
                if pd.notna(val) and val > 0:
                    return float(val)

        name = str(row.get("outputname", "")).lower()
        for keywords, cost in _CATEGORY_COST:
            if any(kw in name for kw in keywords):
                return cost
        return _DEFAULT_COST_PER_KG

    # ------------------------------------------------------------------
    # get_impact_scores (async)
    # ------------------------------------------------------------------

    async def get_impact_scores(self, material_id: str) -> dict | None:
        """Return LCA impact scores from DataSet.xlsx.

        Returns *None* if *material_id* is not found.
        """
        row = self._df[self._df["id"] == material_id]
        if row.empty:
            return None

        r = row.iloc[0]
        cost = self._estimate_cost_per_kg(r)
        return {
            "environmental_impact": float(r["climatechangeimpact"]),
            "is_market":            "market" in str(r["processname"]).lower(),
            "energy_mj":            self._estimate_energy_mj(r),
            "cost_tier":            1 if cost < 1.0 else (2 if cost < 3.0 else (3 if cost < 10.0 else 4)),
            "cost_per_kg":          cost,
            "lifespan_years":       10.0,
        }
