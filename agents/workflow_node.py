import json
from typing import Optional
import logging
# pyrefly: ignore [missing-import]
from langchain_core.messages import HumanMessage, SystemMessage
from agents.state import AgentState
from core.llm_factory import ModelFactory
from data.provider_factory import get_lca_provider
from agents.schemas import WorkflowAndBOMResponse
from agents.nodes import _invoke_structured
import asyncio
import unicodedata

def normalize_text(text: str) -> str:
    """Rimuove accenti e normalizza la stringa (es. 'Perù' -> 'Peru')."""
    if not text:
        return ""
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn').strip()

logger = logging.getLogger(__name__)

GEOMETRY_MAPPING = {
    "Corpi Cavi": "Blow moulding",
    "Pezzi Pieni Complessi": "Injection moulding",
    "Film": "Extrusion (film)",
    "Profili/Tubi": "Extrusion",
}

# ---------------------------------------------------------------------------
# DIRETTIVA 3: Component-Type → Process Mapper (DETERMINISTICO)
#
# Priorità ASSOLUTA su determine_manufacturing_process() basata su geometria.
# L'LLM identifica solo il componente; il processo è hardcoded da questa lista.
#
# STRUTTURA: lista ordinata di (keywords_tuple, process_string).
# MATCHING: any(kw in component_name_lower for kw in keywords).
# ORDINE: le tuple più specifiche precedono quelle generiche.
#   (es. 'bottle cap' deve matchare 'cap' → Injection PRIMA di 'bottle' → Blow)
# ---------------------------------------------------------------------------
COMPONENT_PROCESS_MAPPER: list[tuple[tuple, str]] = [
    # ── Chiusure e tappi → Injection Moulding (geometria piana/solida, mai cava)
    (
        ("cap", "tappo", "lid", "closure", "stopper", "plug",
         "coperchio", "chiusura", "bouchon"),
        "Injection moulding",
    ),
    # ── Bottiglie, contenitori cavi → Blow Moulding
    (
        ("bottle", "bottiglia", "container", "flask", "jug",
         "canister", "boccetta", "flacon", "recipient"),
        "Blow moulding",
    ),
    # ── Tubi, pipe, profili → Extrusion
    (
        ("pipe", "tube", "tubo", "profile", "profilo",
         "conduit", "duct", "condotto", "sezione"),
        "Extrusion",
    ),
    # ── Film, fogli, membrane → Extrusion (film)
    (
        ("film", "sheet", "foglio", "membrane", "membrana",
         "wrap", "pellicola", "packaging film"),
        "Extrusion (film)",
    ),
    # ── Etichette → Printing
    (
        ("label", "etichetta", "sticker", "tag"),
        "Printing",
    ),
    # ── Viti, bulloni, fastener → Metal working
    (
        ("screw", "vite", "bolt", "bullone", "nut", "dado",
         "fastener", "rivet", "rivetto", "washer", "rondella"),
        "Metal working",
    ),
    # ── Guarnizioni, O-ring → Injection Moulding
    (
        ("gasket", "guarnizione", "seal", "o-ring", "sealing"),
        "Injection moulding",
    ),
    # ── Frame, telai strutturali → Metal working
    (
        ("frame", "telaio", "chassis", "bracket", "staffa",
         "support", "supporto"),
        "Metal working",
    ),
    # ── Cuscinetti → Metal working
    (
        ("bearing", "cuscinetto", "bushing"),
        "Metal working",
    ),
]


def get_process_by_component_name(component_name: str) -> Optional[str]:
    """Lookup deterministico: dato il nome del componente, restituisce il processo.

    Scorre COMPONENT_PROCESS_MAPPER in ordine e ritorna il primo match.
    La complessità è O(k) dove k = numero totale di keyword — < 1ms.

    Args:
        component_name: il nome del componente dalla BOM (campo 'name').

    Returns:
        Stringa del processo manifatturiero, o None se non mappato.
        None → il chiamante usa il fallback geometrico (+ logger.warning).
    """
    name_lower = component_name.lower().strip()
    for keywords, process in COMPONENT_PROCESS_MAPPER:
        if any(kw in name_lower for kw in keywords):
            return process
    return None


