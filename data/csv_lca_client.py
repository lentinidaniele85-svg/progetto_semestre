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
    "it":                   "Italy",
    "cina":                 "China",
    "china":                "China",
    "cn":                   "China",
    "stati uniti":          "United States of America",
    "usa":                  "United States of America",
    "us":                   "United States of America",
    "united states":        "United States of America",
    "united states of america": "United States of America",
    "germania":             "Germany",
    "germany":              "Germany",
    "de":                   "Germany",
    "francia":              "France",
    "france":               "France",
    "fr":                   "France",
    "spagna":               "Spain",
    "spain":                "Spain",
    "es":                   "Spain",
    "regno unito":          "United Kingdom",
    "uk":                   "United Kingdom",
    "gb":                   "United Kingdom",
    "svizzera":             "Switzerland",
    "switzerland":          "Switzerland",
    "ch":                   "Switzerland",
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


_SEMANTIC_SYNONYMS: dict[str, list[str]] = {
    # Metalli
    "acciaio":    ["steel", "cast iron", "ferro"],
    "steel":      ["steel", "cast iron"],
    "alluminio":  ["aluminum", "aluminium", "alloy"],
    "aluminum":   ["aluminum", "aluminium", "alloy"],
    "aluminium":  ["aluminum", "aluminium", "alloy"],
    "rame":       ["copper"],
    "copper":     ["copper"],
    "ottone":     ["brass"],
    "brass":      ["brass"],
    "ferro":      ["iron", "cast iron", "steel"],
    "iron":       ["iron", "cast iron", "steel"],
    "titanio":    ["titanium"],
    "titanium":   ["titanium"],
    # Polimeri generici
    "plastica":   ["plastic", "polyethylene", "polypropylene", "pet", "hdpe", "ldpe"],
    "plastic":    ["plastic", "polyethylene", "polypropylene", "pet", "hdpe", "ldpe"],
    # Polimeri specifici
    "polipropilene": ["polypropylene", "pp"],
    "polietilene":   ["polyethylene", "hdpe", "ldpe", "pe"],
    "pla":           ["polylactic acid", "pla", "bioplastic"],
    "abs":           ["acrylonitrile butadiene styrene", "abs"],
    "hdpe":          ["polyethylene", "hdpe", "high density polyethylene"],
    "ldpe":          ["polyethylene", "ldpe", "low density polyethylene"],
    # Vetro / minerali
    "vetro":      ["glass", "silica"],
    "glass":      ["glass", "silica"],
    "calcestruzzo": ["concrete", "cement", "mortar"],
    "cemento":    ["cement", "concrete"],
    "concrete":   ["concrete", "cement"],
    "cement":     ["cement", "concrete"],
    # Legno / naturali
    "legno":      ["wood", "timber", "plywood", "mdf", "board"],
    "wood":       ["wood", "timber", "plywood", "mdf", "board"],
    # Fibre
    "fibra di carbonio": ["carbon fiber", "carbon fibre", "cfrp"],
    "carbon fiber":      ["carbon fiber", "carbon fibre"],
    "fibra di vetro":    ["glass fiber", "glass fibre", "gfrp", "fiberglass"],
    "glass fiber":       ["glass fiber", "glass fibre", "fiberglass"],
    # Gomma / elastomeri
    "gomma":      ["rubber", "elastomer", "natural rubber"],
    "rubber":     ["rubber", "elastomer", "natural rubber"],
    # Tessili
    "cotone":     ["cotton"],
    "cotton":     ["cotton"],
    "lana":       ["wool"],
    "wool":       ["wool"],
    # Trasporti (per Task 3)
    "traghetto":  ["ferry", "ship", "vessel"],
    "nave":       ["ship", "vessel", "ferry"],
    "ferry":      ["ferry", "ship", "vessel"],
    "aereo":      ["aircraft", "airplane", "air freight"],
    "aircraft":   ["aircraft", "airplane", "air freight"],
    "camion":     ["lorry", "truck", "freight"],
    "lorry":      ["lorry", "truck", "freight"],
}

