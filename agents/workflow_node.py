import json
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from agents.state import AgentState
from core.config import settings
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
    thought_log.append("Executing Workflow & BOM Ideator (7 Steps)...")

    llm = ModelFactory.get_model()
    constraints = state.get("constraints", {})
    
    prompt_name = "semantic_ideation_ollama" if settings.llm_provider == "ollama" else "semantic_ideation_api"
    system_prompt = ModelFactory.get_system_prompt(prompt_name).format(
        user_input=state.get("user_input", ""),
        constraints=json.dumps(constraints),
    )
    
    user_prompt = f"""
Product Description: {state.get("user_input", "")}
Constraints: {json.dumps(constraints)}

Esegui i seguenti Passi per la Generazione della BOM:
1. Analisi Entità: Determina se l'input è solo un "Materiale" (is_material_only=True) o un "Prodotto" completo (False).
2. Selezione Materiale: Se il materiale non è specificato per un componente, scegline uno plausibile e segnalalo in "assumptions_made".
3. Vincolo Geometrico & Scomposizione: Scomponi il prodotto. Per ogni componente definisci la geometria ESATTAMENTE come uno tra: "Corpi Cavi", "Pezzi Pieni Complessi", "Film", "Profili/Tubi".
4. Gap Analysis: Se mancano Massa Totale (in kg), Geografia (luogo/distanza) o uno dei 4 Pilastri, imposta is_interview_complete=False e scrivi in interview_questions i dati mancanti.
"""

    chain = llm.with_structured_output(WorkflowAndBOMResponse)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        result: WorkflowAndBOMResponse = await asyncio.to_thread(
            _invoke_structured, chain, llm, WorkflowAndBOMResponse, messages
        )
        
        if not result.is_interview_complete:
            thought_log.append("Interrupt: Missing constraints, geography or mass. Pausing.")
            missing_text = "Non posso procedere perché mancano queste informazioni:\\n"
            for q in result.interview_questions:
                missing_text += f"- {q}\\n"
            return {
                "pending_feedback": missing_text,
                "thought_log": thought_log,
                "current_lca_step": 7
            }
        
        bom = []
        provider = get_lca_provider()
        
        for comp_data in result.components or []:
            comp = comp_data.model_dump()
            mat = comp.get("material", "unknown")
            
            # 1. Fuzzy Match Material in DataSet.xlsx
            best_match = provider.find_closest_match(mat)
            if best_match:
                comp["material_source"] = best_match["flowName"]
                comp["unit_impact_value"] = best_match["environmental_impact"]
            else:
                comp["material_source"] = "Generic Profile (Fallback)"
                comp["unit_impact_value"] = 3.5  # fallback
            
            # 2. Map Geometry to Process
            geom = comp.get("geometry", "Pezzi Pieni Complessi")
            comp["manufacturing_process"] = GEOMETRY_MAPPING.get(geom, "Injection moulding")
            
            # Set baselines for schemas compatibility
            comp["baseline_environmental_impact"] = comp["unit_impact_value"]
            comp["baseline_cost"] = 1.0
            comp["lifespan_years"] = 10.0
            
            bom.append(comp)
            
        workflow = [w.model_dump() for w in (result.workflow_steps or [])]
        
        # Calculate Logistics Data
        mass = result.total_mass_kg or 1.0
        # Default distance if geography is vague, just assume 500km for now if not specified.
        # This is a simplification. The user prompt should ideally extract a distance.
        dist_km = 500.0  
        tkm = (mass / 1000.0) * dist_km
        logistics = {
            "geography": result.geography or "Unknown",
            "distance_km": dist_km,
            "tkm": tkm
        }
            
        return {
            "bom": bom,
            "workflow_steps": workflow,
            "thought_log": thought_log,
            "current_lca_step": 7,
            "detected_geometry": result.components[0].geometry if result.components else "Unknown",
            "logistics_data": logistics,
            "assumptions_list": result.assumptions_made
        }

    except Exception as exc:
        logger.error(f"Workflow Ideation failed: {exc}")
        thought_log.append(f"⚠ Errore ({exc}).")
        
        return {
            "pending_feedback": "Errore durante l'analisi. Riprovare.",
            "thought_log": thought_log
        }