# ---------------------------------------------------------------------------
# AZIONE 2: Process Resolver — ragiona su classi, non su nomi
# ---------------------------------------------------------------------------
# Sostituisce determine_manufacturing_process() con una logica a due
# dimensioni ortogonali:
#   1. material_class: categoria chimica del materiale (thermoplastic, metal…)
#   2. geometry_class:  forma fisica dell'oggetto (profile, hollow, flat…)
#
# Il processo viene risolto con un semplice lookup su _PROCESS_RESOLUTION_TABLE.
# COMPONENT_PROCESS_MAPPER mantiene la PRIORITÀ ASSOLUTA: il Resolver viene
# chiamato solo come fallback quando il mapper non trova un match.
# ---------------------------------------------------------------------------

# ── Classi di materiale ───────────────────────────────────────────────────
_MATERIAL_CLASS_MAP: dict[str, list[str]] = {
    "thermoplastic": [
        "pvc", "polyvinyl chloride", "cloruro di polivinile",
        "polypropylene", "polipropilene",
        "polyethylene", "polietilene", "hdpe", "ldpe", "lldpe",
        "polyethylene terephthalate", "polietilene tereftalato",
        "acrylonitrile butadiene styrene", "acrylonitrile",
        "polystyrene", "polistirene",
        "polylactic", "acido polilattico",
        "nylon", "polyamide", "poliammide",
        "polycarbonate", "polycarbonato",
        "polymethyl methacrylate", "acrylic",
        "thermoplastic polyurethane",
        "polyoxymethylene",
        "plastic", "plastica", "polymer", "polimero",
        # Short acronyms: prefix with WB: marker so _classify_material uses word-boundary match
        "WB:pp", "WB:pe", "WB:pet", "WB:abs",
        "WB:ps", "WB:pla", "WB:pa", "WB:pc",
        "WB:pmma", "WB:tpu", "WB:pom",
        "WB:eps", "WB:xps",
    ],
    "thermoset": [
        "epoxy", "resina epossidica",
        "resin", "resina",
        "polyurethane", "poliuretano", "pu",
        "phenolic", "fenoliche",
        "unsaturated polyester", "poliestere insaturo",
        "composite", "composito",
    ],
    "metal": [
        "steel", "acciaio",
        "stainless steel", "acciaio inox",
        "aluminium", "aluminum", "alluminio",
        "copper", "rame",
        "brass", "ottone",
        "iron", "ferro",
        "cast iron", "ghisa",
        "titanium", "titanio",
        "zinc", "zinco",
        "lead", "piombo",
        "metal", "metallo", "alloy", "lega",
    ],
    "elastomer": [
        "rubber", "gomma",
        "natural rubber", "gomma naturale",
        "silicone",
        "elastomer", "elastomero",
        "epdm", "nbr", "sbr",
        "neoprene",
    ],
    "wood_based": [
        "wood", "legno",
        "timber",
        "mdf", "plywood", "compensato",
        "bamboo", "bambù",
        "particle board", "chipboard",
    ],
    "glass_ceramic": [
        "glass", "vetro",
        "ceramic", "ceramica",
        "porcelain", "porcellana",
        "clay", "argilla",
    ],
    "textile": [
        "cotton", "cotone",
        "wool", "lana",
        "polyester", "poliestere",
        "hemp", "canapa",
        "linen", "lino",
        "silk", "seta",
        "fabric", "tessuto",
        "fiber", "fibre", "fibra",
    ],
}

# ── Classi di geometria → parole chiave per rilevamento ──────────────────
# (speculari a _GEOMETRY_SIGNALS in csv_lca_client.py, ma standalone)
_GEOMETRY_CLASS_SIGNALS: dict[str, tuple[str, ...]] = {
    "profile":  ("tubo", "pipe", "profilo", "profile", "barra", "rod",
                 "condotto", "duct", "sezione", "section", "conduit",
                 "tubing", "tubolaro", "profili/tubi"),
    "hollow":   ("bottiglia", "bottle", "contenitore", "container",
                 "flacone", "flask", "boccetta", "jug", "canister",
                 "barattolo", "jar", "recipient", "corpi cavi"),
    "flat":     ("foglio", "sheet", "film", "pellicola", "lastra",
                 "slab", "board", "pannello", "panel", "membrane",
                 "membrana", "wrap"),
    "solid":    ("tappo", "cap", "blocco", "block", "pezzo", "part",
                 "lid", "coperchio", "dado", "vite", "screw", "bolt",
                 "pezzi pieni complessi"),
    "fabric":   ("tessuto", "fabric", "fibra", "fiber", "fibre",
                 "filato", "yarn", "tela"),
}