def _expand_semantic_terms(label: str) -> List[str]:
    """Return a list of expanded industrial synonyms for a given label."""
    base_term = label.strip().lower()
    expanded = [base_term]
    for key, synonyms in _SEMANTIC_SYNONYMS.items():
        if key in base_term:
            expanded.extend(synonyms)
    # Remove duplicates but preserve order
    seen = set()
    result = []
    for term in expanded:
        if term not in seen:
            seen.add(term)
            result.append(term)
    return result


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
        return ["Europe without Switzerland", "Global", "Rest-of-World"]
    return ["Global", "Rest-of-World"]

def _get_geometry(name: str) -> Optional[str]:
    """Extract geometry keyword from a material name."""
    name = name.lower()
    if any(k in name for k in ["block", "blocco"]): return "block"
    if any(k in name for k in ["slab", "board", "lastra", "pannello"]): return "slab"
    if any(k in name for k in ["tile", "piastrella"]): return "tile"
    if any(k in name for k in ["brick", "mattone"]): return "brick"
    return None


def _parse_ecoinvent_name(raw: str) -> tuple[str, str, str]:
    """
    Parsa un nome ecoinvent nel formato:
      "activity name, attribute | product name | location"

    Restituisce (activity_core, product_name, location).

    Esempi:
      "market for steel, unalloyed | steel, unalloyed | Italy"
        → ("market for steel", "steel, unalloyed", "Italy")
      "steel production, electric, low-alloyed | steel, low-alloyed | Europe without Switzerland"
        → ("steel production", "steel, low-alloyed", "Europe without Switzerland")
      "polypropylene, granulate"  (formato senza pipe)
        → ("polypropylene", "polypropylene, granulate", "")
    """
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) >= 3:
        # Formato completo: activity | product | location
        activity_full = parts[0]
        product = parts[1]
        location = parts[2]
        # Estrai il nome core dell'attività (prima della virgola)
        activity_core = activity_full.split(",")[0].strip()
    elif len(parts) == 2:
        activity_core = parts[0].split(",")[0].strip()
        product = parts[1]
        location = ""
    else:
        # Formato semplice (no pipe)
        activity_core = raw.split(",")[0].strip()
        product = raw
        location = ""
    return activity_core, product, location


