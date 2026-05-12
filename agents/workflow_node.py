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
    thought_log = list(state.get("thought_log", []))
    assumptions = list(state.get("assumptions_list", []))
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

Execute the 7 Steps defined in your System Prompt and provide the BOM Generation output.
Specifically ensure that:
- You determine if it's a material or object (Step 1).
- You provide missing data as interview_questions if needed (Step 7).
- You extract logistics distance_km if explicitly provided (Step 6).
- You declare any assumptions made in assumptions_made (Step 3, 6, 7).
- You use the EXACT geometry mappings (Step 4).
RESPOND EXCLUSIVELY IN ENGLISH.
"""

    chain = llm.with_structured_output(WorkflowAndBOMResponse)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        result: WorkflowAndBOMResponse = await asyncio.to_thread(
            _invoke_structured, chain, llm, WorkflowAndBOMResponse, messages
        )

        if not result.is_interview_complete:
            # T05: step rimane a 2 (non avanzare) durante l'intervista
            thought_log.append("Interrupt: missing data. Waiting for user response.")
            missing_text = "I cannot proceed because the following information is missing:\n"
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
        thought_log.append("Step 3: Material selection completed.")

        bom = []
        provider = get_lca_provider()

        # T05: Step 4 — Vincolo Geometrico & Fuzzy Match materiali
        thought_log.append("Step 4: Mapping geometry → manufacturing process.")

        for comp_data in result.components or []:
            comp = comp_data.model_dump()
            mat = comp.get("material", "unknown")

            # 1. Fuzzy Match del materiale nel DataSet.xlsx
            best_match = provider.find_closest_match(mat)
            if best_match:
                comp["material_source"] = best_match["flowName"]
                comp["unit_impact_value"] = best_match["environmental_impact"]
            else:
                comp["material_source"] = "Generic Profile (Fallback)"
                comp["unit_impact_value"] = 3.5  # fallback
                # T04: fallback reso visibile
                assumptions.append(
                    f"LCA data not found for '{mat}': "
                    f"using fallback value 3.5 kg CO₂/kg (virgin PP impact)."
                )

            # 2. Mapping Geometria → Processo
            geom = comp.get("geometry", "Pezzi Pieni Complessi")
            comp["manufacturing_process"] = GEOMETRY_MAPPING.get(geom, "Injection moulding")

            # Baseline per compatibilità schema
            comp["baseline_environmental_impact"] = comp["unit_impact_value"]
            comp["baseline_cost"] = 1.0
            comp["lifespan_years"] = 10.0

            bom.append(comp)

        # T05: Step 5 — Scomposizione BOM completata
        thought_log.append(f"Step 5: BOM generated with {len(bom)} components.")

        workflow = [w.model_dump() for w in (result.workflow_steps or [])]

        # T05: Step 6 — Calcolo Logistica
        thought_log.append("Step 6: Logistics calculation (tkm).")
        mass = result.total_mass_kg or 1.0

        # T08: Distanza di default con assunzione esplicita se non specificata
        geography = result.geography or "Not specified"
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
            "current_lca_step": 6,          # T05: Step 6 completato
            "current_phase": "workflow",    # T07: routing esplicito
            "detected_geometry": result.components[0].geometry if result.components else "Unknown",
            "logistics_data": logistics,
            "assumptions_list": assumptions,
        }

    except Exception as exc:
        logger.error(f"Workflow Ideation fallito: {exc}")
        thought_log.append(f"⚠ Error during analysis ({exc}).")

        return {
            "pending_feedback": "An error occurred during analysis. Please try again.",
            "thought_log": thought_log,
            "assumptions_list": assumptions,
            "current_phase": "error",       # T07: routing esplicito su errore
            "error_message": str(exc),
        }