# Mapping dalle label geometriche LLM (Step 4) → geometry_class interna
_LLM_GEOMETRY_LABEL_MAP: dict[str, str] = {
    "corpi cavi":            "hollow",
    "pezzi pieni complessi": "solid",
    "film":                  "flat",
    "profili/tubi":          "profile",
    "fibra":                 "fabric",
    "schiuma":               "solid",   # foam moulding → trattato come solid
}

# ── Tabella di risoluzione (material_class, geometry_class) → processo ───
_PROCESS_RESOLUTION_TABLE: dict[tuple[str, str], str] = {
    # Termoplastici
    ("thermoplastic", "profile"):  "Extrusion",
    ("thermoplastic", "hollow"):   "Blow moulding",
    ("thermoplastic", "flat"):     "Extrusion (film)",
    ("thermoplastic", "solid"):    "Injection moulding",
    ("thermoplastic", "fabric"):   "Fibre extrusion",
    # Termoindurenti
    ("thermoset",     "solid"):    "Resin casting",
    ("thermoset",     "flat"):     "Lamination",
    ("thermoset",     "hollow"):   "Resin casting",
    ("thermoset",     "profile"):  "Pultrusion",
    # Metalli
    ("metal",         "profile"):  "Section bar rolling",
    ("metal",         "flat"):     "Metal sheet rolling",
    ("metal",         "solid"):    "Metal working",
    ("metal",         "hollow"):   "Metal working",
    ("metal",         "fabric"):   "Metal working",
    # Elastomeri
    ("elastomer",     "solid"):    "Injection moulding",
    ("elastomer",     "flat"):     "Compression moulding",
    ("elastomer",     "profile"):  "Extrusion",
    ("elastomer",     "hollow"):   "Compression moulding",
    # Legno
    ("wood_based",    "profile"):  "Woodworking",
    ("wood_based",    "flat"):     "Woodworking",
    ("wood_based",    "solid"):    "Woodworking",
    ("wood_based",    "hollow"):   "Woodworking",
    # Vetro / ceramica
    ("glass_ceramic", "hollow"):   "Glass blowing",
    ("glass_ceramic", "flat"):     "Float glass production",
    ("glass_ceramic", "solid"):    "Ceramic firing",
    # Tessili
    ("textile",       "fabric"):   "Textile weaving",
    ("textile",       "profile"):  "Fibre extrusion",
    ("textile",       "flat"):     "Textile weaving",
}


def _classify_material(material: str) -> Optional[str]:
    """Classifica il materiale nella sua classe chimica.

    Scorre _MATERIAL_CLASS_MAP e restituisce la prima classe che matcha.
    Matching:
    - Keyword prefissate con 'WB:' → word-boundary regex (per acronimi corti come 'pp', 'pe')
      evita che 'copper' matchi 'pp' come substring.
    - Keyword normali → substring case-insensitive.

    Returns:
        Stringa della classe materiale (es. "thermoplastic"), o None se sconosciuto.
    """
    import re as _re
    mat_lower = material.lower().strip()
    for mat_class, keywords in _MATERIAL_CLASS_MAP.items():
        for kw in keywords:
            if kw.startswith("WB:"):
                # Word-boundary match per acronimi corti
                acronym = kw[3:]
                if _re.search(r"\b" + _re.escape(acronym) + r"\b", mat_lower):
                    return mat_class
            else:
                if kw in mat_lower:
                    return mat_class
    return None


def _classify_geometry(
    geometry_hint: Optional[str],
    geometry_label: Optional[str],
    component_name: Optional[str] = None,
) -> str:
    """Determina la classe geometrica con priorità decrescente.

    Priorità:
      1. geometry_hint dal SemanticNormalizer (già estratto dall'input utente)
      2. geometry_label dal LLM (es. "Corpi Cavi", "Profili/Tubi")
      3. Segnali nel nome del componente (fallback lessicale)
      4. Default: "solid"

    Returns:
        Stringa della classe geometrica ("profile"|"hollow"|"flat"|"solid"|"fabric")
    """
    # 1. Hint dal normalizzatore semantico (già classificato)
    if geometry_hint and geometry_hint in _GEOMETRY_CLASS_SIGNALS:
        return geometry_hint

    # 2. Label LLM → mappa alla classe interna
    if geometry_label:
        mapped = _LLM_GEOMETRY_LABEL_MAP.get(geometry_label.lower().strip())
        if mapped:
            return mapped

    # 3. Segnali nel nome del componente
    if component_name:
        name_lower = component_name.lower()
        for geom_class, keywords in _GEOMETRY_CLASS_SIGNALS.items():
            if any(kw in name_lower for kw in keywords):
                return geom_class

    return "solid"  # Default sicuro


