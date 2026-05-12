import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from agents.schemas import ConstraintsExtract
from agents.state import AgentState
from core.llm_factory import ModelFactory
from data.provider_factory import get_lca_provider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper strutturato robusto
# ---------------------------------------------------------------------------

async def _ainvoke_structured(chain, llm, schema, messages, *, retries: int = 1):
    """Chiama chain.ainvoke con retry. In caso di fallimento, tenta il parsing
    manuale del testo grezzo restituito dall'LLM."""
    last_exc = None
    for attempt in range(1 + retries):
        try:
            return await chain.ainvoke(messages)
        except (ValueError, KeyError, TypeError) as exc:
            last_exc = exc
            logger.warning("Structured output attempt %d failed: %s", attempt + 1, exc)
    try:
        logger.info("Fallback raw-text per %s", schema.__name__)
        raw_resp = await llm.ainvoke(messages)
        raw = raw_resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]
        return schema.model_validate_json(raw)
    except Exception as fallback_exc:
        logger.error("Fallback raw-text fallito: %s", fallback_exc)
        raise last_exc from fallback_exc


def _invoke_structured(chain, llm, schema, messages, *, retries: int = 1):
    """Variante sincrona di _ainvoke_structured."""
    last_exc = None
    for attempt in range(1 + retries):
        try:
            return chain.invoke(messages)
        except (ValueError, KeyError, TypeError) as exc:
            last_exc = exc
            logger.warning("Structured output attempt %d failed: %s", attempt + 1, exc)
    try:
        logger.info("Fallback raw-text per %s", schema.__name__)
        raw_resp = llm.invoke(messages)
        raw = raw_resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]
        return schema.model_validate_json(raw)
    except Exception as fallback_exc:
        logger.error("Fallback raw-text fallito: %s", fallback_exc)
        raise last_exc from fallback_exc


# ---------------------------------------------------------------------------
# Nodo 1 — Constraint Extractor
# ---------------------------------------------------------------------------

