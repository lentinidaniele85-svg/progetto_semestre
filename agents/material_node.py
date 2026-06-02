import json
import logging
from langchain_core.messages import HumanMessage, SystemMessage  # pyrefly: ignore [missing-import]
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
    constraints = dict(state.get("constraints", {}))
    
    def map_geo(g):
        if not isinstance(g, str): return g
        return {"it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"}.get(g.lower(), g)

    if constraints.get("geography"):
        constraints["geography"] = map_geo(constraints["geography"])

    bom = state.get("bom", [])
    
    system_prompt = ModelFactory.get_system_prompt("semantic_ideation_api").format(
        user_input=state.get("user_input", ""),
        constraints=json.dumps(constraints),
        geography=constraints.get("geography", "Unknown Geography"),
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

CRITICAL RULE FOR OPTIMIZATION:
When proposing alternatives, try to suggest the exact same material but with better environmental profiles (e.g., "recycled [material]", "secondary [material]", "low-carbon [material]") available in the same geographic region to ensure a 1:1 comparable baseline.
Specifically for Carbon Fiber, ALWAYS use the exact string "Reinforced carbon fiber using industrial by-products" as the sustainable alternative. Generic strings like "low-carbon carbon fiber" will collapse onto the virgin material record in the database.
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
            "current_lca_step": 5,          # Step 5 (Material ideation completed, ready for LCA)
            "current_phase": "material",    # T07: routing esplicito → route_after_material → lca_validator
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
