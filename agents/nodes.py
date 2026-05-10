import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from agents.schemas import BOMExtract, ComponentAlternatives, ConstraintsExtract
from agents.state import AgentState
from core.config import settings
from core.llm_factory import ModelFactory
from data.provider_factory import get_lca_provider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Robust structured-output helper
# ---------------------------------------------------------------------------

async def _ainvoke_structured(chain, llm, schema, messages, *, retries: int = 1):
    """Call chain.ainvoke with retries.  On failure, fall back to raw text
    output and attempt manual JSON parsing into *schema*."""
    last_exc = None
    for attempt in range(1 + retries):
        try:
            return await chain.ainvoke(messages)
        except (ValueError, KeyError, TypeError) as exc:
            last_exc = exc
            logger.warning("Structured output attempt %d failed: %s", attempt + 1, exc)
    # Final fallback: ask the LLM for raw text and parse manually.
    try:
        logger.info("Falling back to raw-text LLM call for %s", schema.__name__)
        raw_resp = await llm.ainvoke(messages)
        raw = raw_resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]
        return schema.model_validate_json(raw)
    except Exception as fallback_exc:
        logger.error("Raw-text fallback also failed: %s", fallback_exc)
        raise last_exc from fallback_exc


def _invoke_structured(chain, llm, schema, messages, *, retries: int = 1):
    """Synchronous variant of _ainvoke_structured."""
    last_exc = None
    for attempt in range(1 + retries):
        try:
            return chain.invoke(messages)
        except (ValueError, KeyError, TypeError) as exc:
            last_exc = exc
            logger.warning("Structured output attempt %d failed: %s", attempt + 1, exc)
    try:
        logger.info("Falling back to raw-text LLM call for %s", schema.__name__)
        raw_resp = llm.invoke(messages)
        raw = raw_resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]
        return schema.model_validate_json(raw)
    except Exception as fallback_exc:
        logger.error("Raw-text fallback also failed: %s", fallback_exc)
        raise last_exc from fallback_exc


# ---------------------------------------------------------------------------
# Node 1 — Constraint Extractor
# ---------------------------------------------------------------------------

