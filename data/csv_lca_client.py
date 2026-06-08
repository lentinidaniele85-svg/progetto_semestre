from pathlib import Path
import difflib
from typing import List, Optional, Tuple
import pandas as pd
from pydantic import BaseModel

from data.lca_interface import LCADataProvider

DEFAULT_DATA_PATH = Path(__file__).parent / "dataset_ecoinvent_perfetto.xlsx"

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
GEOGRAPHIC_FALLBACK_CHAIN: List[str] = ["Global", "Europe without Switzerland", "Rest-of-World"]

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


# ---------------------------------------------------------------------------
# AZIONE 1: Semantic Normalization Pipeline
# ---------------------------------------------------------------------------
# Questi blocchi sono processati PRIMA della ricerca fuzzy per estrarre
# l'identità chimica del materiale separandola dal rumore (aggettivi, usi,
# contenuti del contenitore).
# ---------------------------------------------------------------------------

# ── Aggettivi / modificatori "deboli" ────────────────────────────────────
# Parole che NON cambiano la natura chimica del materiale → rimosse.
# Multi-token prima, singoli dopo (greedy match dall'alto verso il basso).
_WEAK_ADJECTIVE_PHRASES: tuple[str, ...] = (
    # Usi finali / applicazioni
    "per edilizia", "per costruzione", "per uso alimentare", "per imballaggio",
    "per uso industriale", "per uso civile", "per uso domestico",
    "for construction", "for packaging", "for food use", "for industrial use",
    # Dimensioni / formati specifici (non classificanti)
    "da 1 litro", "da 500 ml", "da 500ml", "da 1l", "da 2 litri",
    "1 litre", "500 ml", "500ml",
    # Preposizioni introduttive (italiano)
    " per ", " in ", " di ", " da ",
    "per ", "in ", "di ", "da ",
)

_WEAK_ADJECTIVE_TOKENS: frozenset[str] = frozenset({
    # Aggettivi fisici non classificanti
    "rigido", "rigid", "duro", "hard", "morbido", "soft",
    "pesante", "heavy", "leggero", "light",
    "opaco", "opaque", "lucido", "glossy", "satinato", "matte",
    # Colori
    "rosso", "red", "blu", "blue", "verde", "green",
    "bianco", "white", "nero", "black", "trasparente", "transparent", "clear",
    "grigio", "grey", "gray", "giallo", "yellow",
    # Aggettivi di applicazione generici
    "naturale", "natural",   # NON "riciclato": quello cambia l'LCA
    "standard", "comune", "common", "generico", "generic",
    "industriale", "industrial",   # descrittivo, non classificante
    "speciale", "special",
    "nuovo", "new", "usato", "used",
    # Preposizioni standalone che sopravvivono al phrase-strip
    "per", "in", "di", "da", "con", "senza", "for", "with", "without", "of",
})

# ── Aggettivi / modificatori "forti" ─────────────────────────────────────
# Parole che CAMBIANO la natura chimica o il processo → mantenute e propagate
# come `strong_modifiers` nel NormalizationResult.
_STRONG_PROCESS_MODIFIERS: frozenset[str] = frozenset({
    # Processo di produzione (specificano la tecnologia → rilevante LCA)
    "estruso", "extruded", "extrusion",
    "stampato", "moulded", "molded",
    "soffiato", "blown",
    "laminato", "laminated",
    "sinterizzato", "sintered",
    # Natura del materiale (cambiano l'impatto LCA)
    "riciclato", "recycled", "riciclo",
    "secondario", "secondary",
    "vergine", "virgin",
    # Modifiche strutturali (alterano le proprietà del materiale)
    "rinforzato", "reinforced",
    "espanso", "expanded", "espanso",  # es. EPS = polistirene espanso
    "cellulare", "cellular",
    "alveolare",
    "reticolato", "cross-linked",
    "caricato", "filled",              # es. PP caricato vetro
})

