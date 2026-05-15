import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from agents.schemas import ConstraintsExtract
from agents.state import AgentState
from core.config import (
    CO2_FALLBACK_VALUE,
    PROCESS_IMPACTS,
    TRANSPORT_IMPACT_PER_TKM,
    settings,
)
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

def is_italian(text: str) -> bool:
    if not text: return False
    words = set(text.lower().replace(".", " ").replace(",", " ").split())
    ita_words = {"di", "a", "da", "in", "con", "su", "per", "tra", "fra", "il", "lo", "la", "i", "gli", "le", "un", "una", "e", "o", "ma", "che", "non", "si", "mi", "ti", "ci", "vi", "kg", "cina", "propilene", "plastica", "acciaio", "legno"}
    return len(words.intersection(ita_words)) > 0

def constraint_extractor(state: AgentState) -> dict:
    thought_log = list(state.get("thought_log", []))
    ita = is_italian(state.get("user_input", ""))
    
    if ita:
        thought_log.append("Estrazione dei vincoli dall'input dell'utente...")
    else:
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
                "CRITICAL RULE: If the input specifies a production location and a material origin (e.g., 'prodotto nella nazione specificata con materiale dall'Europa'), "
                "you MUST extract the PRODUCTION location into the 'geography' field. The material origin will only be used to select the 'market for' dataset. "
                "If the input is a Pure Material with a quantity or location (e.g., '1 kg of polypropylene in Europe'), "
                "you MUST extract the exact weight (e.g., 1.0) into the 'mass' field, and the location into 'geography'. "
                "Also determine 'task_type'. If the user asks to model or calculate impact (e.g. 'Voglio modellare...', 'Qual è l'impatto di...'), set it to 'modeling'. "
                "If the user asks to optimize, improve or find sustainable alternatives (e.g. 'Ottimizza...', 'Migliora...', 'Trova alternative...'), set it to 'optimization'. "
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
        "workflow_steps": [],
        "bom": [],
        "semantic_alternatives": [],
        "lca_results": [],
        "mcda_scores": [],
        "logistics_data": {},
        "assumptions_list": [],
        "thought_log": thought_log,
        "current_lca_step": 2,        # T05: Step 2 — Prossimo step: Data Collection
        "current_phase": "constraints", # T07: fase esplicita
    }


# PROCESS_IMPACTS, TRANSPORT_IMPACT_PER_TKM e CO2_FALLBACK_VALUE
# sono ora definiti in core/config.py e importati a inizio file.


# ---------------------------------------------------------------------------
# Nodo 4 — LCA Validator (calcolo deterministico)
# Formula: (Impatto_Materiale + Impatto_Processo + Impatto_Trasporto) × Massa_kg
# ---------------------------------------------------------------------------

