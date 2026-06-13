import json
from typing import Optional
import logging
# pyrefly: ignore [missing-import]
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from agents.state import AgentState
from core.llm_factory import ModelFactory
from data.provider_factory import get_lca_provider
from agents.schemas import WorkflowAndBOMResponse, BOMComponent
from agents.nodes import _invoke_structured
import asyncio
import unicodedata


# ---------------------------------------------------------------------------
# TICKET 1: MassEstimation — Schema e Funzione LLM Estimator
# ---------------------------------------------------------------------------

class MassEstimation(BaseModel):
    """Output strutturato dell'LLM Mass Estimator."""
    mass_kg: float = Field(
        gt=0.0,
        description="Stima della massa funzionale in kg per l'oggetto o lotto descritto dall'utente."
    )
    reasoning: str = Field(
        description="Breve spiegazione del ragionamento usato per dedurre la massa (es. standard industriale, confronto con prodotti analoghi)."
    )


_MASS_ESTIMATOR_SYSTEM_PROMPT = (
    "Sei un ingegnere industriale senior specializzato in stime dimensionali e LCA. "
    "Il tuo compito è stimare la massa funzionale (in kg) di un oggetto o lotto di materiale "
    "descritto dall'utente, basandoti sulla tua conoscenza di norme industriali, cataloghi di prodotto "
    "e standard ingegneristici internazionali.\n"
    "Regole:\n"
    "1) Se l'input è un oggetto specifico (es. 'una sedia', 'un tubo da cantiere'): stima il peso tipico "
    "di un'unità standard di quel prodotto (es. sedia residenziale = 4.5 kg, tubo PVC DN110 x 1m = 1.2 kg).\n"
    "2) Se l'input è un materiale grezzo (es. 'polietilene', 'acciaio'): stima un lotto industriale standard "
    "(es. pellet PP = 25 kg = sacco standard, foglio di acciaio = 50 kg).\n"
    "3) Se menziona un quantitativo vago (es. 'un lotto', 'una produzione'): usa il lotto minimo standard "
    "del settore pertinente.\n"
    "4) Restituisci SEMPRE mass_kg > 0 e spiega brevemente il ragionamento in 'reasoning'.\n"
    "5) ATTENZIONE: Il campo `mass_kg` dello schema MassEstimation deve essere espresso rigorosamente in CHILOGRAMMI (kg). Se l'oggetto analizzato pesa meno di un chilo (es. grammi), devi effettuare la conversione in frazioni decimali. Esempio: se una racchetta da padel pesa 360 grammi, devi restituire 0.36, NON 360.0. Non confondere mai i grammi con i chilogrammi.\n"
    "NON chiedere all'utente chiarimenti. Scegli la stima più plausibile e procedi."
)


async def _estimate_mass_with_llm(user_input: str, llm=None) -> tuple[float, str]:
    """Invoca l'LLM per stimare la massa funzionale dall'input utente.

    Returns:
        (mass_kg: float, reasoning: str) — sempre valori positivi.
        In caso di errore LLM: (1.0, messaggio di errore).
    """
    import logging
    _log = logging.getLogger(__name__)
    try:
        if llm is None:
            llm = ModelFactory.get_model()
        chain = llm.with_structured_output(MassEstimation)
        result: MassEstimation = await asyncio.to_thread(
            chain.invoke,
            [
                SystemMessage(content=_MASS_ESTIMATOR_SYSTEM_PROMPT),
                HumanMessage(content=user_input),
            ]
        )
        return result.mass_kg, result.reasoning
    except Exception as exc:
        _log.warning("[LLM Mass Estimator] Fallback a 1.0 kg per errore LLM: %s", exc)
        return 1.0, f"Stima LLM non disponibile ({exc}). Usato valore minimo di sicurezza: 1.0 kg."

def normalize_text(text: str) -> str:
    """Rimuove accenti e normalizza la stringa (es. 'Perù' -> 'Peru')."""
    if not text:
        return ""
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn').strip()