class CSVLcaClient(LCADataProvider):
    """LCA data provider backed by a local Excel file containing LCA scores."""

    def __init__(self, data_path: Path = DEFAULT_DATA_PATH) -> None:
        self._path = Path(data_path)
        if not self._path.exists():
            raise FileNotFoundError(f"LCA Dataset not found: {self._path}")

        self._df = pd.read_excel(self._path, dtype=str).fillna("")
        self._df.columns = [c.strip().lower() for c in self._df.columns]
        self._validate_schema()

        # Pre-compute lowercase columns for fast vectorised search.
        self._df["_flowname_lower"] = self._df["outputname"].str.lower()
        self._df["_processname_lower"] = self._df["processname"].str.lower()

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
        label: Optional[str] = None,
        location: Optional[str] = None,
        threshold: float = 0.65,
        target_product: Optional[str] = None,
        target_geography: Optional[str] = None,
        task_type: str = "optimization"
    ) -> Optional[dict]:
        """Find the closest matching material using a 3-stage search logic.
        
        STADIO 1: Espansione Semantica (The "Think" Phase)
        STADIO 2: Ricerca Fuzzy con Filtro Dinamico (The Best-Match Logic)
        STADIO 3: Fallback Intelligente (Geographic Expansion)
        """
        actual_label = target_product if target_product is not None else label
        actual_location = target_geography if target_geography is not None else location
        
        if not actual_label:
            return None
            
        label_lower = actual_label.lower().strip()
        exact_loc = actual_location.strip() if actual_location else ""
        canonical_loc = _normalise_location(actual_location)

        cache_key = f"{label_lower}__{exact_loc}__{canonical_loc}_semantic_{task_type}"
        if cache_key in self._match_cache:
            return self._match_cache[cache_key]

        if self._df.empty:
            return None

        # STADIO 1: Espansione Semantica
        search_terms = _expand_semantic_terms(label_lower)

        # STADIO 3: Fallback Geografico (Outer Loop)
        # 1. Forced Geographic Array: [location] + _get_regional_bin(location)
        target_loc = exact_loc if exact_loc else (canonical_loc if canonical_loc else "Global")
        
        geographies_to_try = [target_loc]
        if target_loc != "Global":
            if canonical_loc and canonical_loc != target_loc:
                 geographies_to_try.append(canonical_loc)
            geographies_to_try.extend(_get_regional_bin(canonical_loc if canonical_loc else target_loc))
        else:
            geographies_to_try.extend(["Global", "Rest-of-World"])
            
        # Remove duplicates preserving order
        seen_geo = set()
        geographies_to_try = [g for g in geographies_to_try if not (g in seen_geo or seen_geo.add(g))]

        result: Optional[dict] = None
        
        # 3. "Virgin-First" Logic Enforcement (Pass 1)
        for i, geo in enumerate(geographies_to_try):
            if i == 0:
                print(f"[DEBUG] Stage 0: Cerco in {geo}...")
            else:
                print(f"[DEBUG] Nessun match in {geographies_to_try[i-1]}. Passo a {geo}...")
                
            is_fallback = (geo != canonical_loc) if canonical_loc else False
            row = self._search_best_match(search_terms, label_lower, geo, task_type, 0.85, self._df, require_virgin=True)
            if row is not None:
                result = self._build_result(row, location_fallback_used=is_fallback, requested_location=exact_loc, pass_number=1)
                self._match_cache[cache_key] = result
                return result

        # Pass 2: Fallback Standard se non esiste materiale virgin (soglia 0.70)
        for i, geo in enumerate(geographies_to_try):
            is_fallback = (geo != canonical_loc) if canonical_loc else False
            row = self._search_best_match(search_terms, label_lower, geo, task_type, 0.70, self._df, require_virgin=False)
            if row is not None:
                result = self._build_result(row, location_fallback_used=is_fallback, requested_location=exact_loc, pass_number=2)
                self._match_cache[cache_key] = result
                return result

        # Hard Stop - Se non trovato nulla in nessuna geografia
        self._match_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # Internal search helpers
    # ------------------------------------------------------------------

    def _get_location_subset(self, loc: str, base_df: pd.DataFrame) -> pd.DataFrame:
        df_search = base_df
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

    def _search_best_match(
        self, search_terms: List[str], original_label: str, loc: str, 
        task_type: str, threshold: float, base_df: pd.DataFrame,
        require_virgin: bool = False
    ) -> Optional[pd.Series]:
        """Return the best-matching DataFrame row evaluating all semantic terms with dynamic filters."""
        df_search = self._get_location_subset(loc, base_df)
        if df_search.empty:
            return None

        # Raccogli tutti i potenziali candidati che matchano almeno un termine espanso come substring
        # sia in outputname che in processname
        masks_out = [df_search["_flowname_lower"].str.contains(term, regex=False, na=False) for term in search_terms]
        masks_proc = [df_search["_processname_lower"].str.contains(term, regex=False, na=False) for term in search_terms]
        
        combined_mask_out = pd.concat(masks_out, axis=1).any(axis=1) if masks_out else pd.Series(False, index=df_search.index)
        combined_mask_proc = pd.concat(masks_proc, axis=1).any(axis=1) if masks_proc else pd.Series(False, index=df_search.index)
        
        candidates = df_search[combined_mask_out | combined_mask_proc]

        if candidates.empty:
            return None

        # STADIO 2: Filtro Dinamico (Post-Processing)
        is_metal = any(m in original_label for m in ["steel", "aluminum", "aluminium", "iron", "copper", "brass", "metal", "titanium", "acciaio", "alluminio", "ferro", "rame", "ottone"])
        is_plastic = any(m in original_label for m in ["plastic", "polyethylene", "polypropylene", "pet", "hdpe", "ldpe", "polyester", "nylon", "plastica", "polimero"])
        
        valid_indices = []
        for idx, row in candidates.iterrows():
            out_name = row["_flowname_lower"]
            proc_name = row["_processname_lower"]
            impact = float(row["climatechangeimpact"])
            name_combined = f"{out_name} {proc_name}"
            
            import re
            
            # task_type="optimization" + richiesta esplicita riciclato → permetti waste/recycled
            user_wants_recycled = any(
                term in original_label for term in
                ["recycled", "riciclato", "recycling", "riciclo", "secondary", "secondario"]
            )
            
            if require_virgin and not user_wants_recycled:
                if re.search(r"\bwaste\b|\bscrap\b|\bscarto\b", name_combined):
                    print(f"[DEBUG] Trovato {row['outputname']} in {loc} -> Scartato (Virgin-First Enforced)")
                    continue
            elif not user_wants_recycled and task_type == "optimization":
                # In optimization mode senza richiesta riciclato: filtra waste nei metalli
                if is_metal and re.search(r"\bwaste\b|\bscrap\b|\bscarto\b", name_combined):
                    print(f"[DEBUG] Trovato {row['outputname']} in {loc} -> Scartato (Filtro Metallo: waste in optimization)")
                    continue
                    
            if is_metal:
                # Scarta se impatto < 1.0
                if impact < 1.0:
                    print(f"[DEBUG] Trovato {row['outputname']} in {loc} -> Scartato (Filtro Metallo: impatto <1.0)")
                    continue
            elif is_plastic:
                # Permetti recycled ma solo se impatto > 0.8
                if impact <= 0.8:
                    print(f"[DEBUG] Trovato {row['outputname']} in {loc} -> Scartato (Filtro Plastica: impatto <= 0.8)")
                    continue
            # Legno/Naturali non hanno limiti (nessun else break)
            
            valid_indices.append(idx)
            
        candidates = candidates.loc[valid_indices]

        if candidates.empty:
            print(f"[DEBUG] Geografia {loc} scartata (0 candidati validi dopo il filtro).")
            return None

        # STADIO 2: Best-Match Logic
        # Troviamo il candidato con la massima similarità usando difflib su entrambe le colonne
        best_score = -1.0
        best_row = None
        orig_geom = _get_geometry(original_label)
        
        for idx, row in candidates.iterrows():
            out_name = row["_flowname_lower"]
            proc_name = row["_processname_lower"]
            impact = float(row["climatechangeimpact"])
            name_combined = f"{out_name} {proc_name}"
            match_geom = _get_geometry(out_name)
            
            # VINCOLO GEOMETRIA: scarta se le geometrie sono diverse (es. slab vs block)
            if orig_geom is not None and match_geom is not None and orig_geom != match_geom:
                print(f"[DEBUG] Trovato {row['outputname']} in {loc} -> Scartato (Geometria non corrispondente)")
                continue
                
            # Calcola lo score migliore tra tutti i termini di ricerca su entrambe le colonne
            def get_base_score(term: str, raw_text: str) -> float:
                """
                Calcola lo score di similarità tra 'term' (query) e 'raw_text' (nome CSV).
                
                Distingue tra:
                - flowName: usa il product name estratto (dopo il primo pipe, prima della virgola)
                - processName: usa l'activity core (prima della prima virgola o del primo pipe)
                
                La Length Penalty viene applicata solo sulle parole del nome core,
                NON sull'intera stringa (evita penalità per attributi tecnici dopo la virgola).
                """
                activity_core, product_name, _ = _parse_ecoinvent_name(raw_text)
                
                # Confronta term contro sia il product_name sia l'activity_core
                # (prendi il migliore dei due)
                best = 0.0
                for candidate in [activity_core.lower(), product_name.lower()]:
                    if term == candidate:
                        s = 1.0
                    elif term in candidate.split():
                        s = 0.85
                    else:
                        s = difflib.SequenceMatcher(None, term, candidate).ratio()
                    
                    # Length penalty: solo sulle parole del candidate (non della stringa intera)
                    word_diff = len(candidate.split()) - len(term.split())
                    if word_diff > 0:
                        s -= (0.15 * word_diff)
                    
                    best = max(best, s)
                
                return best

            score_out = max((get_base_score(term, out_name) for term in search_terms), default=0.0)
            score_proc = max((get_base_score(term, proc_name) for term in search_terms), default=0.0)
            score = max(score_out, score_proc)
            
            # Penalità/Bonus Industriale
            if any(term in name_combined for term in ["bark", "sawdust", "shavings"]) and not any(term in original_label for term in ["bark", "sawdust", "shavings"]):
                score -= 0.2
            if "sawnwood" in name_combined:
                score += 0.1
                
            # Penalità di Fedeltà: Scarta la ghisa (iron/cast iron) se l'utente ha chiesto acciaio (steel)
            if "steel" in search_terms and "iron" not in search_terms and "ghisa" not in search_terms:
                if "iron" in name_combined or "cast iron" in name_combined:
                    score -= 0.5
                    
            # 2. Industrial Quality Scoring (Stop "Pipe" Hallucinations)
            # Bonus Industrial Quality — DISCRIMINANTE per "market for [material]"
            # Il bonus si applica SOLO a record ecoinvent "market for X" dove X è
            # il materiale cercato, NON a qualsiasi record con "market for".
            is_market_for_material = (
                proc_name.startswith("market for")
                and any(term in proc_name for term in search_terms)
                and "transport" not in proc_name
                and "electricity" not in proc_name
                and "heat" not in proc_name
            )
            
            bonus_terms_non_market = ["production", "primary", "unalloyed", "low-alloyed"]
            penalty_terms = ["pipe", "tube", "welding", "extrusion", "drawing", "wire", "trawler", "seiner", "liner", "vessel", "ship", "vehicle", "machinery", "infrastructure", "forging", "processing"]
            
            if is_market_for_material:
                score += 0.3
            elif any(term in name_combined for term in bonus_terms_non_market):
                score += 0.2   # Bonus ridotto per altri indicatori di qualità
                
            if any(term in name_combined for term in penalty_terms):
                score -= 0.5
                print(f"[DEBUG] Declassato '{row['outputname']}' (-0.5) perché processo/prodotto finito/veicolo. Cerco materia prima...")
                
            if score > best_score:
                best_score = score
                best_row = row

        if best_score >= threshold and best_row is not None:
            print(f"[DEBUG] Trovato {best_row['outputname']} in {loc} -> APPROVATO (Score: {best_score:.3f} >= {threshold})")
            return best_row
        elif best_row is not None:
            print(f"[DEBUG] Trovato {best_row['outputname']} in {loc} -> Scartato (Score: {best_score:.3f} < {threshold})")
            
        return None

    def _build_result(
        self,
        row: pd.Series,
        location_fallback_used: bool,
        requested_location: str = "",
        pass_number: int = 1,
    ) -> dict:
        """Construct the result dict from a matched DataFrame row."""
        matched_location = str(row.get("location", "")).strip()
        exact_match_found = (
            matched_location.lower() == requested_location.lower()
            if requested_location else False
        )
        geo_level = "exact" if exact_match_found else (
            "regional" if "europe" in matched_location.lower() or "rer" in matched_location.lower()
            else "global" if matched_location.lower() in ("global", "glo")
            else "row" if matched_location.lower() in ("rest-of-world", "row")
            else "fallback"
        )
        return {
            "index":                 row.name + 2,
            "id":                    row["id"],
            "providerName":          row["processname"],
            "flowName":              row["outputname"],
            "location":              matched_location,
            "environmental_impact":  float(row["climatechangeimpact"]),
            "is_market":             str(row["processname"]).lower().strip().startswith("market for"),
            "energy_mj":             self._estimate_energy_mj(row),
            "cost_per_kg":           self._estimate_cost_per_kg(row),
            "location_fallback_used": location_fallback_used,
            "exact_match_found":     exact_match_found,      # ← FIX BUG
            "geo_level_used":        geo_level,              # ← FIX BUG
            "pass_number":           pass_number,            # 1=virgin-first, 2=standard fallback
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