# ── Pattern di identità chimica ───────────────────────────────────────────
# Lista ORDINATA: più specifico prima.
# Ogni entry: (pattern_regex_or_substring, canonical_english_term)
# Tipo "re" = regex con word boundary; tipo "sub" = substring case-insensitive
_CHEMICAL_IDENTITY_PATTERNS: list[tuple[str, str, str]] = [
    # ── Tier 1: Acronimi (word-boundary, case-insensitive) ────────────────
    ("re", r"\bPVC\b",   "PVC"),
    ("re", r"\bPET\b",   "PET"),
    ("re", r"\bHDPE\b",  "HDPE"),
    ("re", r"\bLDPE\b",  "LDPE"),
    ("re", r"\bLLDPE\b", "LLDPE"),
    ("re", r"\bPP\b",    "polypropylene"),
    ("re", r"\bABS\b",   "ABS"),
    ("re", r"\bPLA\b",   "PLA"),
    ("re", r"\bEPS\b",   "EPS"),
    ("re", r"\bXPS\b",   "XPS"),
    ("re", r"\bPC\b",    "polycarbonate"),
    ("re", r"\bPA\b",    "polyamide"),
    ("re", r"\bPA6\b",   "polyamide"),
    ("re", r"\bPA66\b",  "polyamide"),
    ("re", r"\bTPU\b",   "thermoplastic polyurethane"),
    ("re", r"\bPOM\b",   "polyoxymethylene"),
    ("re", r"\bPMMA\b",  "polymethyl methacrylate"),
    ("re", r"\bPE\b",    "polyethylene"),
    # ── Tier 2: Nomi completi (normalizzati in inglese) ───────────────────
    ("sub", "polyvinyl chloride",       "PVC"),
    ("sub", "cloruro di polivinile",    "PVC"),
    ("sub", "policloruro di vinile",    "PVC"),
    ("sub", "polyethylene terephthalate", "PET"),
    ("sub", "polietilene tereftalato",  "PET"),
    ("sub", "polipropilene",            "polypropylene"),
    ("sub", "polypropilene",            "polypropylene"),
    ("sub", "polietilene",              "polyethylene"),
    ("sub", "polietylene",              "polyethylene"),
    ("sub", "polistirene",              "polystyrene"),
    ("sub", "polystyrene",              "polystyrene"),
    ("sub", "poliammide",               "polyamide"),
    ("sub", "polycarbonato",            "polycarbonate"),
    ("sub", "polimetilmetacrilato",     "polymethyl methacrylate"),
    ("sub", "acido polilattico",        "PLA"),
    ("sub", "polylactic acid",          "PLA"),
    ("sub", "polyurethane",             "polyurethane"),
    ("sub", "poliuretano",              "polyurethane"),
    ("sub", "acrylonitrile butadiene",  "ABS"),
    # ── Metalli ───────────────────────────────────────────────────────────
    ("sub", "acciaio inox",             "stainless steel"),
    ("sub", "stainless steel",          "stainless steel"),
    ("sub", "acciaio",                  "steel"),
    ("sub", "alluminio",                "aluminium"),
    ("sub", "aluminum",                 "aluminium"),
    ("sub", "rame",                     "copper"),
    ("sub", "ottone",                   "brass"),
    ("sub", "ferro",                    "iron"),
    ("sub", "ghisa",                    "cast iron"),
    ("sub", "titanio",                  "titanium"),
    ("sub", "zinco",                    "zinc"),
    ("sub", "piombo",                   "lead"),
    # ── Naturali / altri ─────────────────────────────────────────────────
    ("sub", "fibra di carbonio",        "carbon fiber"),
    ("sub", "carbon fibre",             "carbon fiber"),
    ("sub", "fibra di vetro",           "glass fiber"),
    ("sub", "glass fibre",              "glass fiber"),
    ("sub", "legno",                    "wood"),
    ("sub", "calcestruzzo",             "concrete"),
    ("sub", "cemento",                  "cement"),
    ("sub", "vetro",                    "glass"),
    ("sub", "gomma",                    "rubber"),
    ("sub", "silicone",                 "silicone"),
    ("sub", "nylon",                    "nylon"),
    ("sub", "cotone",                   "cotton"),
    ("sub", "lana",                     "wool"),
]

# ── Segnali di geometria / forma ─────────────────────────────────────────
# Mappa classe-geometria → parole chiave (italiano + inglese).
# La classe geometrica viene usata dal ProcessResolver.
_GEOMETRY_SIGNALS: dict[str, tuple[str, ...]] = {
    "profile":  ("tubo", "pipe", "profilo", "profile", "barra", "rod",
                 "condotto", "duct", "sezione", "section", "conduit",
                 "tubing", "tubolaro"),
    "hollow":   ("bottiglia", "bottle", "contenitore", "container",
                 "flacone", "flask", "boccetta", "jug", "brocca",
                 "canister", "barattolo", "jar", "recipient", "flacone"),
    "flat":     ("foglio", "sheet", "film", "pellicola", "lastra",
                 "slab", "board", "pannello", "panel", "membrane",
                 "membrana", "wrap", "packaging film"),
    "solid":    ("tappo", "cap", "blocco", "block", "pezzo", "part",
                 "componente", "component", "lid", "coperchio",
                 "dado", "nut", "vite", "screw", "bolt", "bullone"),
    "fabric":   ("tessuto", "fabric", "fibra", "fiber", "fibre",
                 "filato", "yarn", "tela", "cloth"),
}

# ── Contenuti irrilevanti per LCA del contenitore ────────────────────────
# Questi token sono il CONTENUTO del contenitore, non il materiale.
# Es: "bottiglia d'acqua" → il materiale è il contenitore (PET), non l'acqua.
_CONTAINER_CONTENTS: frozenset[str] = frozenset({
    "acqua", "water", "vino", "wine", "birra", "beer", "succo", "juice",
    "olio", "oil", "latte", "milk", "yogurt", "aceto", "vinegar",
    "shampoo", "detersivo", "detergent", "sapone", "soap",
    "bevanda", "beverage", "drink", "liquido", "liquid",
    "gas", "aria", "air",
})

# ── Mapping contenitore → materiale default ───────────────────────────────
# Quando l'input è un oggetto contenitore senza materiale esplicito,
# il normalizzatore usa questo mapping per inferire il materiale base.
_CONTAINER_DEFAULT_MATERIAL: dict[str, str] = {
    "hollow":  "PET",         # bottiglia/contenitore → PET (più comune)
    "profile": "PVC",         # tubo/profilo → PVC (più comune)
    "flat":    "polyethylene", # foglio/film → PE
    "solid":   "polypropylene", # pezzo solido → PP
}


# ---------------------------------------------------------------------------
# NormalizationResult — output strutturato del SemanticNormalizer
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field as dc_field

@dataclass
class NormalizationResult:
    """Output strutturato della Semantic Normalization Pipeline.

    Attributes:
        base_material:     Il termine chimico core da cercare nel DB.
                           Es: "PVC rigido per edilizia" → "PVC"
        geometry_hint:     Classe geometrica dedotta dall'input, se presente.
                           Uno di: "profile", "hollow", "flat", "solid", "fabric", None.
                           Es: "tubo in PVC" → "profile"
        strong_modifiers:  Aggettivi forti mantenuti (cambiano natura LCA).
                           Es: ["recycled"], ["extruded"]
        stripped_tokens:   Token rimossi durante il processo (per audit/debug).
        original_input:    Input originale immutato (per tracciabilità).
        inferred_material: True se il materiale è stato dedotto dalla geometria
                           (non trovato esplicitamente nell'input).
    """
    base_material:    str
    geometry_hint:    "Optional[str]"
    strong_modifiers: "list[str]"  = dc_field(default_factory=list)
    stripped_tokens:  "list[str]"  = dc_field(default_factory=list)
    original_input:   str          = ""
    inferred_material: bool        = False