def constraint_extractor(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    thought_log.append("Extracting constraints from user input...")

    llm = ModelFactory.get_model()
    chain = llm.with_structured_output(ConstraintsExtract)

    messages = [
        SystemMessage(
            content=(
                "You are a product design analyst. Extract the '4 Pilastri' (Dimensioni, Carico Meccanico, Ambiente d'uso, Target di Durata) "
                "from the product description, plus budget, aesthetics, structural_requirements and weight_limit_kg. "
                "Return only explicitly stated or strongly implied fields."
            )
        ),
        HumanMessage(content=state.get("user_input", "")),
    ]

    try:
        logger.debug("constraint_extractor: invoking LLM…")
        result: ConstraintsExtract = _invoke_structured(
            chain, llm, ConstraintsExtract, messages
        )
        logger.debug("constraint_extractor: LLM returned successfully")
        constraints = result.model_dump(exclude_none=True)
    except Exception as exc:
        logger.debug("constraint_extractor: LLM call failed — %s", exc)
        thought_log.append(f"⚠ Constraint extraction failed ({exc}), using empty constraints.")
        constraints = {}

    return {"constraints": constraints, "thought_log": thought_log}


# ---------------------------------------------------------------------------
# Node 2 — BOM Decomposer
# Now async so it can query the LCA provider to populate baseline impact fields.
# ---------------------------------------------------------------------------

async def bom_decomposer(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    thought_log.append("Decomposing product into Bill of Materials with baseline impacts...")

    llm = ModelFactory.get_model()
    chain = llm.with_structured_output(BOMExtract)

    messages = [
        SystemMessage(
            content=(
                "You are a product engineer. Decompose the described product into its "
                "key material components. List each named component separately with its "
                "primary material, estimated weight in kilograms, and its functional_role "
                "(e.g. 'load-bearing frame', 'aesthetic outer casing', 'cushioning layer', "
                "'pivot mechanism'). Be specific about the functional role."
            )
        ),
        HumanMessage(content=state["user_input"]),
    ]

    try:
        logger.debug("bom_decomposer: invoking LLM for BOM extraction…")
        result: BOMExtract = await _ainvoke_structured(
            chain, llm, BOMExtract, messages
        )
        logger.debug("bom_decomposer: LLM returned %d components", len(result.components))
        bom = [comp.model_dump() for comp in result.components]
    except Exception as exc:
        logger.debug("bom_decomposer: LLM call failed — %s", exc)
        thought_log.append(f"⚠ BOM decomposition failed ({exc}), using single-component fallback.")
        bom = [{
            "name": "main_body",
            "material": "plastic",
            "weight_kg": 1.0,
            "functional_role": "general structure",
        }]

    # Populate baseline impact fields from the LCA data layer.
    provider = get_lca_provider()
    for comp in bom:
        try:
            logger.debug("bom_decomposer: querying LCA for material %r", comp["material"])
            matches = await provider.search_materials(comp["material"])
            mat_id = matches[0]["id"] if matches else comp["material"]
            logger.debug("bom_decomposer: resolved %r → %r", comp["material"], mat_id)
            scores = await provider.get_impact_scores(mat_id)
            comp["baseline_environmental_impact"] = scores["environmental_impact"]
            comp["baseline_cost"]   = float(scores["cost_tier"])
            comp["lifespan_years"]  = scores.get("lifespan_years", 10.0)
            logger.debug("bom_decomposer: LCA scores populated for %r", comp["material"])
        except Exception as lca_exc:
            logger.debug("bom_decomposer: LCA lookup failed for %r — %s", comp["material"], lca_exc)
            # Graceful fallback to plastic baseline if LCA lookup fails.
            comp["baseline_environmental_impact"] = 3.5
            comp["baseline_cost"]   = 1.0
            comp["lifespan_years"]  = 8.0

    return {"bom": bom, "thought_log": thought_log}


# ---------------------------------------------------------------------------
# Node 3 — Semantic Ideator (no environmental data — pure LLM knowledge)
# ---------------------------------------------------------------------------

async def semantic_ideator(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    thought_log.append("Generating semantic material alternatives via LLM (mega-SOP)...")

    llm = ModelFactory.get_model()
    constraints = state.get("constraints", {})
    prompt_name = (
        "semantic_ideation_ollama"
        if settings.llm_provider == "ollama"
        else "semantic_ideation_api"
    )
    system_prompt = ModelFactory.get_system_prompt(prompt_name).format(
        user_input=state.get("user_input", ""),
        constraints=json.dumps(constraints),
    )
    alternatives: list[dict] = []

    for component in state.get("bom", []):
        chain = llm.with_structured_output(ComponentAlternatives)

        user_prompt = (
            f"Component: {component['name']}\n"
            f"Current material: {component['material']}\n"
            f"Weight: {component.get('weight_kg', 'unknown')} kg\n"
            f"Functional role: {component.get('functional_role', 'general structural component')}\n"
            f"Baseline Environmental Impact: {component.get('baseline_environmental_impact', 'unknown')} {settings.environmental_impact_unit}/kg\n"
            f"Baseline lifespan: {component.get('lifespan_years', 'unknown')} years\n\n"
            "Follow the SOP exactly. Provide EXACTLY 3 alternatives. Output strictly in JSON."
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        try:
            result: ComponentAlternatives = await _ainvoke_structured(
                chain, llm, ComponentAlternatives, messages
            )
            alternatives.append(result.model_dump())
        except Exception as exc:
            thought_log.append(
                f"⚠ Ideation failed for '{component['name']}' ({exc}), using recycled fallback."
            )
            from agents.schemas import MaterialAlternative
            alternatives.append({
                "component_name": component["name"],
                "alternatives": [
                    MaterialAlternative(
                        name=f"recycled {component['material']}",
                        justification="Fallback: recycled variant of the original material.",
                        aesthetic_match=0.8,
                        structural_match=0.8,
                        estimated_cost_change="Same",
                    ).model_dump()
                ],
            })

    return {"semantic_alternatives": alternatives, "thought_log": thought_log}


# ---------------------------------------------------------------------------
PROCESS_IMPACTS = {
    "Blow moulding": 0.8,
    "Injection moulding": 1.2,
    "Extrusion (film)": 0.5,
    "Extrusion": 0.6
}
TRANSPORT_IMPACT_PER_TKM = 0.05

async def lca_validator(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    thought_log.append("Executing deterministic LCA Validation (Material + Process + Transport) * Mass...")

    provider = get_lca_provider()
    lca_results: list[dict] = []
    
    logistics = state.get("logistics_data", {})
    tkm = logistics.get("tkm", 0.0)

    for component_alts in state.get("semantic_alternatives", []):
        component_name: str = component_alts["component_name"]

        orig_comp = next(
            (c for c in state.get("bom", []) if c["name"] == component_name),
            None
        )
        if not orig_comp:
            continue
            
        original_material = orig_comp["material"]
        mass_kg = orig_comp.get("weight_kg", 1.0)
        process_name = orig_comp.get("manufacturing_process", "Injection moulding")
        process_impact = PROCESS_IMPACTS.get(process_name, 1.0)
        
        # Original component scores
        orig_match = provider.find_closest_match(original_material)
        if orig_match:
            mat_impact = orig_match["environmental_impact"]
            is_market = orig_match.get("is_market", False)
        else:
            mat_impact = 3.5
            is_market = False

        transport_impact = 0.0 if is_market else (tkm * TRANSPORT_IMPACT_PER_TKM)
        total_orig_impact = (mat_impact + process_impact + transport_impact) * mass_kg

        orig_scores = {
            "environmental_impact": total_orig_impact,
            "unit_material_impact": mat_impact,
            "energy_mj": 0.0, "water_l": 0.0,
            "cost_tier": 1, "cost_per_kg": 1.5, "lifespan_years": 10.0,
        }

        alt_results: list[dict] = []
        for alt in component_alts.get("alternatives", []):
            alt_match = provider.find_closest_match(alt["name"])
            if alt_match:
                alt_mat_impact = alt_match["environmental_impact"]
                alt_is_market = alt_match.get("is_market", False)
            else:
                alt_mat_impact = 3.5
                alt_is_market = False
                
            alt_transport_impact = 0.0 if alt_is_market else (tkm * TRANSPORT_IMPACT_PER_TKM)
            total_alt_impact = (alt_mat_impact + process_impact + alt_transport_impact) * mass_kg

            scores = {
                "environmental_impact": total_alt_impact,
                "unit_material_impact": alt_mat_impact,
                "energy_mj": 0.0, "water_l": 0.0,
                "cost_tier": 1, "cost_per_kg": 1.5, "lifespan_years": 10.0,
            }
            
            alt_results.append(
                {
                    "name": alt["name"],
                    "justification": alt["justification"],
                    "aesthetic_match": alt["aesthetic_match"],
                    "structural_match": alt["structural_match"],
                    "estimated_cost_change": alt.get("estimated_cost_change"),
                    "scores": scores,
                }
            )

        lca_results.append(
            {
                "component_name": component_name,
                "original_material": original_material,
                "original_scores": orig_scores,
                "alternatives": alt_results,
            }
        )

    return {"lca_results": lca_results, "thought_log": thought_log, "current_lca_step": 7}


# ---------------------------------------------------------------------------
# Node 5 — MCDA Scorer
# Formula: weighted sum of per-metric % improvement relative to original.
# ---------------------------------------------------------------------------

def _safe_delta(orig: float, alt: float) -> float:
    """(orig - alt) / orig, or 0.0 when orig is zero."""
    return (orig - alt) / orig if orig != 0.0 else 0.0


def mcda_scorer(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    thought_log.append("Calculating MCDA scores and selecting optimal materials...")

    w_co2 = settings.weight_co2
    w_cost = settings.weight_cost
    w_energy = settings.weight_energy
    w_water = settings.weight_water

    mcda_scores: list[dict] = []

    for component in state.get("lca_results", []):
        orig = component["original_scores"]
        orig_co2: float = orig.get("environmental_impact", 0.0)
        orig_cost: float = orig.get("cost_per_kg", orig.get("cost_tier", 0.0))
        orig_energy: float = orig.get("energy_mj", 0.0)
        orig_water: float = orig.get("water_l", 0.0)
        orig_cost_tier: int = orig.get("cost_tier", 0)

        scored: list[dict] = []
        for alt in component["alternatives"]:
            s = alt["scores"]
            alt_co2: float = s.get("environmental_impact", 0.0)
            alt_cost: float = s.get("cost_per_kg", s.get("cost_tier", 0.0))
            alt_energy: float = s.get("energy_mj", 0.0)
            alt_water: float = s.get("water_l", 0.0)
            alt_cost_tier: int = s.get("cost_tier", 0)

            delta_co2 = _safe_delta(orig_co2, alt_co2)
            delta_cost = _safe_delta(orig_cost, alt_cost)
            delta_energy = _safe_delta(orig_energy, alt_energy)
            delta_water = _safe_delta(orig_water, alt_water)

            mcda_score = (
                delta_co2 * w_co2
                + delta_cost * w_cost
                + delta_energy * w_energy
                + delta_water * w_water
            )

            scored.append(
                {
                    "name": alt["name"],
                    "mcda_score": round(mcda_score, 4),
                    "impact_reduction_pct": round(delta_co2 * 100, 2),
                    "cost_reduction_pct": round(delta_cost * 100, 2),
                    "cost_delta": alt_cost_tier - orig_cost_tier,
                    "justification": alt["justification"],
                    "aesthetic_match": alt["aesthetic_match"],
                    "structural_match": alt["structural_match"],
                    "estimated_cost_change": alt.get("estimated_cost_change"),
                }
            )

        scored.sort(key=lambda x: x["mcda_score"], reverse=True)
        best = scored[0] if scored else None

        mcda_scores.append(
            {
                "component_name": component["component_name"],
                "original_material": component["original_material"],
                "alternatives": scored,
                "best_alternative": best,
            }
        )

    return {"mcda_scores": mcda_scores, "thought_log": thought_log}


# ---------------------------------------------------------------------------
# Node 6 — Human Feedback Processor
# Reads state['pending_feedback'] (injected by the UI before resume).
# If it's a simple approval phrase, clears the flag and passes through.
# Otherwise, uses the LLM to parse the natural-language request and patches
# state['bom'] or state['constraints'] accordingly.
# ---------------------------------------------------------------------------

_APPROVE_TOKENS = frozenset({
    "ok", "okay", "approve", "approved", "yes", "y", "continue", "proceed",
    "good", "go ahead", "go", "confirm", "looks good", "lgtm", "next", "done",
    "sure", "fine", "accept", "yep", "yup",
})


async def human_feedback_processor(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    feedback = (state.get("pending_feedback") or "").strip()

    if not feedback:
        thought_log.append("No pending feedback — proceeding as approved.")
        return {"pending_feedback": None, "thought_log": thought_log}

    lower = feedback.lower()
    
    # If BOM is empty, we are in the interview phase. Any feedback, even "ok", is an answer.
    if not state.get("bom"):
        new_user_input = state.get("user_input", "") + f"\n\n[User Interview Answer]: {feedback}"
        thought_log.append("Appended interview answer to user input.")
        return {
            "user_input": new_user_input,
            "pending_feedback": None,
            "thought_log": thought_log
        }

    # Otherwise we are in a review phase (Constraints or Workflow)
    if lower in _APPROVE_TOKENS or any(lower.startswith(t + " ") for t in _APPROVE_TOKENS):
        thought_log.append("User approved — proceeding without modifications.")
        return {"pending_feedback": None, "thought_log": thought_log}

    thought_log.append(f"Applying user feedback: \"{feedback}\"")

    llm = ModelFactory.get_model()

    system_msg = (
        "You are a product design assistant. The user has typed natural-language feedback "
        "to modify the current Bill of Materials or design constraints.\n\n"
        "Output ONLY valid JSON — no markdown fences, no explanation — with this exact shape:\n"
        "{\n"
        "  \"bom_modifications\": [\n"
        "    {\"component_name\": \"<name>\", "
        "\"field\": \"material|weight_kg|functional_role\", \"new_value\": \"<value>\"}\n"
        "  ],\n"
        "  \"constraint_modifications\": {\"<key>\": \"<value>\"},\n"
        "  \"thought\": \"Brief explanation of what changed\"\n"
        "}\n"
        "Set empty arrays/objects when nothing changes for that category."
    )

    user_msg = (
        f"Current BOM:\n{json.dumps(state.get('bom', []), indent=2)}\n\n"
        f"Current constraints:\n{json.dumps(state.get('constraints', {}), indent=2)}\n\n"
        f"User feedback: \"{feedback}\""
    )

    try:
        response = await llm.ainvoke(
            [SystemMessage(content=system_msg), HumanMessage(content=user_msg)]
        )
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]

        patches = json.loads(raw)

        bom = [dict(c) for c in state.get("bom", [])]
        for mod in patches.get("bom_modifications", []):
            for comp in bom:
                if comp.get("name", "").lower() == mod.get("component_name", "").lower():
                    comp[mod["field"]] = mod["new_value"]

        constraints = dict(state.get("constraints", {}))
        constraints.update(patches.get("constraint_modifications", {}))

        thought = patches.get("thought", "Applied user modifications")
        thought_log.append(f"Feedback applied: {thought}")

        return {
            "bom": bom,
            "constraints": constraints,
            "pending_feedback": None,
            "thought_log": thought_log,
        }

    except Exception as exc:
        thought_log.append(f"Could not parse feedback (proceeding unchanged): {exc}")
        return {"pending_feedback": None, "thought_log": thought_log}
