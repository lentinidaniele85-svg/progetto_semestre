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

def determine_manufacturing_process(material: str, geometry: str) -> str:
    mat_lower = material.lower()
    if any(m in mat_lower for m in ["steel", "aluminum", "aluminium", "iron", "copper", "brass", "metal", "titanium", "acciaio"]):
        if geometry == "Profili/Tubi":
            return "Section bar rolling"
        elif geometry == "Film":
            return "Metal sheet rolling"
        else:
            return "Metal working"
    elif any(m in mat_lower for m in ["wood", "timber", "plywood", "mdf", "bamboo", "legno"]):
        return "Woodworking"
    elif any(m in mat_lower for m in ["cotton", "polyester", "fabric", "textile", "nylon", "hemp"]):
        return "Textile weaving"
    elif any(m in mat_lower for m in ["glass", "ceramic", "vetro", "ceramica"]):
        return "Glass production" if "glass" in mat_lower or "vetro" in mat_lower else "Ceramic firing"
    else:
        return GEOMETRY_MAPPING.get(geometry, "Injection moulding")

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

Execute the 7 Steps defined in your System Prompt and provide a COMPLETE BOM Generation output.

ASSUMPTION-FIRST RULES (mandatory):
- ALWAYS set is_interview_complete=True and interview_questions=[] UNLESS the description
  is completely unintelligible (e.g. a single word with no context at all).
- CRITICAL: If 'mass' or 'geography' are already provided in Constraints, DO NOT infer them and DO NOT create an assumption for them. Use the provided constraints explicitly!
- If mass is missing in Constraints → infer it from the product category (chair=4.5kg, 500mL bottle=0.025kg,
  generic part=1.0kg) and record the assumption.
- If material is missing → choose the most plausible one by technical exclusion (Step 3)
  and record the assumption.