# ---------------------------------------------------------------------------
# SemanticNormalizer — la pipeline di normalizzazione
# ---------------------------------------------------------------------------
class SemanticNormalizer:
    """Normalizza l'input utente prima della ricerca fuzzy nel DB LCA.

    Pipeline (3 passi sequenziali):
      1. Content Stripping: rimuove i contenuti del contenitore (acqua, vino…)
      2. Chemical Identity Extraction: estrae il termine chimico core
         usando _CHEMICAL_IDENTITY_PATTERNS (con priorità acronimi > nomi > categorie)
      3. Geometry/Form Extraction: deduce la classe geometrica dall'input
         (usata poi dal ProcessResolver)

    Se nessun pattern chimico matcha, restituisce l'input ripulito dal rumore
    come base_material (comportamento "safe" — non inventa nulla).
    """

    import re as _re_module

    def normalize(self, raw_input: str) -> NormalizationResult:
        """Esegue la pipeline completa su raw_input.

        Args:
            raw_input: stringa grezza dall'utente (qualsiasi lingua).

        Returns:
            NormalizationResult con base_material, geometry_hint, ecc.
        """
        import re

        text = raw_input.strip()
        stripped_tokens: list[str] = []
        strong_modifiers: list[str] = []

        # ── Passo 0: Rimuovi contenuti irrilevanti ────────────────────────
        # Es: "bottiglia d'acqua" → rimuove "acqua" → "bottiglia"
        tokens = text.lower().split()
        content_free_tokens = []
        for tok in tokens:
            # Rimuovi apostrofi per matching (d'acqua → acqua)
            clean_tok = tok.replace("'", "").replace("'", "")
            if clean_tok in _CONTAINER_CONTENTS:
                stripped_tokens.append(tok)
            else:
                content_free_tokens.append(tok)
        text_clean = " ".join(content_free_tokens)

        # ── Passo 0b: Rileva strong modifiers PRIMA dello stripping ──────
        tokens_lower = text_clean.lower().split()
        for tok in tokens_lower:
            if tok in _STRONG_PROCESS_MODIFIERS:
                if tok not in strong_modifiers:
                    strong_modifiers.append(tok)

        # ── Passo 1: Geometry/Form Extraction ────────────────────────────
        geometry_hint: Optional[str] = None
        text_lower = text_clean.lower()
        for geom_class, keywords in _GEOMETRY_SIGNALS.items():
            if any(kw in text_lower for kw in keywords):
                geometry_hint = geom_class
                break  # Prima classe trovata vince (ordine dict = priorità)

        # ── Passo 2: Chemical Identity Extraction ─────────────────────────
        base_material: Optional[str] = None
        for pattern_type, pattern, canonical in _CHEMICAL_IDENTITY_PATTERNS:
            if pattern_type == "re":
                if re.search(pattern, text_clean, re.IGNORECASE):
                    base_material = canonical
                    break
            else:  # "sub"
                if pattern.lower() in text_clean.lower():
                    base_material = canonical
                    break

        inferred = False
        if base_material is None:
            # Passo 2b: Nessun match chimico esplicito.
            # Prova a dedurre dalla geometria (es. "bottiglia" → PET)
            if geometry_hint and geometry_hint in _CONTAINER_DEFAULT_MATERIAL:
                base_material = _CONTAINER_DEFAULT_MATERIAL[geometry_hint]
                inferred = True
            else:
                # Safe fallback: restituisce l'input ripulito dai weak tokens
                base_material = self._strip_weak_tokens(text_clean)

        # ── Passo 3: Strip weak tokens dall'input per audit ───────────────
        # (già eseguito internamente, tracciamo solo per debug)
        for phrase in _WEAK_ADJECTIVE_PHRASES:
            phrase_clean = phrase.strip()
            if phrase_clean and phrase_clean in text_lower:
                stripped_tokens.append(phrase_clean)
        for tok in tokens_lower:
            if tok in _WEAK_ADJECTIVE_TOKENS:
                stripped_tokens.append(tok)

        return NormalizationResult(
            base_material=base_material,
            geometry_hint=geometry_hint,
            strong_modifiers=strong_modifiers,
            stripped_tokens=list(dict.fromkeys(stripped_tokens)),  # dedup
            original_input=raw_input,
            inferred_material=inferred,
        )

    def _strip_weak_tokens(self, text: str) -> str:
        """Rimuove phrase deboli e token deboli, restituisce il residuo pulito."""
        result = text.lower()
        for phrase in _WEAK_ADJECTIVE_PHRASES:
            result = result.replace(phrase.lower(), " ")
        tokens = [t for t in result.split() if t not in _WEAK_ADJECTIVE_TOKENS]
        return " ".join(tokens).strip() or text.lower().strip()


# Istanza singleton del normalizzatore (senza stato → sicuro)
_semantic_normalizer = SemanticNormalizer()


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
    # DIRETTIVA 1: entry dedicata PET polimero — usa nome IUPAC per massimizzare
    # lo score contro "market for polyethylene terephthalate" e minimizzarlo
    # contro "petroleum and gas production" (nomi completamente diversi).
    "pet":        ["polyethylene terephthalate", "pet", "granulate"],
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
    """Return a list of expanded industrial synonyms for a given label.

    MATCHING LOGIC:
    - Chiavi corte (≤ 4 caratteri, es. 'pet', 'pp', 'abs'): word-boundary regex.
      Previene falsi positivi come 'pet' in 'petroleum'.
    - Chiavi lunghe (> 4 caratteri, es. 'acciaio', 'polipropilene'): substring.
      Meno ambigue per definizione.
    """
    import re as _re
    base_term = label.strip().lower()
    expanded = [base_term]
    for key, synonyms in _SEMANTIC_SYNONYMS.items():
        if len(key) <= 4:
            # Word-boundary match per acronimi corti: evita 'pet' in 'petroleum'
            if _re.search(r"\b" + _re.escape(key) + r"\b", base_term):
                expanded.extend(synonyms)
        else:
            # Substring match per termini lunghi (retrocompatibile)
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


