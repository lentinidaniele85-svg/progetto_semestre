import json
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from agents.state import AgentState
from core.config import settings
from core.llm_factory import ModelFactory
from agents.schemas import MaterialIdeationResponse
from agents.nodes import _invoke_structured
import asyncio

logger = logging.getLogger(__name__)

async def material_ideator(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    thought_log.append("Executing Material Ideator (FASE 3-4)...")

    llm = ModelFactory.get_model()
    constraints = state.get("constraints", {})
    bom = state.get("bom", [])
    
    prompt_name = (
        "semantic_ideation_ollama"
        if settings.llm_provider == "ollama"
        else "semantic_ideation_api"
    )
    system_prompt = ModelFactory.get_system_prompt(prompt_name).format(
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
"""

    chain = llm.with_structured_output(MaterialIdeationResponse)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        result: MaterialIdeationResponse = await asyncio.to_thread(
            _invoke_structured, chain, llm, MaterialIdeationResponse, messages
        )
        semantic_alternatives = [comp.model_dump() for comp in result.components]
        
        return {
            "semantic_alternatives": semantic_alternatives,
            "thought_log": thought_log
        }

    except Exception as exc:
        logger.error(f"Material Ideation failed: {exc}")
        thought_log.append(f"⚠ Errore di connessione o ideazione ({exc}).")
        
        # User requested explicitly NOT to fallback to generic errors
        return {
            "pending_feedback": "Non posso procedere perché si è verificato un timeout o errore di connessione col modello durante la scelta dei materiali. Vuoi riprovare?",
            "thought_log": thought_log
        }