- If geography is missing in Constraints → default to RER (Europe) or GLO and record the assumption.
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

        if not is_interview_complete and result.components:
            all_matched = True
            for comp_data in result.components:
                comp_dict = comp_data.model_dump() if hasattr(comp_data, "model_dump") else (comp_data.dict() if hasattr(comp_data, "dict") else comp_data)
                mat = comp_dict.get("material", "unknown") if isinstance(comp_dict, dict) else "unknown"
                match = provider.find_closest_match(mat, location=geography, threshold=0.8, task_type=state.get("constraints", {}).get("task_type", "optimization"))
                if not match:
                    all_matched = False
                    break
            if all_matched:
                if ita:
                    thought_log.append("Trovate corrispondenze con >80% di similarità. Ignoro richiesta intervista.")
                else:
                    thought_log.append("Found matches with >80% similarity. Overriding interview request.")
                is_interview_complete = True

        # Leggi il contatore dai tentativi precedenti
        attempt_count = state.get("interview_attempt_count", 0)
        
        if not is_interview_complete:
            attempt_count += 1
            
            if attempt_count >= 3:
                # Terzo tentativo fallito: applica 500 km e procedi con avviso
                dist_km = 500.0
                mass = result.total_mass_kg or 1.0
                thought_log.append(
                    f"⚠ Intervista fallita dopo {attempt_count} tentativi. "
                    f"Applicati valori di default: massa={mass}kg, distanza=500km."
                )
                assumptions.append(
                    f"AVVISO: Dati logistici non forniti dopo {attempt_count} richieste. "
                    f"Usati valori di default (massa={mass}kg, distanza=500km). "
                    f"I risultati LCA potrebbero non essere accurati."
                )
                # NON fare return — procedi con i default
                is_interview_complete = True
                result.total_mass_kg = mass
                result.distance_km = dist_km
            else:
                # Continua a chiedere
                if ita:
                    thought_log.append("Interruzione: dati mancanti. In attesa di risposta utente.")
                else:
                    thought_log.append("Interrupt: missing data. Waiting for user response.")
                missing_text = "Mi mancano alcune informazioni per poter procedere:\n"
                for q in result.interview_questions:
                    missing_text += f"- {q}\n"
                return {
                    "pending_feedback": missing_text,
                    "thought_log": thought_log,
                    "assumptions_list": assumptions,
                    "current_lca_step": 2,
                    "current_phase": "interview",
                    "interview_attempt_count": attempt_count,
                }

        # T05: Step 3 — Selezione Materiale (inferenza LLM completata)
        if ita:
            thought_log.append("Passo 3: Selezione del materiale completata.")
        else:
            thought_log.append("Step 3: Material selection completed.")

        bom = []

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
            mat = comp.get("material", "unknown")
            mat = normalize_text(mat)
            comp["material"] = mat

            # 1. Fuzzy Match del materiale nel DataSet.xlsx
            best_match = provider.find_closest_match(mat, location=geography, task_type=state.get("constraints", {}).get("task_type", "optimization"))

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

            # 2. Mapping Geometria → Processo (solo per prodotti finiti)
            if not is_material_only:
                geom = comp.get("geometry") or "Pezzi Pieni Complessi"
                comp["manufacturing_process"] = determine_manufacturing_process(mat, geom)
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
        # ── Massa ────────────────────────────────────────────────────────────────────
        # Il valore di massa viene dal LLM (result.total_mass_kg).
        # Se è None significa che il prompt Step 7 non ha ricevuto abbastanza info
        # per ragionare — guard rail Python di ultima istanza.
        mass = result.total_mass_kg
        if mass is None:
            missing_text = "Non è stato possibile determinare la massa del prodotto.\n"
            missing_text += "- Qual è la massa totale del prodotto (in kg)?\n"
            return {
                "pending_feedback": missing_text,
                "thought_log": thought_log,
                "assumptions_list": assumptions,
                "current_lca_step": 2,
                "current_phase": "interview",
            }
        # ── Distanza/Logistica ────────────────────────────────────────────────────────
        # L'LLM (tramite il prompt Step 6 aggiornato) è responsabile di:
        #   A. Estrarre distance_km se dichiarata dall'utente.
        #   B. Stimare distance_km ragionando su supplier_country + destination_country.
        #   C. Impostare is_interview_complete=False e chiedere i dati se non li ha.
        # Il codice Python qui serve solo come guard rail per il caso in cui
        # il modello non abbia seguito le istruzioni.
        dist_km: Optional[float] = result.distance_km
        supplier_country: Optional[str] = result.supplier_country
        destination_country: Optional[str] = result.destination_country
        if dist_km is None and not result.is_material_only:
            # Il LLM avrebbe dovuto o stimare o bloccarsi — se arriviamo qui
            # significa che il modello non ha rispettato Step 6. Blocco Python.
            missing_text = "Dati logistici mancanti per il calcolo del trasporto.\n"
            if not supplier_country:
                missing_text += "- Da quale paese proviene il materiale/fornitore principale?\n"
            if not destination_country:
                missing_text += "- Qual è la nazione di destinazione/assemblaggio?\n"
            missing_text += (
                "\nIn alternativa, puoi specificare direttamente la distanza in km "
                "dal fornitore al sito di produzione."
            )
            return {
                "pending_feedback": missing_text,
                "thought_log": thought_log,
                "assumptions_list": assumptions,
                "current_lca_step": 2,
                "current_phase": "interview",
            }

        dist_km = dist_km or 0.0
        thought_log.append(
            f"Calcolo logistico: {mass:.2f} kg × {dist_km:.0f} km "
            f"= {(mass/1000.0*dist_km):.4f} tkm "
            f"({'stimati' if result.distance_km is None else 'dichiarati dall\\'utente'})."
        )
        tkm = (mass / 1000.0) * dist_km
        logistics = {
            "geography": geography,                                      # Nazione di produzione
            "supplier_country": supplier_country or geography,           # Fallback: usa geography
            "destination_country": destination_country or geography,
            "distance_km": dist_km,
            "tkm": tkm,
        }

        workflow.append({
            "process_name": "Lorry transport",
            "process_output": f"{tkm:.1f} tkm"
        })

        # Aggiungi assunzioni LLM alle nostre
        if result.assumptions_made:
            assumptions.extend(result.assumptions_made)

        # Nota: ecoinvent usa "Europe without Switzerland" come codice regionale europeo.
        # Nessuna sostituzione automatica di nomi di paesi nelle assunzioni.

        return {
            "bom": bom,
            "workflow_steps": workflow,
            "thought_log": thought_log,
            "current_lca_step": 4,          # Step 4 (Workflow completed, ready for Material Ideation)
            "current_phase": "workflow",    # T07: routing esplicito
            "detected_geometry": result.components[0].geometry if result.components else "Unknown",
            "logistics_data": logistics,
            "assumptions_list": assumptions,
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