# ---------------------------------------------------------------------------
# LLM Dynamic Semantic Expander
# ---------------------------------------------------------------------------
# Sostituisce la logica statica _SEMANTIC_SYNONYMS con un LLM chiamato in
# tempo reale. Il risultato viene cachato in sessione per evitare chiamate
# API ripetute per lo stesso materiale (es. 5 componenti PVC = 1 call LLM).
# ---------------------------------------------------------------------------

# Cache di sessione: {material_lower -> lista di termini espansi}
_llm_expansion_cache: dict[str, list[str]] = {}

_LLM_EXPANDER_SYSTEM_PROMPT = (
    "Sei un esperto chimico e analista LCA con conoscenza approfondita del database ecoinvent. "
    "Il tuo compito è tradurre nomi di MATERIALI o MEZZI DI TRASPORTO nei termini tecnici esatti "
    "utilizzati nel database ecoinvent. L'utente ti invierà un termine (es. 'PVC', 'nave', 'lorry'). "
    "Tu devi restituire una lista di 3-5 stringhe di ricerca iper-precise in INGLESE.\n"
    "Regole per MATERIALI:\n"
    "1) Espandi sempre gli acronimi nel nome IUPAC "
    "(es. PVC -> polyvinyl chloride, PET -> polyethylene terephthalate).\n"
    "2) Includi l'acronimo stesso come fallback.\n"
    "3) Se è una plastica, aggiungi varianti comuni nei DB come 'granulate' o 'suspension polymerised'.\n"
    "4) Usa rigorosamente la tassonomia e le convenzioni di spelling dell'Inglese Britannico (UK) per i materiali industriali. Ad esempio, traduci sempre 'fiber' in 'fibre', 'molding' in 'moulding', etc., per garantire che l'algoritmo fuzzy superi la soglia di sbarramento dello 0.85.\n"
    "5) Per materiali BIO-BASED e LEGNO: Ecoinvent usa la convenzione SOSTANTIVO+AGGETTIVO (es. 'sawnwood, softwood' NON 'softwood sawnwood', 'roundwood, azobe' NON 'azobe roundwood'). Genera SEMPRE i termini in questo ordine: categoria prima, specificità dopo.\n"
    "Regole per MEZZI DI TRASPORTO:\n"
    "Se il termine è un mezzo di trasporto (ship, lorry, truck, aircraft, nave, camion, aereo, ferry, ecc.), "
    "NON usare le regole per i materiali. Invece, traduci il mezzo nelle sigle di trasporto merci ecoinvent:\n"
    "- 'ship' / 'nave' / 'sea' / 'ferry' -> "
    "['transport, freight, sea, transoceanic ship', 'transport, freight, sea', 'transoceanic ship', 'freight, sea, container ship']\n"
    "- 'lorry' / 'truck' / 'camion' / 'road' -> "
    "['transport, freight, lorry', 'articulated lorry', 'transport, freight, road', 'lorry >32 metric ton']\n"
    "- 'aircraft' / 'aereo' / 'air' / 'airplane' -> "
    "['transport, freight, air', 'freight, air, express freight', 'air freight', 'freight air transport']\n"
    "Non scrivere spiegazioni, restituisci solo una lista JSON di stringhe "
    "nel campo 'queries'."
)

_LLM_PROCESS_EXPANDER_SYSTEM_PROMPT = (
    "Sei un esperto di ingegneria industriale e analista LCA con conoscenza approfondita del database ecoinvent. "
    "Il tuo compito è tradurre nomi di PROCESSI DI MANIFATTURA o verbi industriali (es. 'injection moulding', 'extrusion', 'cutting', 'welding') "
    "nei termini tecnici esatti utilizzati nel database ecoinvent. L'utente ti invierà un termine. "
    "Tu devi restituire una lista di 3-5 stringhe di ricerca iper-precise in INGLESE.\n"
    "Regole per PROCESSI:\n"
    "1) Genera tassonomie di processo/verbi anziché formule chimiche o materiali.\n"
    "2) Usa verbi o sostantivi tipici dei processi industriali di ecoinvent (es. 'extrusion', 'moulding', 'laser cutting').\n"
    "3) Usa rigorosamente l'Inglese Britannico (UK) (es. 'moulding' con 'u', non 'molding').\n"
    "4) Aggiungi varianti comuni nei DB come 'processing, plastic' o 'moulding, state-of-the-art'.\n"
    "5) Includi il nome del processo originale e varianti con e senza virgole.\n"
    "Non scrivere spiegazioni, restituisci solo una lista JSON di stringhe nel campo 'queries'."
)


class SearchQueryList(BaseModel):
    """Schema Pydantic per l'output strutturato dell'LLM Semantic Expander."""
    queries: list[str]