def resolve_process(
    material: str,
    geometry_hint: Optional[str] = None,
    geometry_label: Optional[str] = None,
    component_name: Optional[str] = None,
) -> str:
    """Risolve il processo manifatturiero ragionando su classi, non su nomi.

    Sostituisce determine_manufacturing_process().

    Algoritmo:
      1. Classifica il materiale → material_class (es. "thermoplastic")
      2. Determina la geometria → geometry_class (es. "profile")
      3. Lookup in _PROCESS_RESOLUTION_TABLE → processo (es. "Extrusion")
      4. Fallback: "Injection moulding" (comportamento legacy invariato)

    Args:
        material:       Nome del materiale (es. "PVC", "polypropylene").
        geometry_hint:  Classe geometrica dal SemanticNormalizer (es. "profile").
        geometry_label: Label geometrica LLM (es. "Profili/Tubi").
        component_name: Nome del componente per hinting lessicale.

    Returns:
        Stringa del processo manifatturiero.
    """
    mat_class = _classify_material(material)
    geom_class = _classify_geometry(geometry_hint, geometry_label, component_name)

    if mat_class:
        process = _PROCESS_RESOLUTION_TABLE.get((mat_class, geom_class))
        if process:
            logger.debug(
                "ProcessResolver: '%s' -> material_class='%s', geometry_class='%s' -> '%s'",
                material, mat_class, geom_class, process,
            )
            return process
        logger.warning(
            "ProcessResolver: nessuna regola per ('%s', '%s'). Fallback -> 'Injection moulding'.",
            mat_class, geom_class,
        )
    else:
        logger.warning(
            "ProcessResolver: materiale '%s' non classificato. Fallback -> 'Injection moulding'.",
            material,
        )

    return "Injection moulding"


# Deprecato — mantenuto per compatibilità interna residua.
# Il codice che usava determine_manufacturing_process() ora chiama resolve_process().
def determine_manufacturing_process(material: str, geometry: str) -> str:
    """DEPRECATED: usa resolve_process() al suo posto."""
    return resolve_process(material=material, geometry_label=geometry)


