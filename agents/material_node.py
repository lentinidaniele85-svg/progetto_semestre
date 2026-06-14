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

    llm = ModelFactory.get_model(max_tokens=4000)
    constraints = dict(state.get("constraints", {}))
    
    def map_geo(g):
        if not isinstance(g, str): return g
        return {"it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"}.get(g.lower(), g)

    if constraints.get("geography"):
        constraints["geography"] = map_geo(constraints["geography"])

    bom = state.get("bom", [])

    # NOTA: il blocco "CRITICAL RULE FOR CARBON FIBER / COMPOSITE MATERIALS" nel
    # user_prompt sotto è un workaround specifico per l'attuale
    # dataset_ecoinvent_perfetto.xlsx, che non contiene varianti riciclate/bio-based
    # di carbon fiber sopra la soglia di match 0.85. Se il dataset viene aggiornato
    # con nuovi record di carbon fiber (riciclata/bio-based), questo blocco va
    # ri-verificato e potenzialmente rimosso o aggiornato di conseguenza.
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

CRITICAL RULE FOR CARBON FIBER / COMPOSITE MATERIALS (read carefully — violation causes a 0% optimization result):
The local dataset_ecoinvent_perfetto.xlsx contains NO bio-based, recycled, low-carbon, or
"industrial by-product" variant of carbon fiber — only the standard virgin
"carbon fibre reinforced plastic" record exists (~25.5 kgCO2/kg). ANY alternative whose
chemical identity remains "carbon fiber" plus a sustainability qualifier
(e.g. "recycled carbon fiber", "bio-based carbon fiber", "low-carbon carbon fiber",
"reinforced carbon fiber using industrial by-products") will score BELOW the 0.85
STRICT MODE match threshold, be excluded from the MCDA comparison, and — if this
happens to every proposed alternative — collapse the optimization score to 0%.
For Carbon Fiber components you MUST therefore SWITCH to a different base material
that is verified present in the dataset at high confidence, choosing your 3
alternatives (Eco-Max / Balanced / Drop-in) from this list (do not invent others):
  • base_material="glass fibre"   (modifier=null, is_recycled=false) — a structurally
    comparable composite reinforcement with ~10x lower embodied carbon than carbon
    fiber (~2.5 vs ~25.5 kgCO2/kg); propose as "Eco-Max" or "Drop-in" for components
    where extreme stiffness-to-weight is not the binding constraint.
  • base_material="nylon"         (modifier=null, is_recycled=false) — a
    high-performance engineering polymer (~9.4 kgCO2/kg) viable for structural /
    composite-frame components; propose as "Balanced".
  • base_material="polycarbonate" (modifier=null, is_recycled=false) — another
    verified high-performance structural polymer (~6.2 kgCO2/kg) for lighter-duty
    structural swaps; propose as "Eco-Max".
Use EXACTLY these lowercase base_material strings — do NOT prepend/append adjectives
like "recycled", "bio-based", "low-carbon" or "reinforced ... using ..." to them: the
dataset has NO secondary/recycled records for glass fibre, nylon or polycarbonate, so
is_recycled=true would make the DB search discard every candidate and the alternative
would be excluded from MCDA exactly like the broken carbon-fiber variants above. Put
the sustainability narrative ("~90% lower embodied carbon than virgin carbon fibre",
lighter supply chain, etc.) in the `justification` field instead, and reflect any
trade-off honestly via a lower `structural_match` / `aesthetic_match` score.
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
        # Ticket 3: Prevent infinite context accumulation on retry by reverting state
        original_thought_log = list(state.get("thought_log", []))
        
        # User requested explicitly NOT to fallback to generic errors
        return {
            "pending_feedback": "Non posso procedere perché si è verificato un timeout o errore di connessione col modello durante la scelta dei materiali. Vuoi riprovare?",
            "thought_log": original_thought_log,
            "current_phase": "error",
            "error_message": str(exc)
        }
