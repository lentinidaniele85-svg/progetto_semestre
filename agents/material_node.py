import json
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from agents.state import AgentState
from core.llm_factory import ModelFactory
from agents.schemas import MaterialIdeationResponse
from agents.nodes import _invoke_structured
import asyncio

logger = logging.getLogger(__name__)

async def material_ideator(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    thought_log.append("Esecuzione Material Ideator (Selezione Alternative Sostenibili)...")

    llm = ModelFactory.get_model()
    constraints = state.get("constraints", {})
    bom = state.get("bom", [])
    
    system_prompt = ModelFactory.get_system_prompt("semantic_ideation_api").format(
        user_input=state.get("user_input", ""),
        constraints=json.dumps(constraints),
    )
    
user_prompt = f"""
Product Description: {state.get("user_input", "")}
Constraints: {json.dumps(constraints)}
Approved BOM: {json.dumps(bom)}

Please execute ONLY FASE 3 and FASE 4 exactly as described in your system prompt.
Generate 3 sustainable alternatives for EACH component in the BOM (Eco-Max, Balanced, Drop-in).
Exclude any material not compatible with the user constraints.

CRITICAL RULE FOR MATERIAL CATEGORY CONSISTENCY:
You MUST respect the original Material Category. If the original material is a "Plastic/Polymer" (e.g. Polypropylene, PE, PET), the alternatives MUST be searched among bioplastics (e.g. PLA, PHA) or recycled plastics (e.g. rPP, rPET). 
It is STRICTLY FORBIDDEN to suggest "Cardboard", "Paper/Board", or wood to replace a plastic polymer, unless explicitly requested by the user.
"""

    chain = llm.with_structured_output(MaterialIdeationResponse)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        result: MaterialIdeationResponse = await asyncio.to_thread(
            _invoke_structured, chain, llm, MaterialIdeationResponse, messages
        )
        semantic_alternatives = [comp.model_dump() for comp in result.components]
        
        thought_log.append("Alternative materiali generate con successo.")
        return {
            "semantic_alternatives": semantic_alternatives,
            "thought_log": thought_log,
            "current_lca_step": 5,  # Step 5 (Material ideation completed, ready for LCA)
        }

    except Exception as exc:
        logger.error(f"Material Ideation failed: {exc}")
        thought_log.append(f"⚠ Errore di connessione o ideazione ({exc}).")
        
        # User requested explicitly NOT to fallback to generic errors
        return {
            "pending_feedback": "Non posso procedere perché si è verificato un timeout o errore di connessione col modello durante la scelta dei materiali. Vuoi riprovare?",
            "thought_log": thought_log,
            "current_phase": "error",
            "error_message": str(exc)
        }