def constraint_extractor(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    thought_log.append("Extracting constraints from user input...")

    llm = ModelFactory.get_model()
    chain = llm.with_structured_output(ConstraintsExtract)

    messages = [
        SystemMessage(
            content=(
                "You are a product design analyst. Extract the '4 Pillars' "
                "(Dimensions, Mechanical Load, Usage Environment, Target Lifespan) "
                "from the product description, along with budget, aesthetics, "
                "structural requirements, and weight limit. "
                "Return ONLY the fields explicitly stated or strongly implied. RESPOND EXCLUSIVELY IN ENGLISH."
            )
        ),
        HumanMessage(content=state.get("user_input", "")),
    ]

    try:
        logger.debug("constraint_extractor: chiamata LLM...")
        result: ConstraintsExtract = _invoke_structured(
            chain, llm, ConstraintsExtract, messages
        )
        logger.debug("constraint_extractor: LLM risposto correttamente")
        constraints = result.model_dump(exclude_none=True)
    except Exception as exc:
        logger.debug("constraint_extractor: LLM fallito — %s", exc)
        thought_log.append(f"⚠ Constraint extraction failed ({exc}), using empty constraints.")
        constraints = {}

    return {
        "constraints": constraints,
        "thought_log": thought_log,
        "current_lca_step": 1,        # T05: Step 1 — Analisi Entità
        "current_phase": "constraints", # T07: fase esplicita
    }


# ---------------------------------------------------------------------------
# Impatti processo manifatturiero e trasporto (usati da lca_validator)
# ---------------------------------------------------------------------------

PROCESS_IMPACTS = {
    "Blow moulding": 0.8,
    "Injection moulding": 1.2,
    "Extrusion (film)": 0.5,
    "Extrusion": 0.6
}
TRANSPORT_IMPACT_PER_TKM = 0.05


# ---------------------------------------------------------------------------
# Nodo 4 — LCA Validator (calcolo deterministico)
# Formula: (Impatto_Materiale + Impatto_Processo + Impatto_Trasporto) × Massa_kg
# ---------------------------------------------------------------------------

async def lca_validator(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    thought_log.append("Executing Deterministic LCA (Material + Process + Transport) × Mass...")

    provider = get_lca_provider()
    lca_results: list[dict] = []
    assumptions = list(state.get("assumptions_list", []))

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

        # Impatto materiale originale
        orig_match = provider.find_closest_match(original_material)
        if orig_match:
            mat_impact = orig_match["environmental_impact"]
            is_market = orig_match.get("is_market", False)  # T02: campo corretto
            mat_energy = orig_match.get("energy_mj", 50.0)
            mat_cost = orig_match.get("cost_per_kg", 1.0)
        else:
            mat_impact = 3.5  # fallback
            is_market = False
            mat_energy = 50.0
            mat_cost = 1.0
            # T04: fallback visibile all'utente
            assumptions.append(
                f"LCA data not found for '{original_material}': "
                f"using fallback value 3.5 kg CO₂/kg (virgin PP impact)."
            )

        transport_impact = 0.0 if is_market else (tkm * TRANSPORT_IMPACT_PER_TKM)
        total_orig_impact = (mat_impact + process_impact + transport_impact) * mass_kg

        orig_scores = {
            "environmental_impact": total_orig_impact,
            "unit_material_impact": mat_impact,
            "energy_mj": mat_energy * mass_kg,
            "water_l": 1.0 * mass_kg,
            "cost_tier": 1,
            "cost_per_kg": mat_cost,
            "lifespan_years": 10.0,
        }

        alt_results: list[dict] = []
        for alt in component_alts.get("alternatives", []):
            alt_match = provider.find_closest_match(alt["name"])
            if alt_match:
                alt_mat_impact = alt_match["environmental_impact"]
                alt_is_market = alt_match.get("is_market", False)  # T02: campo corretto
                alt_energy = alt_match.get("energy_mj", 50.0)
                alt_cost = alt_match.get("cost_per_kg", 1.0)
            else:
                alt_mat_impact = 3.5
                alt_is_market = False
                alt_energy = 50.0
                alt_cost = 1.0
                # T04: fallback visibile
                assumptions.append(
                    f"LCA data not found for alternative '{alt['name']}': "
                    f"using fallback value 3.5 kg CO₂/kg."
                )

            alt_transport = 0.0 if alt_is_market else (tkm * TRANSPORT_IMPACT_PER_TKM)
            total_alt_impact = (alt_mat_impact + process_impact + alt_transport) * mass_kg

            scores = {
                "environmental_impact": total_alt_impact,
                "unit_material_impact": alt_mat_impact,
                "energy_mj": alt_energy * mass_kg,
                "water_l": 1.0 * mass_kg,
                "cost_tier": 1,
                "cost_per_kg": alt_cost,
                "lifespan_years": 10.0,
            }

            alt_results.append({
                "name": alt["name"],
                "justification": alt["justification"],
                "aesthetic_match": alt["aesthetic_match"],
                "structural_match": alt["structural_match"],
                "estimated_cost_change": alt.get("estimated_cost_change"),
                "scores": scores,
            })

        lca_results.append({
            "component_name": component_name,
            "original_material": original_material,
            "original_scores": orig_scores,
            "alternatives": alt_results,
        })

    return {
        "lca_results": lca_results,
        "thought_log": thought_log,
        "assumptions_list": assumptions,
        "current_lca_step": 7,          # T05: Step 7 — Validazione
        "current_phase": "lca",         # T07: fase esplicita
    }


# ---------------------------------------------------------------------------
# Nodo 5 — MCDA Scorer
# Formula: somma pesata dei miglioramenti percentuali per metrica
# ---------------------------------------------------------------------------

def _safe_delta(orig: float, alt: float) -> float:
    """(orig - alt) / orig, oppure 0.0 se orig è zero."""
    return (orig - alt) / orig if orig != 0.0 else 0.0


def mcda_scorer(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    thought_log.append("Calculating MCDA score and selecting optimal materials...")

    from core.config import settings
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

            scored.append({
                "name": alt["name"],
                "mcda_score": round(mcda_score, 4),
                "impact_reduction_pct": round(delta_co2 * 100, 2),
                "cost_reduction_pct": round(delta_cost * 100, 2),
                "cost_delta": alt_cost_tier - orig_cost_tier,
                "justification": alt["justification"],
                "aesthetic_match": alt["aesthetic_match"],
                "structural_match": alt["structural_match"],
                "estimated_cost_change": alt.get("estimated_cost_change"),
            })

        scored.sort(key=lambda x: x["mcda_score"], reverse=True)
        best = scored[0] if scored else None

        mcda_scores.append({
            "component_name": component["component_name"],
            "original_material": component["original_material"],
            "alternatives": scored,
            "best_alternative": best,
        })

    return {
        "mcda_scores": mcda_scores,
        "thought_log": thought_log,
        "current_phase": "complete",  # T07: fase esplicita
    }


# ---------------------------------------------------------------------------
# Nodo 6 — Human Feedback Processor
# Legge state['pending_feedback'] iniettato dalla UI prima di riprendere.
# Se è una frase di approvazione, pulisce il flag e passa oltre.
# Altrimenti usa l'LLM per parsare la richiesta in linguaggio naturale
# e aggiorna state['bom'] o state['constraints'].
# ---------------------------------------------------------------------------

_APPROVE_TOKENS = frozenset({
    "ok", "okay", "approva", "approvato", "si", "sì", "yes", "y",
    "continua", "procedi", "bene", "vai", "conferma", "perfetto",
    "approve", "approved", "continue", "proceed", "good", "go ahead",
    "go", "looks good", "lgtm", "next", "done", "sure", "fine",
    "accept", "yep", "yup",
})


async def human_feedback_processor(state: AgentState) -> dict:
    """
    Nodo unificato di feedback umano (T08).
    Usa current_phase per determinare il contesto:
      - 'interview'   → accumula risposta nel user_input e torna al workflow ideator
      - 'constraints' → gestisce approvazione/modifica vincoli
      - 'workflow'    → gestisce approvazione/modifica BOM+workflow
    """
    thought_log = list(state.get("thought_log", []))
    feedback = (state.get("pending_feedback") or "").strip()
    current_phase = state.get("current_phase", "constraints")

    if not feedback:
        thought_log.append("No pending feedback — continuation approved.")
        return {"pending_feedback": None, "thought_log": thought_log}

    lower = feedback.lower()

    # Fase di intervista: qualsiasi risposta è una risposta alle domande mancanti
    if current_phase == "interview":
        new_user_input = state.get("user_input", "") + f"\n\n[User Interview Response]: {feedback}"
        thought_log.append("Interview response added to user input.")
        return {
            "user_input": new_user_input,
            "pending_feedback": None,
            "thought_log": thought_log
        }

    # Fase di revisione (constraints o workflow): controlla approvazione
    if lower in _APPROVE_TOKENS or any(lower.startswith(t + " ") for t in _APPROVE_TOKENS):
        thought_log.append("User approved — proceeding without modifications.")
        return {"pending_feedback": None, "thought_log": thought_log}

    thought_log.append(f"Applying user feedback: \"{feedback}\"")

    llm = ModelFactory.get_model()

    system_msg = (
        "You are a product design assistant. The user provided natural language feedback "
        "to modify the Bill of Materials or design constraints.\n\n"
        "Return ONLY valid JSON — no markdown, no explanations — with this structure:\n"
        "{\n"
        "  \"bom_modifications\": [\n"
        "    {\"component_name\": \"<name>\", "
        "\"field\": \"material|weight_kg|functional_role\", \"new_value\": \"<value>\"}\n"
        "  ],\n"
        "  \"constraint_modifications\": {\"<key>\": \"<value>\"},\n"
        "  \"thought\": \"Brief explanation of what changed\"\n"
        "}\n"
        "Use empty arrays/objects when there are no modifications for that category. RESPOND EXCLUSIVELY IN ENGLISH."
    )

    user_msg = (
        f"Current BOM:\n{json.dumps(state.get('bom', []), indent=2)}\n\n"
        f"Current Constraints:\n{json.dumps(state.get('constraints', {}), indent=2)}\n\n"
        f"User Feedback: \"{feedback}\""
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

        thought = patches.get("thought", "User modifications applied")
        thought_log.append(f"Feedback applied: {thought}")

        return {
            "bom": bom,
            "constraints": constraints,
            "pending_feedback": None,
            "thought_log": thought_log,
        }

    except Exception as exc:
        thought_log.append(f"Failed to parse feedback (proceeding unchanged): {exc}")
        return {"pending_feedback": None, "thought_log": thought_log}