async def generate_search_queries(material_name: str, entity_type: str = "material") -> list[str]:
    """Usa l'LLM per espandere material_name in termini di ricerca Ecoinvent-precisi.

    Invocato in modalità asincrona (ainvoke) per non bloccare l'event loop.
    I risultati sono cachati in _llm_expansion_cache per la durata della sessione:
    se lo stesso materiale o processo compare su più componenti del BOM, l'LLM viene
    interrogato una sola volta.

    Fallback deterministico: se l'LLM va in timeout o restituisce un errore,
    la funzione ricade su un fallback deterministico, senza sollevare eccezioni verso i caller.

    Args:
        material_name: Nome del materiale o processo normalizzato (es. "PVC", "polypropylene", "injection moulding").
        entity_type: "material" per i materiali o "process" per i processi di manifattura.

    Returns:
        Lista di stringhe di ricerca iper-precise per il DB LCA (es.
        ["polyvinyl chloride", "pvc", "polyvinylchloride", "suspension polymerised pvc"]).
    """
    import logging
    _log = logging.getLogger(__name__)

    cache_key = f"{entity_type}:{material_name.strip().lower()}"
    if cache_key in _llm_expansion_cache:
        return _llm_expansion_cache[cache_key]

    try:
        from core.llm_factory import ModelFactory
        from langchain_core.messages import SystemMessage as _SM, HumanMessage as _HM

        llm = ModelFactory.get_model()
        chain = llm.with_structured_output(SearchQueryList)
        
        system_prompt = _LLM_PROCESS_EXPANDER_SYSTEM_PROMPT if entity_type == "process" else _LLM_EXPANDER_SYSTEM_PROMPT
        
        result: SearchQueryList = await chain.ainvoke([
            _SM(content=system_prompt),
            _HM(content=material_name),
        ])
        expanded: list[str] = result.queries
        if not expanded:
            raise ValueError("LLM Expander ha restituito una lista vuota")
    except Exception as exc:
        _log.warning(
            "[LLMExpander] Fallback statico per '%s' (tipo: %s, LLM non disponibile): %s",
            material_name, entity_type, exc,
        )
        if entity_type == "process":
            # Fallback per i processi
            expanded = [material_name, f"{material_name}ing", f"{material_name} moulding", f"{material_name} extrusion"]
        else:
            expanded = _expand_semantic_terms(material_name.lower())

    _llm_expansion_cache[cache_key] = expanded
    return expanded


# ---------------------------------------------------------------------------
# DIRETTIVA 1: Contextual Intent Classifier
#
# Determina l'intenzione della query in modo DETERMINISTICO (zero LLM).
# Output: "plastic_material" | "extraction_activity" | "generic"
#
# LOGICA:
#   1. Se l'input contiene keyword di ESTRAZIONE → "extraction_activity" (priorità)
#   2. Elif contiene keyword di PLASTICA/PRODOTTO → "plastic_material"
#   3. Else → "generic" (nessun modificatore aggiunto)
#
# PRIORITÀ ESTRAZIONE: evita false-positive su query ibride tipo "petroleum PET".
# In quel caso il filtro anti-petroleum NON viene applicato.
# ---------------------------------------------------------------------------

# Segnali che indicano "voglio modellare un materiale plastico / prodotto finito"
_PLASTIC_MATERIAL_INTENT: frozenset = frozenset({
    # Acronimi e nomi tecnici polimeri
    "pet", "pete", "polyethylene terephthalate",
    "hdpe", "ldpe", "lldpe",
    "pvc", "polyvinyl chloride",
    "pp", "polypropylene", "polipropilene",
    "pe", "polyethylene", "polietilene",
    "ps", "polystyrene",
    "abs", "acrylonitrile butadiene styrene",
    "pla", "polylactic acid",
    "nylon", "polyamide",
    "plastic", "plastica", "polymer", "polimero",
    # Prodotti finiti in plastica
    "bottle", "bottiglia", "container", "cap", "tappo",
    "granulate", "granulato", "pellet",
    "packaging", "imballaggio",
    "film", "sheet", "foglio",
})

# Segnali che indicano "voglio modellare un'attività estrattiva / industria fossile"
_EXTRACTION_ACTIVITY_INTENT: frozenset = frozenset({
    "petroleum", "petrolio",
    "crude oil", "greggio", "olio grezzo",
    "natural gas", "gas naturale",
    "offshore", "onshore",
    "extraction", "estrazione",
    "drilling", "perforazione",
    "refinery", "raffineria", "refining",
    "oil well", "pozzo petrolifero",
    "fossil fuel", "combustibile fossile",
    "liquefied natural gas", "lng",
    "upstream", "downstream",
})