logger = logging.getLogger(__name__)

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
- MATERIAL SPECIFICITY: Output the basic industrial material name (e.g., 'steel', 'aluminum', 'polypropylene'). DO NOT add adjectives like 'virgin', 'natural', or 'primary'. Our database logic will automatically filter out waste/scrap datasets. NEVER use 'waste' or 'recycled' in the material name field.
- RECYCLED RULE (MANDATORY): If the user requests a recycled/secondary material (e.g. 'legno riciclato', 'recycled PP', 'rPET', 'scrap steel'), keep the material name CLEAN (e.g. 'wood', 'polypropylene') but MUST set is_recycled=true on the BOMComponent. is_recycled is the ONLY source of truth for the database scoring engine. NEVER add 'recycled' to the material name — this destroys DB matching.
- SINGLE-MATERIAL LOCK (MANDATORY): If the user requests a quantity of a raw, bulk, or pure material (e.g. '200 kg of recycled steel', '5 tonnes of polypropylene'), the BOM MUST contain EXACTLY ONE component with 100% of the requested mass. DO NOT split into multiple sub-materials.
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
                # TENTATIVO 1: L'utente ha saltato la domanda -> LLM Estimator
                # =========================================================
                thought_log.append("Gap Analysis (Tentativo 2): Dati ancora assenti. Attivazione LLM Estimator per deduzione autonoma.")
                
                if "massa" in missing_fields:
                    # TICKET 1: Invoca l'LLM per stimare la massa funzionale dal contesto
                    # (nessun numero hardcoded — il sistema ragiona, non assume)
                    estimated_mass, mass_reasoning = await _estimate_mass_with_llm(
                        state.get("user_input", ""), llm=llm
                    )
                    mass = estimated_mass
                    result.total_mass_kg = estimated_mass
                    constraints["mass"] = estimated_mass
                    _mass_log = (
                        f"[LLM Mass Estimator] Massa non fornita dall'utente. "
                        f"Stima LLM: {estimated_mass:.3f} kg. "
                        f"Ragionamento: {mass_reasoning}"
                    )
                    thought_log.append(_mass_log)
                    assumptions.append(
                        f"Massa non specificata. L'LLM ha stimato {estimated_mass:.3f} kg "
                        f"basandosi sul contesto: {mass_reasoning}"
                    )
                    
                if "luogo (geografia)" in missing_fields:
                    geography = "Europe (RER)"
                    result.geography = "Europe (RER)"
                    constraints["geography"] = "Europe (RER)"
                    assumptions.append("Geografia non specificata: assunto mercato europeo di default (RER).")
                    
                if "distanza di trasporto" in missing_fields:
                    dist_km = None
                    result.distance_km = None  # has_transport diventa False → forzerà l'uso di 'market for'
                    assumptions.append("Distanza non specificata: il sistema utilizzerà i dataset 'market for' (trasporto già integrato, 0 tkm aggiuntivi).")

        # Se siamo qui (o perché i dati c'erano, o perché l'utente ha risposto al tentativo 0,
        # o perché abbiamo applicato i default al tentativo 1), il workflow può procedere.

        # T05: Step 3 — Selezione Materiale (inferenza LLM completata)
        if ita:
            thought_log.append("Passo 3: Selezione del materiale completata.")
        else:
            thought_log.append("Step 3: Material selection completed.")

        # --- SALVAGUARDIA MATERIALE SINGOLO ---
        if result.is_material_only:
            if result.components and len(result.components) > 1:
                # Mantieni solo il componente con massa maggiore
                heaviest = max(result.components, key=lambda c: getattr(c, 'weight_kg', 0.0) or 0.0)
                result.components = [heaviest]
                thought_log.append("[GUARDRAIL] is_material_only=True: BOM ridotta a 1 componente (anti-splitting)")
            
            # Enforce is_material_only on the component and clear geometry/process
            if result.components:
                result.components[0].is_material_only = True
                result.components[0].geometry = None
                result.components[0].manufacturing_process = None
                if mass is not None and mass > 0:
                    result.components[0].weight_kg = mass

        if not result.components:
            extracted_material = constraints.get("material", "polypropylene")
            result.components = [
                BOMComponent(
                    name=extracted_material.capitalize(),
                    material=extracted_material,
                    weight_kg=state.get("total_mass_kg", result.total_mass_kg or 1.0),
                    is_material_only=result.is_material_only,
                    geometry=None if result.is_material_only else constraints.get("geometry"),
                    manufacturing_process=None if result.is_material_only else constraints.get("manufacturing_process"),
                    material_source="Pending"
                )
            ]

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

            # Propagate is_recycled from Pydantic component — UNICA fonte di verità
            comp["is_recycled"] = comp_data.is_recycled if hasattr(comp_data, "is_recycled") else comp.get("is_recycled", False)

            # 1. Fuzzy Match del materiale nel dataset_ecoinvent_perfetto.xlsx
            has_transport = dist_km is not None and dist_km > 0
            is_recycled_comp = comp.get("is_recycled", False)
            best_match = await provider.find_closest_match(
                mat,
                location=geography,
                task_type=state.get("constraints", {}).get("task_type", "optimization"),
                has_transport=has_transport,
                thought_log=thought_log,
                is_recycled=is_recycled_comp,
            )

            if not best_match or best_match.get("environmental_impact") is None:
                # ── STRICT MODE — MATERIAL NOT FOUND ─────────────────────────
                # Il materiale non è presente nel DB con sufficiente confidenza
                # (threshold > 0.85) nella catena geografica [location → RER → GLO → RoW].
                # Blocca il workflow e avvisa l'utente: NON usare dati non correlati.
                display_geo = {"it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"}.get(geography.lower(), geography.title())
                suggested_alt = {"marble": "natural stone o concrete", "carbon fiber": "glass fiber o generic composite", "bamboo": "wood o generic biomass", "hemp": "natural fiber o flax", "kevlar": "aramid fiber", "titanium": "stainless steel o aluminum alloy"}.get(mat.lower(), "una categoria superiore (es. 'natural stone' o 'concrete')")

                error_msg = (
                    f"⚠️ **Materiale non trovato nel database LCA** (soglia similarità: 0.85 e fallback 0.75 falliti).\n\n"
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

            # DIRETTIVA 3: Process Mapper — Material-First con Fallback sul Nome
            # 1. Priorità assoluta al materiale.
            # 2. Gating per Polimeri/Compositi: se il materiale contiene keyword specifiche, salta il nome e usa il ProcessResolver.
            # 3. Fallback sul Nome: check deterministico (es. 'frame' -> 'Metal working') solo se non è polimero/composito.
            if not is_material_only:
                mat_lower = mat.lower()
                geom_lower = (comp.get("geometry") or "Pezzi Pieni Complessi").lower()
                component_name_for_lookup = comp.get("name", "")

                # 1. CATEGORIA ELETTRO-ELETTRONICA (Semiconduttori e circuiti)
                if any(w in mat_lower for w in ["silicon", "silicio", "semiconductor", "microchip", "wafer", "germanium"]):
                    process_name = "electronic component production, wafer fabrication"

                # 2. CATEGORIA POLIMERI E COMPOSITI (Plastiche e strutture leggere)
                elif any(w in mat_lower for w in ["plastic", "pet", "hdpe", "pvc", "pp", "abs", "poly", "carbon fiber", "carbon fibre", "epoxy", "resin", "fiberglass"]):
                    process_name = "injection moulding"

                # 3. CATEGORIA METALLI (Ferrosi e non ferrosi)
                elif any(w in mat_lower for w in ["metal", "steel", "iron", "aluminium", "aluminum", "copper", "titanium", "brass", "alluminio", "acciaio", "rame"]):
                    process_name = "metal working"

                # 4. CATEGORIA MATERIALI BIO-BASED E LEGNO (Arredamento e bio-packaging)
                elif any(w in mat_lower for w in ["wood", "legno", "timber", "bamboo", "paper", "carta", "cardboard", "cartone"]):
                    process_name = "woodworking"

                # 5. CATEGORIA VETRO E CERAMICA (Packaging in vetro, isolanti, edilizia)
                elif any(w in mat_lower for w in ["glass", "vetro", "silica", "ceramic", "ceramica", "porcelain", "clay"]):
                    process_name = "glass production"

                # 6. CATEGORIA TESSILE (Abbigliamento, rivestimenti, filati)
                elif any(w in mat_lower for w in ["cotton", "cotone", "wool", "lana", "polyester textile", "nylon textile", "fabric", "tessuto", "yarn"]):
                    process_name = "textile production, weaving"

                # 7. FALLBACK GENERALE DETERMINISTICO SE NESSUNA CATEGORIA COMBACIA
                else:
                    if "pezzi pieni" in geom_lower or "complesso" in geom_lower:
                        process_name = "injection moulding"
                    else:
                        process_name = "metal working"

                comp["manufacturing_process"] = process_name
                logger.debug(
                    "ProcessMapper [TAXONOMY]: Materiale '%s' (geom '%s') -> Processo: '%s'",
                    mat, geom_lower, process_name
                )
                thought_log.append(
                    f"ProcessMapper: Componente '{component_name_for_lookup}' con materiale '{mat}' "
                    f"assegnato al processo '{process_name}' (Tassonomia Estesa)"
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
        transport_mode_val = getattr(result, "transport_mode", None)
        # Fallback condizionale: 'lorry' SOLO se c'è una distanza da percorrere ma manca il mezzo.
        # Se mancano entrambi (dist_km == 0), il trasporto è nei dataset 'market for' → nessun fallback.
        if transport_mode_val is None and dist_km and dist_km > 0:
            transport_mode_val = "lorry"
            thought_log.append("[Logistica] Mezzo di trasporto non specificato ma distanza presente: fallback deterministico a 'lorry'.")
        elif transport_mode_val is None:
            transport_mode_val = "lorry"  # default per il campo logistics_data, non influisce sul calcolo tkm=0
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
            "constraints": constraints,
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
