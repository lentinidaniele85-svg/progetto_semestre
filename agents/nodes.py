import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from agents.schemas import ConstraintsExtract
from agents.state import AgentState
from core.config import (
    CO2_FALLBACK_VALUE,
    PROCESS_IMPACTS,
    TRANSPORT_IMPACT_PER_TKM,
    SHIP_IMPACT_PER_TKM,
    AIRCRAFT_IMPACT_PER_TKM,
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
    ita_words = {"di", "a", "da", "in", "con", "su", "per", "tra", "fra", "il", "lo", "la", "i", "gli", "le", "un", "una", "e", "o", "ma", "che", "non", "si", "mi", "ti", "ci", "vi", "kg"}
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
                "structural requirements, and weight limit.\n\n"
                "GEOGRAPHY RULES:\n"
                "- 'geography': the PRODUCTION/ASSEMBLY location (where the product is made).\n"
                "- 'supplier_country': the ORIGIN of the main raw material (where it comes from).\n"
                "- 'destination_country': the DELIVERY destination (if different from geography).\n"
                "- If the user says 'materiale da Country_A, assemblato in Country_B':\n"
                "    geography='Country_B', supplier_country='Country_A'\n"
                "- If the user says 'prodotto in Region_X' with no material origin:\n"
                "    geography='Region_X', supplier_country=None (to be asked)\n\n"
                "MASS RULE: Extract 'mass' ONLY if explicitly stated (e.g., '1 kg', '5 tonnes').\n"
                "Do NOT infer mass from product type.\n\n"
                "TASK TYPE: 'modeling' if user wants to calculate/model impact. "
                "'optimization' if user wants alternatives/improvements.\n\n"
                "Return ONLY fields explicitly stated or strongly implied. "
                "RESPOND EXCLUSIVELY IN ENGLISH."
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

        constraints = state.get("constraints") or {}
        logistics = state.get("logistics_data", {})
        dist_km = constraints.get("distance_km")
        if dist_km is None:
            dist_km = logistics.get("distance_km") or 0.0
        geography = constraints.get("geography") or logistics.get("geography", "GLO")
        
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
            
            GEOMETRY_TO_PROCESS = {
                "Corpi Cavi": "Blow moulding",
                "Pezzi Pieni Complessi": "Injection moulding",
                "Film": "Film extrusion",
                "Profili/Tubi": "Tube extrusion"
            }
            process_name = GEOMETRY_TO_PROCESS.get(orig_comp.get("geometry"), "Injection moulding")

            task_type = (state.get("constraints") or {}).get("task_type", "optimization")
            
            # --- Ricerca Dinamica Processo ---
            proc_match = provider.find_closest_match(label=process_name, location=geography, has_transport=False)
            if proc_match and proc_match.get("climatechangeimpact") is not None:
                process_impact = proc_match["climatechangeimpact"]
                thought_log.append(f"[LCA Validation] Processo '{process_name}' associato al record: {proc_match.get('processname', '?')}")
            else:
                process_impact = PROCESS_IMPACTS.get(process_name, 1.0)
                thought_log.append(f"[LCA Validation] Processo '{process_name}' non trovato, uso default hardcoded.")
            
            # Impatto materiale originale
            thought_log.append(f"Termine tradotto: {original_material}")
            orig_match = await provider.find_closest_match(
                target_product=original_material,
                target_geography=geography,
                task_type=task_type,
                thought_log=thought_log,
            )

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
                orig_comp["is_market"] = is_market
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
                alt_match = await provider.find_closest_match(
                    target_product=alt_name,
                    target_geography=geography,
                    task_type=task_type,
                    thought_log=thought_log,
                )

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
                # Per ottimizzazione eseguiamo lo stesso identico calcolo LCA (Materiale + Processo)
                alt_mat_total = alt_mat_impact * mass_kg
                alt_total_impact = alt_mat_total + proc_total_impact if task_type == "optimization" else alt_mat_total

                scores = {
                    "environmental_impact": alt_total_impact,
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

        # ── PATCH MIXED LOGISTICS ──
        transport_impact_total = 0.0
        total_tkm = 0.0
        
        constraints = state.get("constraints") or {}
        global_mode = constraints.get("transport_mode")
        if not global_mode:
            user_input_lower = (state.get("user_input") or "").lower()
            if any(w in user_input_lower for w in ["nave", "ship", "container", "sea freight", "ferry", "traghetto"]):
                global_mode = "ship"
            elif any(w in user_input_lower for w in ["aereo", "aircraft", "air freight", "flight"]):
                global_mode = "aircraft"
            else:
                global_mode = "lorry"
        global_mode = global_mode.lower()

        for orig_comp in (state.get("bom") or []):
            mass_kg = orig_comp.get("weight_kg", 1.0)
            comp_mode = (orig_comp.get("transport_mode") or global_mode).lower()
            
            eff_dist = orig_comp.get("distance_km") or logistics.get("distance_km") or 0
            
            if eff_dist > 0:
                c_tkm = (mass_kg / 1000.0) * eff_dist
                total_tkm += c_tkm
                
                transp_match = provider.find_closest_match(label=comp_mode, location=geography, has_transport=True)
                if transp_match and transp_match.get("climatechangeimpact") is not None:
                    c_factor = transp_match["climatechangeimpact"]
                    thought_log.append(f"[LCA Validation] Trasporto '{comp_mode}' associato al record: {transp_match.get('processname', '?')}")
                else:
                    c_factor = SHIP_IMPACT_PER_TKM if comp_mode == "ship" else (AIRCRAFT_IMPACT_PER_TKM if comp_mode == "aircraft" else TRANSPORT_IMPACT_PER_TKM)
                    thought_log.append(f"[LCA Validation] Trasporto '{comp_mode}' non trovato nel DB, uso fallback.")
                
                component_transport_impact = c_tkm * c_factor
                transport_impact_total += component_transport_impact
            else:
                # DISTANZA ASSENTE O ZERO: Il trasporto è incluso nel 'market for'
                component_transport_impact = 0
                thought_log.append(f"[LCA Validation] Componente '{orig_comp.get('name')}': nessuna distanza specificata. Impatto trasporto extra impostato a 0 (incluso nel dataset 'market for').")

        transport_name = "Mixed Logistics (Dinamicamente Calcolata)"
        if total_tkm == 0:
             transport_name = "Trasporto integrato nei dataset 'market' (nessun addizionale)"
             
        lca_results.append({
            "component_name": "Transport",
            "original_material": transport_name,
            "original_scores": {
                "environmental_impact": transport_impact_total,
                "unit_material_impact": 0.0, # Già aggregato
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
        

        unique_thoughts = list(dict.fromkeys(thought_log))
        unique_assumptions = list(dict.fromkeys(assumptions))

        return {
            "lca_results": lca_results,
            "thought_log": unique_thoughts,
            "assumptions_list": unique_assumptions,
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
    """(orig - alt) / orig, oppure 0.0 se orig <= 0.0."""
    return (orig - alt) / orig if orig > 0.0 else 0.0


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

import re

def _clean_token(text: str) -> str:
    """Rimuove punteggiatura finale e normalizza."""
    return re.sub(r"[.,!?;:]+$", "", text.strip().lower())

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
            return {
                "pending_feedback": None,
                "thought_log": thought_log,
                "current_phase": current_phase,
                "interview_attempt_count": state.get("interview_attempt_count", 0)
            }

        lower = _clean_token(feedback)

        # Fase di intervista: qualsiasi risposta è una risposta alle domande mancanti
        if current_phase == "interview":
            new_user_input = state.get("user_input", "") + f"\n\n[User Interview Response]: {feedback}"
            thought_log.append(f"Interview response received. Extracting missing constraints...")
            
            # --- INGESTIONE ATTIVA DEI CONSTRAINTS ---
            llm = ModelFactory.get_model()
            chain = llm.with_structured_output(ConstraintsExtract)
            
            messages = [
                SystemMessage(
                    content=(
                        "You are a product design analyst. The user just provided missing "
                        "information for a product (e.g., mass, geography, transport distance). "
                        "Extract these fields from the user's response. "
                        "Return ONLY fields explicitly stated or strongly implied in the response."
                    )
                ),
                HumanMessage(content=feedback),
            ]
            
            try:
                extracted_result: ConstraintsExtract = await _ainvoke_structured(
                    chain, llm, ConstraintsExtract, messages
                )
                new_constraints = extracted_result.model_dump(exclude_none=True)
                current_constraints = dict(state.get("constraints", {}))
                
                if new_constraints:
                    current_constraints.update(new_constraints)
                    thought_log.append(f"Constraints actively updated from interview: {list(new_constraints.keys())}")
            except Exception as exc:
                logger.warning(f"Failed to extract constraints from interview response: {exc}")
                current_constraints = state.get("constraints", {})
            
            return {
                "user_input": new_user_input,
                "constraints": current_constraints,
                "pending_feedback": None,
                "thought_log": thought_log,
                "current_phase": "constraints",
                "interview_attempt_count": state.get("interview_attempt_count", 0)
            }

        # Fase di revisione (constraints o workflow): controlla approvazione
        if lower in _APPROVE_TOKENS or any(lower.startswith(t + " ") for t in _APPROVE_TOKENS):
            thought_log.append("User approved — proceeding without modifications.")
            return {
                "pending_feedback": None,
                "thought_log": thought_log,
                "current_phase": current_phase,
                "interview_attempt_count": state.get("interview_attempt_count", 0)
            }

        thought_log.append(f"Applying user feedback: \"{feedback}\"")

        llm = ModelFactory.get_model()

        system_msg = (
            "You are a product design assistant helping refine a Bill of Materials (BOM) "
            "and design constraints.\n\n"
            "The user has provided corrective feedback in natural language. "
            "Your task is to generate MINIMAL, SURGICAL modifications — only change what the user explicitly mentioned.\n\n"
            "RULES:\n"
            "1. Do NOT modify fields the user did not mention.\n"
            "2. Do NOT regenerate the entire BOM — only patch the specific components/fields mentioned.\n"
            "3. If the user says 'it's a table not a chair', only update 'name' and related fields, "
            "   keep all materials, weights, and constraints unchanged.\n"
            "4. If the user mentions a specific material change (e.g. 'use steel instead of aluminum'), "
            "   only change the 'material' field for the named component.\n"
            "5. constraint_modifications must be EMPTY {} unless the user explicitly mentioned constraints or logistics (e.g. set 'transport_mode': 'ship', 'lorry', or 'aircraft').\n\n"
            "Return ONLY valid JSON with this structure (no markdown, no explanation):\n"
            "{\n"
            "  \"bom_modifications\": [\n"
            "    {\"component_name\": \"<exact component name>\", "
            "\"field\": \"material|weight_kg|name|functional_role\", \"new_value\": \"<value>\"}\n"
            "  ],\n"
            "  \"constraint_modifications\": {\"<key>\": \"<value>\"},\n"
            "  \"thought\": \"Brief explanation in the user's language of exactly what changed and why\"\n"
            "}\n"
            "Use empty arrays/objects when there are no modifications for that category. "
            "RESPOND IN THE SAME LANGUAGE AS THE USER FEEDBACK."
        )

        user_msg = (
            f"Current BOM:\n{json.dumps(state.get('bom', []), indent=2)}\n\n"
            f"Current Constraints:\n{json.dumps(state.get('constraints', {}), indent=2)}\n\n"
            f"User Feedback: \"{feedback}\"\n\n"
            f"Remember: make ONLY the changes explicitly requested. "
            f"Do NOT change anything the user did not mention."
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
            
            # --- PATCH: Validazione dei vincoli uniti ---
            try:
                validated_constraints = ConstraintsExtract(**constraints)
                constraints = validated_constraints.model_dump(exclude_none=True)
            except Exception as e:
                raise ValueError(f"Validazione Pydantic fallita sui nuovi vincoli: {e}")
            # --------------------------------------------

            thought = patches.get("thought", "User modifications applied")
            thought_log.append(f"Feedback applied: {thought}")

            new_user_input = state.get("user_input", "") + f"\n\n[User Constraints Modification]: {feedback}"

            return {
                "user_input": new_user_input,
                "bom": bom,
                "constraints": constraints,
                "pending_feedback": None,
                "thought_log": thought_log,
                "current_phase": current_phase,
                "interview_attempt_count": state.get("interview_attempt_count", 0)
            }

        except Exception as exc:
            thought_log.append(f"Errore di formattazione interno. Ripristino del checkpoint.")
            return {
                "pending_feedback": "Ho avuto difficoltà a comprendere la correzione tecnica. Puoi riformulare cosa devo modificare?",
                "current_phase": "interview", # ← Riapre il loop senza far avanzare il grafo
                "thought_log": thought_log
            }
    except Exception as exc:
        logger.error(f"Human feedback processor failed: {exc}")
        return {
            "current_phase": "error",
            "error_message": f"Errore nel feedback loop: {exc}"
        }