async def workflow_bom_ideator(state: AgentState) -> dict:
    from agents.nodes import is_italian
    ita = is_italian(state.get("user_input", ""))
    thought_log = list(state.get("thought_log", []))
    assumptions = list(state.get("assumptions_list", []))
    thought_log.append(
        f"Ho ricevuto la descrizione: \"{state.get('user_input', '')[:60]}...\". "
        f"Avvio l'analisi in 7 fasi per costruire il modello LCA."
    )

    # T05: Step 2 — Lookup Aggregato
    llm = ModelFactory.get_model()
    constraints = dict(state.get("constraints", {}))
    
    def map_geo(g):
        if not isinstance(g, str): return g
        return {"it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"}.get(g.lower(), g)

    if constraints.get("geography"):
        constraints["geography"] = map_geo(constraints["geography"])

    if constraints.get("mass") is not None or constraints.get("geography") is not None:
        if ita:
            thought_log.append("Utilizzo vincoli forniti dall'utente.")
        else:
            thought_log.append("Using user-provided constraints.")

    system_prompt = ModelFactory.get_system_prompt("semantic_ideation_api").format(
        user_input=state.get("user_input", ""),
        constraints=json.dumps(constraints),
        geography=constraints.get("geography", "Unknown Geography"),
    )

    user_prompt = f"""
Product Description: {state.get("user_input", "")}
Constraints: {json.dumps(constraints)}

CRITICAL: The Product Description may contain a "[User Interview Response]" section. You MUST extract any missing data (like mass, geography, or distance) from this response and incorporate it into your output!

Execute the 7 Steps defined in your System Prompt and provide a COMPLETE BOM Generation output.

ASSUMPTION-FIRST RULES (mandatory):
- ALWAYS set is_interview_complete=True and interview_questions=[] UNLESS the description
  is completely unintelligible (e.g. a single word with no context at all).
- CRITICAL: If 'mass' or 'geography' are already provided in Constraints, DO NOT infer them and DO NOT create an assumption for them. Use the provided constraints explicitly!
- CRITICAL: If mass is missing in Constraints → DO NOT INFER IT. You MUST leave total_mass_kg as null so the system can ask the user.
- CRITICAL: LITERAL EXTRACTION ONLY for geography and environment. Extract ONLY if explicitly mentioned.
- CRITICAL: NO LANGUAGE BIAS. Do NOT infer geography or supplier_country from the language spoken by the user. If the country is not explicitly named, it MUST be left as null.
- CRITICAL: NO DOMAIN INFERENCE. Do NOT guess usage_environment from the material.
- If material is missing → choose the most plausible one by technical exclusion (Step 3) and record the assumption.
- If geography is missing in Constraints → DO NOT INFER IT. You MUST leave geography as null so the Gap Analysis can handle it!
- HARD LOCK GEOGRAPHY: If the user specifies a geography/nation (e.g., 'in Perù') or if it's in Constraints, it is a PRIMARY CONSTRAINT. You MUST extract it, translate it to English, and NEVER declare it 'not specified'.
- MATERIAL SPECIFICITY: Output the basic industrial material name (e.g., 'steel', 'aluminum', 'polypropylene'). DO NOT add adjectives like 'virgin', 'natural', or 'primary'. Our database logic will automatically filter out waste/scrap datasets. NEVER use 'waste' or 'recycled' unless explicitly requested by the user.
- You MUST translate BOTH the extracted material name and the geography into English.
- NEVER leave fields at zero or undefined when an assumption can fill them.

ALWAYS ensure:
- You determine if it's a material or object (Step 1).
- You extract logistics distance_km ONLY if explicitly stated by the user (Step 6).
- Every assumption is listed in assumptions_made with a clear explanation. ONLY list actual assumptions made. Do NOT list 'no assumption needed' or 'provided by user' notes.
- You use the EXACT geometry labels from Step 4 (Corpi Cavi, Pezzi Pieni Complessi,
  Film, Profili/Tubi).
- JSON keys remain in English; user-facing text (assumptions_made, justification) must
  be in the same language as the Product Description.
"""

    chain = llm.with_structured_output(WorkflowAndBOMResponse)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        result: WorkflowAndBOMResponse = await asyncio.to_thread(
            _invoke_structured, chain, llm, WorkflowAndBOMResponse, messages
        )

        provider = get_lca_provider()
        
        raw_geography = result.geography or "Not specified"
        raw_geography = normalize_text(raw_geography)
        
        geo_dict = {
            "it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", 
            "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", 
            "glo": "Global", "row": "Rest of World"
        }
        
        if raw_geography.lower() in geo_dict:
            geography = geo_dict[raw_geography.lower()]
        else:
            geography = raw_geography.title() if raw_geography != "Not specified" else "Not specified"

        is_material_only = result.is_material_only
        is_interview_complete = result.is_interview_complete

        # --- PASSO 7: GAP ANALYSIS COGNITIVA ---

        thought_log = state.get("thought_log", [])
        assumptions = state.get("assumptions_list", [])
        attempt_count = state.get("interview_attempt_count", 0)

        # --- INIEZIONE ATTIVA DEI CONSTRAINTS ---
        # Forza i valori dai constraints se presenti per evitare che l'LLM li ignori o perda nel parsing
        if constraints.get("mass") is not None:
            result.total_mass_kg = constraints["mass"]
        if constraints.get("geography") and constraints.get("geography", "").lower() not in ["not specified", "unknown geography", ""]:
            result.geography = constraints["geography"]
        if constraints.get("distance_km") is not None:
            result.distance_km = constraints["distance_km"]

        # Dati estratti dall'LLM strutturato (ora sovrascritti coi constraints certi)
        is_material_only = result.is_material_only
        mass = result.total_mass_kg
        geography = result.geography
        dist_km = result.distance_km

        # Mappiamo analiticamente cosa manca
        missing_fields = []
        if result.total_mass_kg is None or result.total_mass_kg == 0:
            missing_fields.append("massa")
        if not result.geography or result.geography.lower() in ["not specified", "unknown geography", ""]:
            missing_fields.append("luogo (geografia)")
        if result.distance_km is None:
            missing_fields.append("distanza di trasporto")

        # Se ci sono dati mancanti, attiviamo la logica condizionale sui tentativi
        if missing_fields:
            if attempt_count == 0:
                # =========================================================
                # TENTATIVO 0: Interrompiamo il grafo e chiediamo all'utente
                # =========================================================
                if len(missing_fields) == 1 and "massa" in missing_fields:
                    # Messaggio specifico richiesto per la massa
                    msg = "Manca la massa. Puoi specificare quanti kg di prodotto vuoi analizzare?"
                else:
                    # Messaggio cumulativo se la massa c'è ma manca altro o se mancano più campi
                    campi_str = ", ".join(missing_fields)
                    msg = f"Mancano alcune informazioni importanti: {campi_str}. Puoi fornirle?"
                
                thought_log.append(f"Gap Analysis (Tentativo 1): Campi mancanti {missing_fields}. Richiesta feedback via UI.")
                
                return {
                    "pending_feedback": msg,
                    "thought_log": thought_log,
                    "assumptions_list": assumptions,
                    "current_phase": "interview",
                    "current_lca_step": 7,
                    "interview_attempt_count": 1, # Al prossimo giro entrerà nel ramo autonomo
                }
                
            elif attempt_count == 1:
                # =========================================================
                # TENTATIVO 1: L'utente ha saltato la domanda -> Fallback deterministici
                # =========================================================
                thought_log.append("Gap Analysis (Tentativo 2): Dati ancora assenti. Applicazione gerarchia di default.")
                
                if "massa" in missing_fields:
                    mass = 5.0
                    result.total_mass_kg = 5.0  # Aggiorniamo la distinta base effettiva
                    assumptions.append("Massa non specificata: assunto valore di default industriale di 5.0 kg.")
                    
                if "luogo (geografia)" in missing_fields:
                    geography = "Europe (RER)"
                    result.geography = "Europe (RER)"
                    assumptions.append("Geografia non specificata: assunto mercato europeo di default (RER).")
                    
                if "distanza di trasporto" in missing_fields:
                    dist_km = None
                    result.distance_km = None  # has_transport diventa False → forzerà l'uso di 'market for'
                    assumptions.append("Distanza non specificata: il sistema utilizzerà i dataset 'market for' (trasporto già integrato, 0 tkm aggiuntivi).")

        # Se siamo qui (o perché i dati c'erano, o perché l'utente ha risposto al tentativo 0, 
        # o perché abbiamo applicato i default al tentativo 1), il workflow può procedere.
        current_phase = "workflow"

        # T05: Step 3 — Selezione Materiale (inferenza LLM completata)
        if ita:
            thought_log.append("Passo 3: Selezione del materiale completata.")
        else:
            thought_log.append("Step 3: Material selection completed.")

        bom = []

        # --- PRE-CALCOLO PER RI-PROPORZIONARE I PESI ---
        components_list = result.components or []
        num_comps = len(components_list)
        total_llm_weight = sum((getattr(c, "weight_kg", 0.0) or 0.0 for c in components_list), 0.0)
        
        scale_factor = 1.0
        fallback_weight_per_comp = None
        
        if mass is not None and mass > 0 and num_comps > 0:
            if total_llm_weight > 0:
                if abs(total_llm_weight - mass) > 0.01:
                    scale_factor = mass / total_llm_weight
                    thought_log.append(f"Ricalcolo proporzionale pesi componenti per farli coincidere con la massa totale ({mass:.2f} kg).")
            else:
                # LLM ha generato componenti a peso zero (o mancante). Ripartizione equa.
                fallback_weight_per_comp = mass / num_comps
                thought_log.append("[Gap Analysis] Componenti generati con peso nullo. Ripartizione equa della massa totale.")

        # T05: Step 4 — Vincolo Geometrico & Fuzzy Match materiali
        if not is_material_only:
            if ita:
                thought_log.append("Passo 4: Mappatura geometria → processo manifatturiero.")
            else:
                thought_log.append("Step 4: Mapping geometry → manufacturing process.")
        else:
            if ita:
                thought_log.append("Passo 4: Saltato — input classificato come materiale grezzo (is_material_only=True). Nessun processo manifatturiero aggiunto.")
            else:
                thought_log.append("Step 4: Skipped — input classified as raw material (is_material_only=True). No manufacturing process appended.")

        for comp_data in result.components or []:
            comp = comp_data.model_dump()
            
            # Applica proporzione al peso se necessaria
            if fallback_weight_per_comp is not None:
                comp["weight_kg"] = round(fallback_weight_per_comp, 4)
            elif scale_factor != 1.0 and comp.get("weight_kg") is not None:
                comp["weight_kg"] = round(comp["weight_kg"] * scale_factor, 4)

            mat = comp.get("material", "unknown")
            import re
            mat = re.sub(r'\s*\([^)]*\)', '', mat)
            mat = normalize_text(mat)
            comp["material"] = mat

            # 1. Fuzzy Match del materiale nel DataSet.xlsx
            comp_dist = comp.get("distance_km")
            eff_dist = comp_dist if comp_dist is not None else (dist_km or 0.0)
            has_transport = dist_km is not None and dist_km > 0
            best_match = await provider.find_closest_match(
                mat,
                location=geography,
                task_type=state.get("constraints", {}).get("task_type", "optimization"),
                has_transport=has_transport,
                thought_log=thought_log,
            )

            if not best_match or best_match.get("environmental_impact") is None:
                # ── STRICT MODE — MATERIAL NOT FOUND ─────────────────────────
                # Il materiale non è presente nel DB con sufficiente confidenza
                # (threshold > 0.85) nella catena geografica [location → RER → GLO → RoW].
                # Blocca il workflow e avvisa l'utente: NON usare dati non correlati.
                display_geo = {"it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"}.get(geography.lower(), geography.title())
                suggested_alt = {"marble": "natural stone o concrete", "carbon fiber": "glass fiber o generic composite", "bamboo": "wood o generic biomass", "hemp": "natural fiber o flax", "kevlar": "aramid fiber", "titanium": "stainless steel o aluminum alloy"}.get(mat.lower(), "una categoria superiore (es. 'natural stone' o 'concrete')")

                error_msg = (
                    f"⚠️ **Materiale non trovato nel database LCA** (soglia similarità: 0.85).\n\n"
                    f"Il materiale **'{mat}'** non è presente nel dataset ecoinvent "
                    f"per la geografia **'{display_geo}'** né nei proxy regionali (RER, GLO, RoW).\n\n"
                    f"Questo blocco è necessario per garantire che i calcoli di sostenibilità siano basati su dati certificati e non su stime incerte.\n\n"
                    f"**Suggerimenti per la risoluzione:**\n"
                    f"- Prova a cercare con {suggested_alt}.\n"
                    f"- Fornisci un nome del materiale più generico in inglese (es. 'polypropylene', 'steel').\n"
                    f"- Cambia l'area geografica (es. 'Global', 'Europe').\n"
                    f"- Nota: i prodotti agricoli o grezzi molto specifici potrebbero non essere coperti dal dataset industriale."
                )
                assumptions.append(
                    f"ERRORE RETRIEVAL: Materiale '{mat}' non trovato nel DB LCA (soglia 0.85) "
                    f"per '{geography}'. Workflow interrotto per garantire l'integrità dei dati."
                )
                logger.warning("STRICT MODE: materiale '%s' non trovato per '%s'. Interrompo.", mat, geography)
                thought_log.append(f"🚫 STRICT RETRIEVAL FAIL: '{mat}' @ '{geography}' → nessun match con confidenza ≥ 0.85.")
                return {
                    "pending_feedback": error_msg,
                    "thought_log": thought_log,
                    "assumptions_list": assumptions,
                    "current_lca_step": 2,
                    "current_phase": "error",
                    "error_message": error_msg,
                }
            else:
                loc_found = best_match.get("location", "")
                if (
                    geography.lower() not in ["not specified", ""]
                    and loc_found.lower() != geography.lower()
                ):
                    # Geographic fallback usato dal provider — solo warning, non crash
                    display_loc_found = map_geo(loc_found)
                    display_geography = map_geo(geography)
                    geo_note = (
                        f"Nota: per '{mat}' richiesta geografia '{display_geography}', "
                        f"usato proxy geografico '{display_loc_found}' dal database."
                    )
                    assumptions.append(geo_note)
                    logger.info(geo_note)

                idx = best_match.get("index", "?")
                provider_name = best_match.get("providerName", "?")
                val_co2 = best_match.get("environmental_impact", "?")
                thought_log.append(f"Riga Excel trovata: {idx} - {provider_name} - {loc_found} - {val_co2}")

                comp["material_source"] = best_match["flowName"]
                comp["unit_impact_value"] = best_match["environmental_impact"]

            # DIRETTIVA 3: Process Mapper — Name-First con Fallback Geometrico
            # Priorità assoluta al nome del componente (deterministico);
            # fallback alla geometria LLM solo se il componente non è in COMPONENT_PROCESS_MAPPER.
            if not is_material_only:
                component_name_for_lookup = comp.get("name", "")
                process_by_name = get_process_by_component_name(component_name_for_lookup)

                if process_by_name:
                    comp["manufacturing_process"] = process_by_name
                    logger.debug(
                        "ProcessMapper [NAME-FIRST]: '%s' → '%s' (deterministico per nome)",
                        component_name_for_lookup, process_by_name,
                    )
                    thought_log.append(
                        f"ProcessMapper: '{component_name_for_lookup}' → '{process_by_name}' "
                        f"[deterministico per nome]"
                    )
                else:
                    # PROCESS RESOLVER — attivo solo se il componente non è in COMPONENT_PROCESS_MAPPER.
                    # Ragiona su (material_class, geometry_class) invece di keyword matching.
                    geom_label = comp.get("geometry") or "Pezzi Pieni Complessi"
                    resolved = resolve_process(
                        material=mat,
                        geometry_label=geom_label,
                        component_name=component_name_for_lookup,
                    )
                    comp["manufacturing_process"] = resolved
                    logger.warning(
                        "ProcessResolver [FALLBACK]: '%s' non in COMPONENT_PROCESS_MAPPER. "
                        "ProcessResolver: mat='%s', geom='%s' → process='%s'.",
                        component_name_for_lookup, mat, geom_label, resolved,
                    )
                    thought_log.append(
                        f"ProcessResolver: '{component_name_for_lookup}' (mat='{mat}', geom='{geom_label}') "
                        f"→ '{resolved}' [resolver — aggiungere a COMPONENT_PROCESS_MAPPER se ricorrente]"
                    )
            else:
                comp["geometry"] = None
                comp["manufacturing_process"] = None

            # Baseline per compatibilità schema
            comp["baseline_environmental_impact"] = comp["unit_impact_value"]
            comp["baseline_cost"] = 1.0
            comp["lifespan_years"] = 10.0

            bom.append(comp)

        # T05: Step 5 — Scomposizione BOM completata
        _m = result.total_mass_kg or 0.0
        comp_names = ", ".join(c.get("name", "?") for c in bom[:3])
        thought_log.append(
            f"La BOM è composta da {len(bom)} componente/i: {comp_names}"
            + (" e altri..." if len(bom) > 3 else ".")
            + f" Massa totale: {_m:.2f} kg."
        )

        workflow = [w.model_dump() for w in (result.workflow_steps or [])]

        # T05: Step 6 — Calcolo Logistica
        mass = result.total_mass_kg or 0.0
        
        dist_km: Optional[float] = result.distance_km
        supplier_country: Optional[str] = result.supplier_country
        destination_country: Optional[str] = result.destination_country
        
        dist_km = dist_km or 0.0
        log_type = "stimati o assunti" if result.distance_km is None else "dichiarati dall'utente"
        thought_log.append(
            f"Calcolo logistico: {mass:.2f} kg × {dist_km:.0f} km "
            f"= {(mass/1000.0*dist_km):.4f} tkm "
            f"({log_type})."
        )
        tkm = (mass / 1000.0) * dist_km
        transport_mode_val = getattr(result, "transport_mode", "lorry") or "lorry"
        logistics = {
            "geography": geography,                                      # Nazione di produzione
            "supplier_country": supplier_country or geography,           # Fallback: usa geography
            "destination_country": destination_country or geography,
            "distance_km": dist_km,
            "tkm": tkm,
            "transport_mode": transport_mode_val
        }

        process_name = f"{transport_mode_val.capitalize()} transport"

        workflow.append({
            "process_name": process_name,
            "process_output": f"{tkm:.1f} tkm"
        })

        # Aggiungi assunzioni LLM alle nostre
        if result.assumptions_made:
            assumptions.extend(result.assumptions_made)

        # Nota: ecoinvent usa "Europe without Switzerland" come codice regionale europeo.
        # Nessuna sostituzione automatica di nomi di paesi nelle assunzioni.

        unique_thoughts = list(dict.fromkeys(thought_log))
        unique_assumptions = list(dict.fromkeys(assumptions))

        return {
            "bom": bom,
            "workflow_steps": workflow,
            "thought_log": unique_thoughts,
            "current_lca_step": 4,          # Step 4 (Workflow completed, ready for Material Ideation)
            "current_phase": "workflow",    # T07: routing esplicito
            "detected_geometry": result.components[0].geometry if result.components else "Unknown",
            "logistics_data": logistics,
            "assumptions_list": unique_assumptions,
        }

    except Exception as exc:
        logger.error(f"Workflow Ideation fallito: {exc}")
        if ita:
            thought_log.append(f"⚠ Errore durante l'analisi ({exc}).")
        else:
            thought_log.append(f"⚠ Error during analysis ({exc}).")

        return {
            "pending_feedback": "An error occurred during analysis. Please try again.",
            "thought_log": thought_log,
            "assumptions_list": assumptions,
            "current_phase": "error",       # T07: routing esplicito su errore
            "error_message": str(exc),
        }