def classify_search_intent(label_lower: str) -> str:
    """Classifica l'intenzione della query in modo deterministico (zero LLM).

    Algoritmo:
    - Tokenizza label_lower in parole e bigrammi (per catturare 'crude oil', 'natural gas').
    - Controlla intersezione con _EXTRACTION_ACTIVITY_INTENT (priorità alta).
    - Poi controlla intersezione con _PLASTIC_MATERIAL_INTENT.
    - Default: "generic" (nessun modificatore applicato).

    Args:
        label_lower: la query normalizzata in lowercase.

    Returns:
        "extraction_activity" | "plastic_material" | "generic"
    """
    tokens = label_lower.split()
    # Genera bigrammi per catturare frasi composte (es. "crude oil", "natural gas")
    bigrams = [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]
    all_tokens: set = set(tokens) | set(bigrams)

    # PRIORITÀ ESTRAZIONE: se l'utente menziona termini fossili/estrattivi,
    # non applicare filtri anti-petroleum (potrebbe essere un use-case legittimo)
    if all_tokens & _EXTRACTION_ACTIVITY_INTENT:
        return "extraction_activity"

    # INTENT PLASTICA: termini di polimeri o prodotti finiti in plastica
    if all_tokens & _PLASTIC_MATERIAL_INTENT:
        return "plastic_material"

    return "generic"


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
    # Cache management
    # ------------------------------------------------------------------

    def flush_match_cache(self) -> None:
        """Evict ALL entries from _match_cache and _search_cache.

        Call this at the start of every new agent session to guarantee
        that no stale coroutine objects left by pre-fix executions can
        be returned to callers.  The DataFrame (_df) is expensive to
        reload and is intentionally kept intact.
        """
        self._match_cache.clear()
        self._search_cache.clear()

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

    async def find_closest_match(
        self,
        label: Optional[str] = None,
        location: Optional[str] = None,
        threshold: float = 0.65,
        target_product: Optional[str] = None,
        target_geography: Optional[str] = None,
        task_type: str = "optimization",
        has_transport: Optional[bool] = None,
        thought_log: Optional[list] = None,
        is_recycled: bool = False,
        entity_type: str = "material",
    ) -> Optional[dict]:
        """Find the closest matching material using a 4-stage search logic.

        STADIO 0: Semantic Normalization (The "Understand" Phase) -> NUOVO
        STADIO 1: Espansione Semantica (The "Think" Phase)
        STADIO 2: Ricerca Fuzzy con Filtro Dinamico (The Best-Match Logic)
        STADIO 3: Fallback Intelligente (Geographic Expansion)

        Args:
            is_recycled: If True (from Pydantic BOMComponent), disables virgin filters
                         and applies +0.3 bonus to secondary/scrap/recycled records.
                         This is the ONLY source of truth for recycled detection.
        """
        actual_label = target_product if target_product is not None else label
        actual_location = target_geography if target_geography is not None else location

        if not actual_label:
            return None

        # Clean actual_label to remove acronyms in parentheses like "(rEPS)" or "(rPP)"
        import re
        actual_label = re.sub(r"\s*\([^)]*\)", "", actual_label).strip()
        # NOTE: is_recycled parameter is the sole source of truth for recycled detection.
        # We do NOT infer recycled intent from label text-scan — that caused false positives.

        # ── STADIO 0: Semantic Normalization Pipeline ─────────────────────
        # Normalizza l'input PRIMA di qualsiasi ricerca:
        #   - Rimuove contenuti di contenitori (acqua, vino…)
        #   - Estrae l'identità chimica core (PVC, PET, polypropylene…)
        #   - Deduce la classe geometrica (profile, hollow, flat, solid…)
        norm_result: NormalizationResult = _semantic_normalizer.normalize(actual_label)
        normalized_label = norm_result.base_material
        geometry_hint   = norm_result.geometry_hint

        # Log auditabile della normalizzazione
        if normalized_label.lower() != actual_label.lower().strip():
            print(
                f"[NORMALIZER] '{actual_label}' → base_material='{normalized_label}'"
                f"{', geometry_hint=' + repr(geometry_hint) if geometry_hint else ''}"
                f"{', inferred=' + str(norm_result.inferred_material) if norm_result.inferred_material else ''}"
                f"{', strong_modifiers=' + str(norm_result.strong_modifiers) if norm_result.strong_modifiers else ''}"
                f"{', stripped=' + str(norm_result.stripped_tokens) if norm_result.stripped_tokens else ''}"
            )

        # label_lower ora punta al materiale normalizzato (es. "PVC" invece di "PVC rigido per edilizia")
        label_lower = normalized_label.lower().strip()
        exact_loc = actual_location.strip() if actual_location else ""
        canonical_loc = _normalise_location(actual_location)

        # Cache key usa l'etichetta originale per evitare collisioni tra input diversi
        cache_key = (
            f"{actual_label.lower().strip()}__{exact_loc}__{canonical_loc}"
            f"_semantic_{task_type}_{has_transport}_{is_recycled}"
        )
        if cache_key in self._match_cache:
            cached = self._match_cache[cache_key]
            # Guard: evict stale coroutine objects left by pre-fix calls without await.
            # A valid cached value is either None or a dict; a coroutine means the
            # entry was stored before 'await' was added and must be recomputed.
            import inspect as _inspect
            if not _inspect.iscoroutine(cached):
                return cached
            # Coroutine found in cache — close it to avoid ResourceWarning and recompute.
            cached.close()
            del self._match_cache[cache_key]

        if self._df.empty:
            return None

        # ── STADIO 1: Espansione Semantica ───────────────────────────────
        # ── STADIO 1 AGGIORNATO: LLM Dynamic Expansion (async, con cache) ────
        # L'LLM viene sempre interrogato per garantire la massima precisione
        # semantica. In caso di errore/timeout, ricade su _expand_semantic_terms.
        if entity_type != "material":
            search_terms = await generate_search_queries(normalized_label, entity_type=entity_type)
        else:
            search_terms = await generate_search_queries(normalized_label)
        _expander_msg = (
            f"[LLM Semantic Expander] Materiale '{normalized_label}' "
            f"tradotto nei termini di ricerca: {search_terms}"
        )
        print(_expander_msg)
        if thought_log is not None:
            thought_log.append(_expander_msg)

        # ── STADIO 3: Fallback Geografico (Outer Loop) ───────────────────
        target_loc = exact_loc if exact_loc else (canonical_loc if canonical_loc else "Global")

        geographies_to_try = [target_loc]
        if target_loc != "Global":
            if canonical_loc and canonical_loc != target_loc:
                geographies_to_try.append(canonical_loc)
            geographies_to_try.extend(_get_regional_bin(canonical_loc if canonical_loc else target_loc))
        else:
            geographies_to_try.extend(["Global", "Rest-of-World"])

        # Remove duplicates preserving order
        seen_geo: set = set()
        geographies_to_try = [g for g in geographies_to_try if not (g in seen_geo or seen_geo.add(g))]

        result: Optional[dict] = None

        # is_recycled viene propagato dal campo Pydantic — è l'unica fonte di verità.
        # NON si usa text-scan sul label per rilevare il riciclato.
        pass1_threshold = 0.80 if is_recycled else 0.85

        # Pass 1: Virgin-First Logic (or Recycled-First Logic quando is_recycled=True)
        for i, geo in enumerate(geographies_to_try):
            if i == 0:
                print(f"[DEBUG] Stage 0: Cerco '{label_lower}' in {geo}...")
            else:
                print(f"[DEBUG] Nessun match in {geographies_to_try[i-1]}. Passo a {geo}...")

            is_fallback = (geo != canonical_loc) if canonical_loc else False
            row = self._search_best_match(
                search_terms, actual_label.lower().strip(), geo, task_type, pass1_threshold, self._df,
                require_virgin=True, has_transport=has_transport,
                geometry_hint=geometry_hint,
                is_recycled=is_recycled,
            )
            if row is not None:
                result = self._build_result(row, location_fallback_used=is_fallback, requested_location=exact_loc, pass_number=1)
                self._match_cache[cache_key] = result
                return result

        # Pass 1.5: Fallback automatico con soglia a 0.75
        for i, geo in enumerate(geographies_to_try):
            print(f"[DEBUG] Stage 1.5: Fallback soglia 0.75 per '{label_lower}' in {geo}...")
            is_fallback = (geo != canonical_loc) if canonical_loc else False
            row = self._search_best_match(
                search_terms, actual_label.lower().strip(), geo, task_type, 0.75, self._df,
                require_virgin=True, has_transport=has_transport,
                geometry_hint=geometry_hint,
                is_recycled=is_recycled,
            )
            if row is not None:
                result = self._build_result(row, location_fallback_used=is_fallback, requested_location=exact_loc, pass_number=1)
                self._match_cache[cache_key] = result
                return result

        # Pass 2: Fallback Standard se non esiste materiale virgin (soglia 0.70)
        for i, geo in enumerate(geographies_to_try):
            is_fallback = (geo != canonical_loc) if canonical_loc else False
            row = self._search_best_match(
                search_terms, actual_label.lower().strip(), geo, task_type, 0.70, self._df,
                require_virgin=False, has_transport=has_transport,
                geometry_hint=geometry_hint,
                is_recycled=is_recycled,
            )
            if row is not None:
                result = self._build_result(row, location_fallback_used=is_fallback, requested_location=exact_loc, pass_number=2)
                self._match_cache[cache_key] = result
                return result

        # Hard Stop — nessun match trovato in nessuna geografia
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
        require_virgin: bool = False, has_transport: Optional[bool] = None,
        geometry_hint: Optional[str] = None,
        is_recycled: bool = False,
    ) -> Optional[pd.Series]:
        """Return the best-matching DataFrame row evaluating all semantic terms with dynamic filters.

        Args:
            is_recycled: If True, disables virgin-first filter and applies recycled-content
                         score bonuses. This is the SOLE source of truth — no text-scan.
        """
        print(f"[DEBUG _search_best_match] Analisi query: '{original_label}' in loc: '{loc}', termini: {search_terms}")
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
            
            # ── TICKET 3: Boolean Gating Stretto (Nessuna inferenza testuale) ──────────
            # is_recycled proviene dal campo Pydantic BOMComponent — unica fonte di verità.
            # Nessun text-scan su original_label: elimina falsi positivi.
            if not is_recycled and require_virgin:
                if re.search(r"\bwaste\b|\bscrap\b|\bscarto\b|\brecycled\b|\bsecondary\b", name_combined):
                    print(f"[DEBUG] Trovato {row['outputname']} in {loc} -> Scartato (Filtro Vergine Semantico)")
                    continue
            elif not is_recycled:
                # is_recycled=False: scarta waste/scrap assoluto anche in pass2
                if re.search(r"\bwaste\b|\bscrap\b|\bscarto\b", name_combined):
                    print(f"[DEBUG] Trovato {row['outputname']} in {loc} -> Scartato (Filtro Waste Assoluto)")
                    continue
            else:
                # is_recycled=True: disattiva require_virgin, cerca solo materiali secondari/scrap
                if not re.search(r"\bwaste\b|\bscrap\b|\bscarto\b|\brecycled\b|\bsecondary\b", name_combined):
                    print(f"[DEBUG] Trovato {row['outputname']} in {loc} -> Scartato (is_recycled=True ma record è vergine)")
                    continue
            # Legno/Naturali non hanno limiti (nessun else break)
            
            valid_indices.append(idx)
            
        candidates = candidates.loc[valid_indices]

        if candidates.empty:
            print(f"[DEBUG] Geografia {loc} scartata (0 candidati validi dopo il filtro).")
            return None

        # DIRETTIVA 1: Calcola l'intent UNA SOLA VOLTA per questa query (fuori dal loop)
        search_intent = classify_search_intent(original_label)
        if search_intent != "generic":
            print(f"[INTENT] classify_search_intent('{original_label}') -> '{search_intent}'")

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
            
            # GARANZIA DI IDENTITÀ (Ticket 2)
            primary_term = search_terms[0].lower()
            
            # Helper to check if a term matches a string with word boundaries
            import re as _re_match
            def has_word_boundary_match(term: str, target: str) -> bool:
                if len(term) <= 3:
                    # usa word-boundary match per stringhe <= 3 caratteri come "PP" o "PE"
                    pattern = r"\b" + _re_match.escape(term) + r"\b"
                    return bool(_re_match.search(pattern, target))
                else:
                    return term in target
            
            # primary_term must appear in out_name OR proc_name
            matches_primary_out = has_word_boundary_match(primary_term, out_name)
            matches_primary_proc = has_word_boundary_match(primary_term, proc_name)
            
            if not (matches_primary_out or matches_primary_proc):
                # Fallback: controlla se c'è almeno un termine nei primi 3 search_terms che matchi out_name
                matched_any_fallback = False
                for t in search_terms[:3]:
                    if has_word_boundary_match(t, out_name):
                        matched_any_fallback = True
                        break
                if not matched_any_fallback:
                    print(f"[DEBUG] Scartato '{row['outputname']}' -> Fallito primary identity guard per '{primary_term}'")
                    continue

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
                
            # Bonus Recycled: applicato SOLO se is_recycled=True (campo Pydantic strutturato)
            # NON basato su text-scan — elimina falsi positivi.
            if is_recycled and any(term in name_combined for term in ["recycled", "riciclato", "secondary", "scrap", "waste"]):
                score += 0.3
                print(f"[RECYCLED] Bonus +0.3 a '{row['outputname']}' (is_recycled=True, record secondario)")
            elif is_recycled and any(term in name_combined for term in ["primary", "virgin", "unalloyed", "production"]):
                score -= 0.5
                print(f"[RECYCLED] Malus -0.5 a '{row['outputname']}' (is_recycled=True ma record è vergine/primary)")
                
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
                if has_transport is True:
                    score -= 0.3 # Penalize market dataset if transport is already provided
                else:
                    score += 0.3 # Boost market dataset if no transport is provided
            elif any(term in name_combined for term in bonus_terms_non_market):
                if has_transport is True:
                    score += 0.4 # Boost production dataset if transport is provided
                else:
                    score += 0.2 # Standard bonus
                
            if any(term in name_combined for term in penalty_terms):
                # Se geometry_hint corrisponde al tipo del record, NON penalizzare:
                # es. "tubo in PVC" ha geometry_hint='profile' → il record "pipe" è corretto
                if geometry_hint and any(kw in name_combined for kw in _GEOMETRY_SIGNALS.get(geometry_hint, ())):
                    pass  # Geometria richiesta: nessuna penalità
                else:
                    score -= 0.5
                    print(f"[DEBUG] Declassato '{row['outputname']}' (-0.5) perché processo/prodotto finito/veicolo. Cerco materia prima...")

            # Geometry Hint Bonus (dal SemanticNormalizer)
            # Se l'utente ha implicato una geometria (es. "tubo" → "profile"),
            # premiamo i record che matchano quella classe geometrica.
            if geometry_hint:
                hint_keywords = _GEOMETRY_SIGNALS.get(geometry_hint, ())
                if any(kw in name_combined for kw in hint_keywords):
                    score += 0.15
                    print(f"[GEOMETRY] Bonus +0.15 a '{row['outputname']}' (geometry_hint='{geometry_hint}' confermato)")

            # DIRETTIVA 1: Score Modifier Contestuale
            # Agisce SOLO sullo score — non elimina il record dal pool candidati.
            # Mantiene la reversibilità e l'auditabilità dei risultati.
            if search_intent == "plastic_material":
                # Penalizza dataset petroliferi/estrattivi in contesto plastica.
                # -0.6 supera qualsiasi bonus possibile (max bonus attuale: +0.4)
                # quindi un record petrolifero non può vincere, ma resta auditabile.
                _petro_signals = {
                    "petroleum", "petrol", "crude", "offshore",
                    "oil field", "natural gas", "refin", "fossil",
                }
                if any(signal in name_combined for signal in _petro_signals):
                    score -= 0.6
                    print(
                        f"[INTENT] plastic_material: penalizzato '{row['outputname']}' "
                        f"(-0.6, segnali petroliferi in contesto plastica)"
                    )
            elif search_intent == "extraction_activity":
                # Premia dataset estrattivi in contesto estrazione.
                _extract_signals = {
                    "petroleum", "crude", "extraction", "offshore",
                    "natural gas", "oil production", "refin",
                }
                if any(signal in name_combined for signal in _extract_signals):
                    score += 0.25
                    print(
                        f"[INTENT] extraction_activity: premiato '{row['outputname']}' "
                        f"(+0.25, segnali estrattivi in contesto estrazione)"
                    )
            # "generic": nessuna modifica — comportamento attuale invariato

            if score > best_score:
                best_score = score
                best_row = row

        if best_score >= threshold and best_row is not None:
            print(f"[DEBUG MATCH TROVATO] ID da cercare nel JSON: '{best_row.get('id')}' | Processo: '{best_row.get('processname')}' | Location: '{best_row.get('location')}' | Impatto: {best_row.get('climatechangeimpact')}")
            return best_row
        elif best_row is not None:
            print(
                f"[MISS FAIL] '{best_row['outputname']}' | {loc} "
                f"| Score: {best_score:.3f} < {threshold} "
                f"| ID: {best_row['id']}"
            )
            
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
            "unit_of_measure":       self._get_unit_of_measure(row),
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

    def _get_unit_of_measure(self, row: pd.Series) -> str:
        """Return the functional Unit of Measure (UOM) for *row*.

        Letta dall'ultima colonna di ``dataset_ecoinvent_perfetto.xlsx``
        (``unitOfMeasure`` → normalizzata in ``unitofmeasure``). Indica a
        quale unità funzionale è riferito ``climatechangeimpact`` (es. 'kg',
        'unit', 'm2', 'm3', 'kWh'...). È la base per scalare correttamente
        l'impatto nel motore LCA (vedi ``_resolve_scale_factor`` in
        ``agents.nodes``) evitando errori di magnitudo quando l'unità non è
        una misura di massa. Default 'kg' se la colonna manca o è vuota —
        coerente con la stragrande maggioranza dei record del dataset.
        """
        if "unitofmeasure" in row.index:
            val = str(row["unitofmeasure"]).strip()
            if val and val.lower() != "nan":
                return val
        return "kg"

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
        """Return LCA impact scores from dataset_ecoinvent_perfetto.xlsx.

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
            "unit_of_measure":      self._get_unit_of_measure(r),
            "lifespan_years":       10.0,
        }