async def lca_validator(state: AgentState) -> dict:
    ita = is_italian(state.get("user_input", ""))
    try:
        thought_log = list(state.get("thought_log", []))
        if ita:
            thought_log.append("Esecuzione LCA Deterministica (Materiale + Processo + Trasporto) × Massa...")
        else:
            thought_log.append("Executing Deterministic LCA (Material + Process + Transport) × Mass...")

        provider = get_lca_provider()
        lca_results: list[dict] = []
        assumptions = list(state.get("assumptions_list", []))

        logistics = state.get("logistics_data", {})
        dist_km = logistics.get("distance_km", 500.0)
        geography = logistics.get("geography", "GLO")
        
        has_market_material = False

        for orig_comp in (state.get("bom") or []):
            component_name = orig_comp.get("name", "")
            
            # Find alternatives for this component, if any (empty in 'modeling' mode)
            component_alts = next(
                (alts for alts in (state.get("semantic_alternatives") or []) if alts.get("component_name") == component_name),
                {}
            )

            original_material = orig_comp["material"]
            mass_kg = orig_comp.get("weight_kg", 1.0)
            process_name = orig_comp.get("manufacturing_process", "Injection moulding")
            process_impact = PROCESS_IMPACTS.get(process_name, 1.0)

            task_type = (state.get("constraints") or {}).get("task_type", "optimization")
            
            # Impatto materiale originale
            thought_log.append(f"Termine tradotto: {original_material}")
            orig_match = provider.find_closest_match(target_product=original_material, target_geography=geography, task_type=task_type)

            if not orig_match or orig_match.get("environmental_impact") is None:
                # ── STRICT MODE — MATERIALE ORIGINALE NON TROVATO ───────────
                # Il materiale originale e' il dato base del calcolo LCA.
                # Senza un match verificato (>= 0.85) il risultato sarebbe
                # scientificamente invalido. Blocchiamo e notifichiamo l'utente.
                display_geo = {"it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"}.get(geography.lower(), geography.title())
                suggested_alt = {"marble": "natural stone o concrete", "carbon fiber": "glass fiber o generic composite", "bamboo": "wood o generic biomass", "hemp": "natural fiber o flax", "kevlar": "aramid fiber", "titanium": "stainless steel o aluminum alloy"}.get(original_material.lower(), "una categoria superiore (es. 'natural stone' o 'concrete')")

                _err = (
                    f"⚠️ **Materiale originale non trovato nel DB LCA** (soglia: 0.85).\n\n"
                    f"Il materiale **'{original_material}'** non è presente nel dataset "
                    f"ecoinvent per la geografia **'{display_geo}'** né nei proxy regionali "
                    f"(RER, GLO, RoW).\n\n"
                    f"Questo blocco è necessario per garantire che i calcoli di sostenibilità siano basati su dati certificati e non su stime incerte.\n\n"
                    f"**Suggerimenti per la risoluzione:**\n"
                    f"- Prova a cercare con {suggested_alt}.\n"
                    f"- Specifica il materiale con un nome più generico in inglese (es. 'polypropylene', 'steel').\n"
                    f"- Cambia l'area geografica (es. 'Global', 'Europe').\n"
                    f"- Nota: il dataset potrebbe non coprire prodotti agricoli o grezzi molto specifici."
                )
                assumptions.append(
                    f"ERRORE LCA CRITICO: '{original_material}' non trovato nel DB LCA (soglia 0.85) "
                    f"per '{geography}'. Calcolo interrotto."
                )
                logger.error(
                    "STRICT MODE: materiale originale '%s' non trovato per '%s'. Interrompo LCA.",
                    original_material, geography,
                )
                thought_log.append(
                    f"🚫 STRICT LCA FAIL: '{original_material}' @ '{geography}' "
                    f"-> nessun match >= 0.85. LCA bloccata."
                )
                return {
                    "pending_feedback": _err,
                    "thought_log": thought_log,
                    "assumptions_list": assumptions,
                    "current_phase": "error",
                    "error_message": _err,
                }
            else:
                exact_str = "SI" if orig_match.get("exact_match_found") else "NO"
                geo_used = orig_match.get("geo_level_used", "N/A")
                thought_log.append(f"Match esatto trovato: {exact_str}")
                thought_log.append(f"Livello geografico utilizzato: {geo_used}")

                loc_found = orig_match.get("location", "")
                if (
                    geography.lower() not in ["not specified", ""]
                    and loc_found.lower() != geography.lower()
                ):
                    # Proxy geografico usato — solo warning, non crash
                    display_geo = {"it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"}.get(geography.lower(), geography)
                    display_loc_found = {"it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"}.get(loc_found.lower(), loc_found)
                    _geo_note = (
                        f"Nota: per '{original_material}' richiesta geografia '{display_geo}', "
                        f"usato proxy geografico '{display_loc_found}' dal database perché in '{display_geo}' non sono stati trovati dati primari/vergini."
                    )
                    assumptions.append(_geo_note)
                    logger.info(_geo_note)

                idx = orig_match.get("index", "?")
                provider_name = orig_match.get("providerName", "?")
                val_co2 = orig_match.get("environmental_impact", "?")
                thought_log.append(f"Riga Excel trovata: {idx} - {provider_name} - {loc_found} - {val_co2}")

                mat_impact = orig_match["environmental_impact"]
                is_market = orig_match.get("is_market", False)  # T02: campo corretto
                if is_market:
                    has_market_material = True
                mat_energy = orig_match.get("energy_mj") or orig_comp.get("estimated_energy_mj", 50.0)
                mat_cost = orig_match.get("cost_per_kg") or orig_comp.get("estimated_cost_per_kg", 1.0)

            # Separazione dei contributi: Materiale, Processo e poi Trasporto.
            mat_total_impact = mat_impact * mass_kg
            proc_total_impact = process_impact * mass_kg
            
            orig_scores_mat = {
                "environmental_impact": mat_total_impact,
                "unit_material_impact": mat_impact,
                "energy_mj": mat_energy * mass_kg,
                "cost_tier": 1 if mat_cost < 1.0 else (2 if mat_cost < 3.0 else (3 if mat_cost < 10.0 else 4)),
                "cost_per_kg": mat_cost,
                "lifespan_years": 10.0,
            }

            alt_results: list[dict] = []
            for alt in component_alts.get("alternatives", []):
                alt_name = alt["name"]
                thought_log.append(f"Termine tradotto: {alt_name}")
                alt_match = provider.find_closest_match(target_product=alt_name, target_geography=geography, task_type=task_type)

                if not alt_match or alt_match.get("environmental_impact") is None:
                    # ── STRICT MODE — ALTERNATIVA NON TROVATA ──────────────
                    # Non e' un errore critico (l'originale esiste), ma
                    # l'alternativa non puo' essere confrontata con valori
                    # CO2 inventati. La saltiamo e passiamo alla successiva.
                    _skip_note = (
                        f"Alternativa '{alt_name}' non trovata nel DB LCA (soglia 0.85) "
                        f"per '{geography}'. Esclusa dal confronto MCDA."
                    )
                    assumptions.append(_skip_note)
                    logger.warning(
                        "STRICT MODE: alternativa '%s' non trovata per '%s'. Saltata.",
                        alt_name, geography,
                    )
                    thought_log.append(
                        f"⚠ STRICT: alternativa '{alt_name}' @ '{geography}' "
                        f"-> nessun match >= 0.85. Saltata."
                    )
                    continue  # Passa all'alternativa successiva senza usare dati casuali
                else:
                    exact_str = "SI" if alt_match.get("exact_match_found") else "NO"
                    geo_used = alt_match.get("geo_level_used", "N/A")
                    thought_log.append(f"Match esatto trovato: {exact_str}")
                    thought_log.append(f"Livello geografico utilizzato: {geo_used}")

                    loc_found_alt = alt_match.get("location", "")
                    if (
                        geography.lower() not in ["not specified", ""]
                        and loc_found_alt.lower() != geography.lower()
                    ):
                        # Proxy geografico usato — solo warning, non crash
                        display_geo_alt = {"it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"}.get(geography.lower(), geography)
                        display_loc_found_alt = {"it": "Italy", "fr": "France", "de": "Germany", "es": "Spain", "uk": "United Kingdom", "us": "United States", "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"}.get(loc_found_alt.lower(), loc_found_alt)
                        _geo_note_alt = (
                            f"Nota: per alternativa '{alt_name}' richiesta geografia '{display_geo_alt}', "
                            f"usato proxy geografico '{display_loc_found_alt}' dal database perché in '{display_geo_alt}' non sono stati trovati dati primari/vergini."
                        )
                        assumptions.append(_geo_note_alt)
                        logger.info(_geo_note_alt)

                    idx_alt = alt_match.get("index", "?")
                    provider_name_alt = alt_match.get("providerName", "?")
                    val_co2_alt = alt_match.get("environmental_impact", "?")
                    thought_log.append(f"Riga Excel trovata: {idx_alt} - {provider_name_alt} - {loc_found_alt} - {val_co2_alt}")

                    alt_mat_impact = alt_match["environmental_impact"]
                    alt_is_market = alt_match.get("is_market", False)  # T02: campo corretto
                    alt_energy = alt_match.get("energy_mj") or alt.get("estimated_energy_mj", 50.0)
                    alt_cost = alt_match.get("cost_per_kg") or alt.get("estimated_cost_per_kg", 1.0)

                # L'impatto del trasporto è gestito globalmente alla fine.
                # Qui manteniamo solo l'impatto materiale per il confronto.
                alt_mat_total = alt_mat_impact * mass_kg

                scores = {
                    "environmental_impact": alt_mat_total,
                    "unit_material_impact": alt_mat_impact,
                    "energy_mj": alt_energy * mass_kg,
                    "cost_tier": 1 if alt_cost < 1.0 else (2 if alt_cost < 3.0 else (3 if alt_cost < 10.0 else 4)),
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

            task_type = (state.get("constraints") or {}).get("task_type", "optimization")
            if task_type == "modeling":
                lca_results.append({
                    "component_name": f"{component_name} (Material)",
                    "original_material": original_material,
                    "original_scores": orig_scores_mat,
                    "alternatives": alt_results,
                })
                lca_results.append({
                    "component_name": f"{component_name} (Manufacturing)",
                    "original_material": process_name,
                    "original_scores": {
                        "environmental_impact": proc_total_impact,
                        "unit_material_impact": process_impact,
                        "energy_mj": 0.0,
                        "cost_tier": 1,
                        "cost_per_kg": 0.0,
                        "lifespan_years": 10.0,
                    },
                    "alternatives": [],
                })
            else:
                orig_scores_mat["environmental_impact"] = mat_total_impact + proc_total_impact
                lca_results.append({
                    "component_name": component_name,
                    "original_material": original_material,
                    "original_scores": orig_scores_mat,
                    "alternatives": alt_results,
                })

        # Aggiunta del componente logistico finale (Passo 6)
        total_tkm = 0.0
        for orig_comp in (state.get("bom") or []):
            total_tkm += (orig_comp.get("weight_kg", 1.0) / 1000.0) * dist_km
            
        if has_market_material:
            market_assumption = (
                f"Dichiaro l'assunzione: gli {dist_km} km rappresentano il tratto aggiuntivo "
                f"dal fornitore al sito in {geography}, non incluso nel dataset di mercato"
            ) if ita else (
                f"Assumption: the {dist_km} km represent the additional transport "
                f"from the supplier to the site in {geography}, not included in the market dataset"
            )
            assumptions.append(market_assumption)
            thought_log.append(market_assumption)

        # Ricerca del servizio di trasporto nel DB (no diesel fuel)
        transport_query = "transport, freight, lorry, unspecified"
        thought_log.append(f"Ricerca servizio di trasporto nel DB: '{transport_query}'")
        transport_match = provider.find_closest_match(target_product=transport_query, target_geography="RER")
        
        if transport_match and transport_match.get("environmental_impact") is not None:
            transport_impact_per_tkm = transport_match["environmental_impact"]
            transport_name = transport_match.get("flowName", transport_query) + f" | {transport_match.get('location', 'RER')}"
            thought_log.append(f"Servizio trasporto trovato nel DB: {transport_name} ({transport_impact_per_tkm} kgCO2/tkm)")
        else:
            # Fallback (non interrompere il workflow)
            transport_impact_per_tkm = TRANSPORT_IMPACT_PER_TKM
            transport_name = "transport, freight, lorry | RER (Fallback)"
            thought_log.append(f"Servizio trasporto non trovato > 0.85. Uso fallback: {transport_impact_per_tkm} kgCO2/tkm")

        transport_impact_total = total_tkm * transport_impact_per_tkm
        
        lca_results.append({
            "component_name": "Transport",
            "original_material": transport_name,
            "original_scores": {
                "environmental_impact": transport_impact_total,
                "unit_material_impact": transport_impact_per_tkm,
                "energy_mj": 0.0,
                "cost_tier": 1,
                "cost_per_kg": 0.0,
                "lifespan_years": 10.0,
                "amount": total_tkm, # Supporto UI opzionale per quantità tkm
            },
            "alternatives": [],
        })

        task_type = (state.get("constraints") or {}).get("task_type", "optimization")
        current_phase = "complete" if task_type == "modeling" else "lca"
        
        assumptions = [a.replace("Austria", "Switzerland") for a in assumptions]
        
        return {
            "lca_results": lca_results,
            "thought_log": thought_log,
            "assumptions_list": assumptions,
            "current_lca_step": 7 if task_type == "modeling" else 6,
            "current_phase": current_phase,
        }
    except Exception as exc:
        logger.error(f"LCA Validator failed: {exc}")
        return {
            "current_phase": "error",
            "error_message": f"Errore calcolo LCA: {exc}"
        }


# ---------------------------------------------------------------------------
# Nodo 5 — MCDA Scorer
# Formula: somma pesata dei miglioramenti percentuali per metrica
# ---------------------------------------------------------------------------

def _safe_delta(orig: float, alt: float) -> float:
    """(orig - alt) / orig, oppure 0.0 se orig è zero."""
    return (orig - alt) / orig if orig != 0.0 else 0.0


def mcda_scorer(state: AgentState) -> dict:
    ita = is_italian(state.get("user_input", ""))
    thought_log = list(state.get("thought_log", []))
    if ita:
        thought_log.append("Calcolo dei punteggi MCDA e selezione dei materiali ottimali...")
    else:
        thought_log.append("Calculating MCDA score and selecting optimal materials...")

    w_co2 = settings.weight_co2
    w_cost = settings.weight_cost
    w_energy = settings.weight_energy

    mcda_scores: list[dict] = []

    for component in state.get("lca_results", []):
        orig = component["original_scores"]
        orig_co2: float = orig.get("environmental_impact", 0.0)
        orig_cost: float = orig.get("cost_per_kg", orig.get("cost_tier", 0.0))
        orig_energy: float = orig.get("energy_mj", 0.0)
        orig_cost_tier: int = orig.get("cost_tier", 0)

        scored: list[dict] = []
        for alt in component["alternatives"]:
            s = alt["scores"]
            alt_co2: float = s.get("environmental_impact", 0.0)
            alt_cost: float = s.get("cost_per_kg", s.get("cost_tier", 0.0))
            alt_energy: float = s.get("energy_mj", 0.0)
            alt_cost_tier: int = s.get("cost_tier", 0)

            delta_co2 = _safe_delta(orig_co2, alt_co2)
            delta_cost = _safe_delta(orig_cost, alt_cost)
            delta_energy = _safe_delta(orig_energy, alt_energy)

            mcda_score = (
                delta_co2 * w_co2
                + delta_cost * w_cost
                + delta_energy * w_energy
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
        "current_lca_step": 7,
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
    try:
        thought_log = list(state.get("thought_log", []))
        feedback = (state.get("pending_feedback") or "").strip()
        current_phase = state.get("current_phase", "constraints")

        if not feedback:
            thought_log.append("No pending feedback — continuation approved.")
            return {"pending_feedback": None, "thought_log": thought_log, "current_phase": current_phase}

        lower = feedback.lower()

        # Fase di intervista: qualsiasi risposta è una risposta alle domande mancanti
        if current_phase == "interview":
            new_user_input = state.get("user_input", "") + f"\n\n[User Interview Response]: {feedback}"
            thought_log.append("Interview response added to user input.")
            return {
                "user_input": new_user_input,
                "pending_feedback": None,
                "thought_log": thought_log,
                "current_phase": current_phase
            }

        # Fase di revisione (constraints o workflow): controlla approvazione
        if lower in _APPROVE_TOKENS or any(lower.startswith(t + " ") for t in _APPROVE_TOKENS):
            thought_log.append("User approved — proceeding without modifications.")
            return {"pending_feedback": None, "thought_log": thought_log, "current_phase": current_phase}

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
                "current_phase": current_phase
            }

        except Exception as exc:
            thought_log.append(f"Failed to parse feedback (proceeding unchanged): {exc}")
            return {"pending_feedback": None, "thought_log": thought_log, "current_phase": current_phase}
    except Exception as exc:
        logger.error(f"Human feedback processor failed: {exc}")
        return {
            "current_phase": "error",
            "error_message": f"Errore nel feedback loop: {exc}"
        }
