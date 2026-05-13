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
GEOGRAPHIC_FALLBACK_CHAIN: List[str] = ["RER", "GLO", "RoW"]

# Normalisation map: user-facing strings → canonical dataset codes/names
_LOCATION_NORMALISE: dict[str, str] = {
    # Italian names
    "italia":               "IT",
    "italy":                "IT",
    "cina":                 "CN",
    "china":                "CN",
    "stati uniti":          "US",
    "usa":                  "US",
    "united states":        "US",
    "united states of america": "US",
    "germania":             "DE",
    "germany":              "DE",
    "francia":              "FR",
    "france":               "FR",
    "spagna":               "ES",
    "spain":                "ES",
    "regno unito":          "GB",
    "uk":                   "GB",
    "united kingdom":       "GB",
    "svizzera":             "CH",
    "switzerland":          "CH",
    # Regional / global aliases
    "europa":               "RER",
    "europe":               "RER",
    "rer":                  "RER",
    "mondo":                "GLO",
    "world":                "GLO",
    "globale":              "GLO",
    "global":               "GLO",
    "glo":                  "GLO",
    "row":                  "RoW",
    "rest of world":        "RoW",
    "resto del mondo":      "RoW",
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


def _get_fallback_chain(canonical_location: str) -> List[str]:
    """Build the ordered list of locations to try after the primary one fails.

    For a specific country code (e.g. "IT") the chain is:
        ["IT", "RER", "GLO", "RoW"]

    For a regional code already in the chain (e.g. "RER") the chain starts
    from that point onward:
        ["RER", "GLO", "RoW"]

    For an empty / already-global location the chain is just ["GLO", "RoW"].
    """
    loc = canonical_location.upper() if canonical_location else ""
    if loc in ("GLO", "ROW", ""):
        return [canonical_location] if canonical_location else ["GLO", "RoW"]
    if loc == "RER":
        return ["RER", "GLO", "RoW"]
    # Specific country → full chain
    return [canonical_location] + GEOGRAPHIC_FALLBACK_CHAIN


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
        threshold: float = 0.5,
    ) -> Optional[dict]:
        """Find the closest matching material using substring + difflib search.

        Parameters
        ----------
        label:
            Material name to search for.
        location:
            Optional location hint (country name, ISO code, or region code).
            Supports Italian names and common aliases; mapped internally to
            canonical dataset codes via ``_normalise_location``.
        threshold:
            Minimum difflib similarity score (0–1).

        Returns
        -------
        dict or None
            Match record including ``location_fallback_used`` flag.
            ``location_fallback_used`` is *True* when the result comes from a
            broader geographic scope than the one originally requested.
        """
        label_lower = label.lower().strip()
        canonical_loc = _normalise_location(location)

        cache_key = f"{label_lower}__{canonical_loc}"
        if cache_key in self._match_cache:
            return self._match_cache[cache_key]

        if self._df.empty:
            return None

        # Build the ordered list of locations to try
        chain = _get_fallback_chain(canonical_loc) if canonical_loc else [""]

        result: Optional[dict] = None
        location_fallback_used = False

        for attempt_idx, attempt_loc in enumerate(chain):
            row = self._search_in_location(label_lower, attempt_loc, threshold)
            if row is not None:
                location_fallback_used = attempt_idx > 0
                result = self._build_result(row, location_fallback_used)
                break

        # If even the full fallback chain failed, try completely without
        # location filter as a last resort.
        if result is None and canonical_loc:
            row = self._search_in_location(label_lower, "", threshold)
            if row is not None:
                location_fallback_used = True
                result = self._build_result(row, location_fallback_used)

        self._match_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # Internal search helpers
    # ------------------------------------------------------------------

    def _search_in_location(
        self, label_lower: str, loc: str, threshold: float
    ) -> Optional[pd.Series]:
        """Return the best-matching DataFrame row for *label_lower* within *loc*.

        Parameters
        ----------
        loc:
            Canonical location string. Empty string → search without filter.
        """
        df_search = self._df

        if loc:
            # Try exact match first, then partial/case-insensitive
            mask_exact = df_search["location"].str.upper() == loc.upper()
            if mask_exact.any():
                df_search = df_search[mask_exact]
            else:
                mask_partial = df_search["location"].str.contains(
                    loc, case=False, na=False, regex=False
                )
                if not mask_partial.any():
                    return None  # location not in dataset at all
                df_search = df_search[mask_partial]

        # --- Substring match (preferred) ---
        mask_sub = df_search["_flowname_lower"].str.contains(
            label_lower, regex=False, na=False
        )
        subset = df_search[mask_sub]

        if not subset.empty:
            # Prefer "market for …" processes (more representative in ecoinvent)
            market = subset[
                subset["processname"].str.contains("market for", case=False, na=False)
            ]
            if not market.empty:
                subset = market
            # Among candidates pick shortest name (most specific match)
            unique_names = subset["_flowname_lower"].unique()
            best = min(unique_names, key=len)
            return subset[subset["_flowname_lower"] == best].iloc[0]

        # --- Difflib fuzzy match (fallback within location) ---
        unique_names = df_search["_flowname_lower"].unique()
        matches = difflib.get_close_matches(
            label_lower, unique_names, n=1, cutoff=threshold
        )
        if not matches:
            return None

        best = matches[0]
        match_rows = df_search[df_search["_flowname_lower"] == best]
        market_rows = match_rows[
            match_rows["processname"].str.contains("market for", case=False, na=False)
        ]
        return market_rows.iloc[0] if not market_rows.empty else match_rows.iloc[0]

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
