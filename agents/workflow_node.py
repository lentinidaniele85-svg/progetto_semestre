import json
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from agents.state import AgentState
from core.llm_factory import ModelFactory
from data.provider_factory import get_lca_provider
from agents.schemas import WorkflowAndBOMResponse
from agents.nodes import _invoke_structured
import asyncio

logger = logging.getLogger(__name__)

GEOMETRY_MAPPING = {
    "Corpi Cavi": "Blow moulding",
    "Pezzi Pieni Complessi": "Injection moulding",
    "Film": "Extrusion (film)",
    "Profili/Tubi": "Extrusion",
}

async def workflow_bom_ideator(state: AgentState) -> dict:
    from agents.nodes import is_italian
    ita = is_italian(state.get("user_input", ""))
    thought_log = list(state.get("thought_log", []))
    assumptions = list(state.get("assumptions_list", []))
    if ita:
        thought_log.append("Esecuzione Workflow & Ideatore BOM (7 Passi)...")
    else:
        thought_log.append("Executing Workflow & BOM Ideator (7 Steps)...")

    # T05: Step 2 — Lookup Aggregato
    llm = ModelFactory.get_model()
    constraints = state.get("constraints", {})

    system_prompt = ModelFactory.get_system_prompt("semantic_ideation_api").format(
        user_input=state.get("user_input", ""),
        constraints=json.dumps(constraints),
    )

    user_prompt = f"""
Product Description: {state.get("user_input", "")}
Constraints: {json.dumps(constraints)}

Execute the 7 Steps defined in your System Prompt and provide a COMPLETE BOM Generation output.

ASSUMPTION-FIRST RULES (mandatory):
- ALWAYS set is_interview_complete=True and interview_questions=[] UNLESS the description
  is completely unintelligible (e.g. a single word with no context at all).
- If mass is missing → infer it from the product category (chair=4.5kg, 500mL bottle=0.025kg,
  generic part=1.0kg) and record the assumption.
- If material is missing → choose the most plausible one by technical exclusion (Step 3)
  and record the assumption.
- If geography is missing → default to RER (Europe) or GLO and record the assumption.
- NEVER leave fields at zero or undefined when an assumption can fill them.

ALWAYS ensure:
- You determine if it's a material or object (Step 1).
- You extract logistics distance_km ONLY if explicitly stated by the user (Step 6).
- Every assumption is listed in assumptions_made with a clear explanation.
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
        GEO_MAPPING = {
            "europa": "RER",
            "europe": "RER",
            "mondo": "GLO",
            "globale": "GLO",
            "world": "GLO",
            "global": "GLO",
            "stati uniti": "United States of America",
            "usa": "United States of America",
            "united states": "United States of America",
            "cina": "China",
            "china": "China"
        }
        geography = GEO_MAPPING.get(raw_geography.lower(), raw_geography)

        is_interview_complete = result.is_interview_complete

        if not is_interview_complete and result.components:
            all_matched = True
            for comp_data in result.components:
                comp_dict = comp_data.model_dump() if hasattr(comp_data, "model_dump") else (comp_data.dict() if hasattr(comp_data, "dict") else comp_data)
                mat = comp_dict.get("material", "unknown") if isinstance(comp_dict, dict) else "unknown"
                thought_log.append(f"Termine tradotto: {mat}")
                match = provider.find_closest_match(target_product=mat, target_geography=geography, threshold=0.8)
                if match:
                    exact_str = "SI" if match.get("exact_match_found") else "NO"
                    geo_used = match.get("geo_level_used", "N/A")
                    thought_log.append(f"Match esatto trovato: {exact_str}")
                    thought_log.append(f"Livello geografico utilizzato: {geo_used}")
                else:
                    all_matched = False
                    break
            if all_matched:
                if ita:
                    thought_log.append("Trovate corrispondenze con >80% di similarità. Ignoro richiesta intervista.")
                else:
                    thought_log.append("Found matches with >80% similarity. Overriding interview request.")
                is_interview_complete = True

        if not is_interview_complete:
            # T05: step rimane a 2 (non avanzare) durante l'intervista
            if ita:
                thought_log.append("Interruzione: dati mancanti. In attesa di risposta utente.")
            else:
                thought_log.append("Interrupt: missing data. Waiting for user response.")
            missing_text = "Mi mancano alcune informazioni per poter procedere con il modello:\n"
            for q in result.interview_questions:
                missing_text += f"- {q}\n"
            return {
                "pending_feedback": missing_text,
                "thought_log": thought_log,
                "assumptions_list": assumptions,
                "current_lca_step": 2,          # T05: blocco alla fase 2
                "current_phase": "interview",   # T07: routing esplicito
            }

        # T05: Step 3 — Selezione Materiale (inferenza LLM completata)
        if ita:
            thought_log.append("Passo 3: Selezione del materiale completata.")
        else:
            thought_log.append("Step 3: Material selection completed.")

        bom = []

        # T05: Step 4 — Vincolo Geometrico & Fuzzy Match materiali
        if ita:
            thought_log.append("Passo 4: Mappatura geometria → processo manifatturiero.")
        else:
            thought_log.append("Step 4: Mapping geometry → manufacturing process.")

        for comp_data in result.components or []:
            comp = comp_data.model_dump()
            mat = comp.get("material", "unknown")

            # 1. Match del materiale nel DataSet.xlsx
            thought_log.append(f"Termine tradotto: {mat}")
            best_match = provider.find_closest_match(target_product=mat, target_geography=geography)

            if not best_match:
                # ── FALLBACK CAUTELATIVO ──────────────────────────────────────
                # Materiale non trovato nel database: usiamo 3.5 kg CO₂/kg come
                # valore conservativo e lo documentiamo nelle assunzioni.
                CO2_FALLBACK = 3.5
                fallback_warning = (
                    f"ATTENZIONE: Materiale '{mat}' non trovato nel database, "
                    f"usato valore cautelativo di fallback di {CO2_FALLBACK} kg CO\u2082/kg."
                )
                assumptions.append(fallback_warning)
                logger.warning(fallback_warning)
                thought_log.append(f"⚠ Fallback CO₂ applicato per '{mat}': {CO2_FALLBACK} kg CO₂/kg")

                comp["material_source"] = f"{mat} (fallback — non trovato nel database)"
                comp["unit_impact_value"] = CO2_FALLBACK
            else:
                exact_str = "SI" if best_match.get("exact_match_found") else "NO"
                geo_used = best_match.get("geo_level_used", "N/A")
                thought_log.append(f"Match esatto trovato: {exact_str}")
                thought_log.append(f"Livello geografico utilizzato: {geo_used}")

                loc_found = best_match.get("location", "")
                if (
                    geography.lower() not in ["not specified", ""]
                    and loc_found.lower() != geography.lower()
                ):
                    # Geographic fallback usato dal provider — solo warning, non crash
                    geo_note = (
                        f"Nota: per '{mat}' richiesta geografia '{geography}', "
                        f"usato proxy geografico '{loc_found}' dal database."
                    )
                    assumptions.append(geo_note)
                    logger.info(geo_note)

                idx = best_match.get("index", "?")
                provider_name = best_match.get("providerName", "?")
                val_co2 = best_match.get("environmental_impact", "?")
                thought_log.append(f"Riga Excel trovata: {idx} - {provider_name} - {loc_found} - {val_co2}")

                comp["material_source"] = best_match["flowName"]
                comp["unit_impact_value"] = best_match["environmental_impact"]

            # 2. Mapping Geometria → Processo
            geom = comp.get("geometry", "Pezzi Pieni Complessi")
            comp["manufacturing_process"] = GEOMETRY_MAPPING.get(geom, "Injection moulding")

            # Baseline per compatibilità schema
            comp["baseline_environmental_impact"] = comp["unit_impact_value"]
            comp["baseline_cost"] = 1.0
            comp["lifespan_years"] = 10.0

            bom.append(comp)

        # T05: Step 5 — Scomposizione BOM completata
        if ita:
            thought_log.append(f"Passo 5: BOM generata con {len(bom)} componenti.")
        else:
            thought_log.append(f"Step 5: BOM generated with {len(bom)} components.")

        workflow = [w.model_dump() for w in (result.workflow_steps or [])]

        # T05: Step 6 — Calcolo Logistica
        if ita:
            thought_log.append("Passo 6: Calcolo logistica (tkm).")
        else:
            thought_log.append("Step 6: Logistics calculation (tkm).")
        mass = result.total_mass_kg or 1.0

        # T08: Distanza di default con assunzione esplicita se non specificata
        if result.distance_km is not None:
            dist_km = result.distance_km
        else:
            dist_km = 500.0
            # T08: notifica l'assunzione all'utente
            assumptions.append(
                "Logistics distance not specified: using default value 500 km. "
                "Specify the distance from the supplier for a precise calculation."
            )

        tkm = (mass / 1000.0) * dist_km
        logistics = {
            "geography": geography,
            "distance_km": dist_km,
            "tkm": tkm,
        }

        # Aggiungi assunzioni LLM alle nostre
        if result.assumptions_made:
            assumptions.extend(result.assumptions_made)

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
