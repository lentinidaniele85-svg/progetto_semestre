import copy
import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage  # pyrefly: ignore [missing-import]

from agents.schemas import ConstraintsExtract
from agents.state import AgentState
from core.config import (
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

    llm = ModelFactory.get_model(max_tokens=500)
    chain = llm.with_structured_output(ConstraintsExtract)

    messages = [
        SystemMessage(
            content=(
                "You are a product design analyst. Extract the '4 Pillars' "
                "(Dimensions, Mechanical Load, Usage Environment, Target Lifespan) "
                "from the product description, along with budget, aesthetics, "
                "structural requirements, and weight limit.\n\n"
                "CRITICAL EXTRACTION RULES:\n"
                "1. LITERAL EXTRACTION ONLY: Extract values ONLY if explicitly mentioned in the user's text. NEVER make assumptions, deductions, or logical inferences. If a field is not present word-for-word or clearly specified, it MUST be left as null.\n"
                "2. NO LANGUAGE BIAS: The language of the prompt (Italian, English, French, etc.) DOES NOT imply the production location. NEVER infer 'geography' or 'supplier_country' based on the language. If the country/geography is not explicitly named (e.g., 'in Italia', 'produced in Germany'), the field MUST remain null.\n"
                "3. NO DOMAIN INFERENCE: Do not guess the 'usage_environment' based on the material. Even if construction PVC pipes are typically used outdoors, if the user does not explicitly write 'outdoor', 'esterno' or similar, the field MUST remain null.\n\n"
                "GEOGRAPHY RULES:\n"
                "- 'geography': the PRODUCTION/MANUFACTURING location (where the product is MADE).\n"
                "  This is NEVER the shipping/delivery destination. It drives the ecoinvent dataset location.\n"
                "- 'supplier_country': the ORIGIN of the main raw material (where it comes from).\n"
                "- 'destination_country': the DELIVERY destination (where it is shipped TO).\n"
                "- CRITICAL: if the user says 'prodotto in X, trasportato/spedito in Y'\n"
                "  (e.g. 'profilati prodotti in Cina ... fino in Italia'):\n"
                "    geography='X' (China — where it is produced), destination_country='Y' (Italy — delivery).\n"
                "    NEVER set geography to the destination Y. The product is made in X, so geography=X.\n"
                "- If the user says 'materiale da Country_A, assemblato in Country_B':\n"
                "    geography='Country_B', supplier_country='Country_A'\n"
                "- If the user says 'prodotto in Region_X' with no material origin:\n"
                "    geography='Region_X', supplier_country=None (to be asked)\n\n"
                "MASS RULE: Extract 'mass' ONLY if explicitly stated (e.g., '1 kg', '5 tonnes').\n"
                "The 'mass' value MUST ALWAYS be expressed in KILOGRAMS (kg). You MUST convert other units:\n"
                "  - grams → kg: '360 grammi'/'360 g' → 0.36 ; '500 g' → 0.5 ; '50 g' → 0.05\n"
                "  - milligrams → kg: '500 mg' → 0.0005\n"
                "  - tonnes → kg: '5 tonnellate'/'5 t' → 5000 ; '0.5 t' → 500\n"
                "It is STRICTLY FORBIDDEN to output the raw number when the unit is grams/mg/tonnes — convert it to kg FIRST.\n"
                "Example: 'racchetta da 360 grammi' → mass=0.36 (NOT 360).\n"
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


# PROCESS_IMPACTS e TRANSPORT_IMPACT_PER_TKM sono ora definiti in
# core/config.py e importati a inizio file.


# ---------------------------------------------------------------------------
# Nodo 4 — LCA Validator (calcolo deterministico)
# Formula: (Impatto_Materiale + Impatto_Processo + Impatto_Trasporto) × Fattore_di_Scala
#          dove Fattore_di_Scala è coerente con la UOM del record matchato
#          (kg → massa reale; altre unità → quantità di output attesa).
#          Vedi _resolve_scale_factor.
# ---------------------------------------------------------------------------

# Alias riconosciuti come "unità di massa" nella colonna 'unitOfMeasure'
# (ultima colonna di dataset_ecoinvent_perfetto.xlsx). Per questi l'impatto
# è espresso "al kg" → si scala per il peso reale del componente (mass_kg):
# è il comportamento storico, fisicamente corretto.
_MASS_UOM_ALIASES = frozenset({"kg", "kilogram", "kilograms", "kilogrammo", "kilogrammi", ""})


def _resolve_scale_factor(unit_of_measure, mass_kg: float, expected_output: float = 1.0):
    """Determina il fattore di scala coerente con l'Unità di Misura (UOM) del record.

    Il dataset esprime ogni fattore di impatto rispetto alla propria unità
    funzionale (colonna 'unitOfMeasure': 'kg', 'unit', 'm2', 'm3', 'kWh', ...).
    Moltiplicare ciecamente per il peso in kg del componente (mass_kg) un
    impatto espresso "a unità" (es. un intero impianto/macchinario, migliaia
    di kg CO2 per 'unit') produce un errore di magnitudo enorme
    (kg_CO2/unità × kg ≠ kg_CO2).

    Ritorna (scale_factor, uom_mismatch):
      - UOM di massa (kg e alias) → scala per il peso reale del componente
        (mass_kg): comportamento storico, fisicamente corretto.
      - Qualunque altra UOM → scala per la quantità di output attesa
        (default 1.0 = un singolo componente/funzione), evitando di
        applicare un fattore di massa a un valore non riferito al kg.
        Il chiamante è responsabile di registrare un'assunzione esplicita
        quando uom_mismatch=True, per audit e trasparenza.
    """
    uom = (unit_of_measure or "kg").strip().lower()
    if uom in _MASS_UOM_ALIASES:
        return mass_kg, False
    return expected_output, True


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

        # TICKET 1 FIX: Estrai task_type in modo incondizionato all'inizio
        # della funzione per evitare UnboundLocalError se nessun ramo interno
        # (else di is_mat_only) viene mai eseguito.
        task_type = state.get("constraints", {}).get("task_type", "modeling")

        constraints = state.get("constraints") or {}
        logistics = state.get("logistics_data", {})
        dist_km = constraints.get("distance_km")
        if dist_km is None:
            dist_km = logistics.get("distance_km") or 0.0
        geography = constraints.get("geography") or logistics.get("geography", "GLO")

        # ── GUARDIA task_type (1/3) — definita SUBITO, prima del loop ────
        # Alcuni rami condizionali (es. is_material_only=True) saltano il
        # blocco "ricerca dinamica processo" dove 'task_type' veniva
        # storicamente assegnato per la prima volta: la successiva
        # 'await provider.find_closest_match(..., task_type=task_type, ...)'
        # sul materiale originale leggerebbe quindi una variabile mai
        # vincolata in quel ramo → UnboundLocalError. Questa assegnazione
        # iniziale chiude lo scope una volta per tutte; le altre due
        # ri-assegnazioni più sotto (2/3 e 3/3) restano come ri-letture
        # difensive ridondanti dello stesso valore — comportamento invariato.
        task_type = constraints.get("task_type", "optimization")

        # FIX BOM DEEP COPY: iteriamo su una copia profonda della BOM per evitare
        # mutazioni in-place (es. orig_comp["is_market"] = ...) che inquinano il
        # vettore originale di state["bom"] e causano il bug dello split 50/50 sui pesi.
        # La BOM originale mantiene il 100% del peso sul materiale di partenza.
        bom_snapshot = copy.deepcopy(state.get("bom") or [])

        for orig_comp in bom_snapshot:
            component_name = orig_comp.get("name", "")
            
            # Find alternatives for this component, if any (empty in 'modeling' mode)
            component_alts = next(
                (alts for alts in (state.get("semantic_alternatives") or []) if alts.get("component_name") == component_name),
                {}
            )

            original_material = orig_comp["material"]
            mass_kg = orig_comp.get("weight_kg", 1.0)
            is_recycled_comp = orig_comp.get("is_recycled", False)
            
            is_mat_only = orig_comp.get("is_material_only", False)
            mfg_proc_field = orig_comp.get("manufacturing_process")
            geom_field = orig_comp.get("geometry")

            # UOM di default per il contributo di processo: i valori hardcoded
            # in PROCESS_IMPACTS — e l'impatto nullo del gate is_material_only —
            # sono espressi "al kg di materiale lavorato", quindi la UOM di
            # riferimento resta 'kg' (scala = mass_kg). Viene sovrascritta più
            # sotto SOLO se troviamo un match dinamico nel DB con UOM diversa
            # (vedi _resolve_scale_factor / colonna 'unitOfMeasure').
            process_uom = "kg"
            # Anomalia A: nome ecoinvent COMPLETO del record di processo matchato
            # (es. "injection moulding"). Persistito nello stato per essere reso
            # nella tabella di audit "Processi Ecoinvent Matchati" del report HTML,
            # specularmente alle righe Materiale e Trasporto. None se la manifattura
            # è saltata (is_material_only) o se non c'è match DB (si userà il label).
            manufacturing_ecoinvent_name = None
            manufacturing_ecoinvent_id = None  # id ecoinvent del record di processo (per audit ID)

            # Gate condizionale per i materiali sfusi (grezzi): se il
            # componente è is_material_only=True (o manca processo/geometria),
            # la manifattura viene saltata in modo pulito — process_impact=0.0,
            # nessuna ricerca dinamica di processo, nessuna UOM da risolvere.
            if is_mat_only or mfg_proc_field is None or geom_field is None or mfg_proc_field.lower() == "none" or geom_field.lower() == "none":
                process_impact = 0.0
                process_name = "none"
                thought_log.append(f"[GATE] {component_name}: is_material_only={is_mat_only} (mfg_proc={mfg_proc_field}, geom={geom_field}) → process_impact=0.0 (gate condizionale superato)")
            else:
                # HF3: Usa il campo manufacturing_process del componente BOM come query primaria.
                # Questo campo è impostato dal ProcessMapper (workflow_node.py) con stringhe
                # precise come "electronic component production, wafer fabrication".
                # La GEOMETRY_TO_PROCESS è mantenuta solo come fallback display, non come
                # fonte della query DB (eliminando il pattern "silicon injection moulding").
                GEOMETRY_TO_PROCESS = {
                    "Corpi Cavi": "Blow moulding",
                    "Pezzi Pieni Complessi": "Injection moulding",
                    "Film": "Extrusion (film)",
                    "Profili/Tubi": "Extrusion"
                }
                # process_name: label display (per thought log e fallback PROCESS_IMPACTS)
                process_name = mfg_proc_field if mfg_proc_field else GEOMETRY_TO_PROCESS.get(orig_comp.get("geometry"), "Injection moulding").lower()

                task_type = (state.get("constraints") or {}).get("task_type", "optimization")
                
                # --- Ricerca Dinamica Processo ---
                # HF3: La query al DB usa il processo BOM reale (es. "electronic component
                # production, wafer fabrication"), NON il prodotto [materiale + geometria] hardcoded.
                proc_query = mfg_proc_field if mfg_proc_field else f"{original_material} {process_name}"
                proc_match = await provider.find_closest_match(
                    label=proc_query,
                    location=geography,
                    has_transport=False,
                    entity_type="process"
                )
                if proc_match and proc_match.get("environmental_impact") is not None:
                    process_impact = proc_match["environmental_impact"]
                    process_uom = proc_match.get("unit_of_measure", "kg")
                    manufacturing_ecoinvent_name = proc_match.get("providerName")  # Anomalia A
                    manufacturing_ecoinvent_id = proc_match.get("id")  # id ecoinvent del processo
                    thought_log.append(f"[LCA Validation] Processo '{proc_query}' associato al record: {proc_match.get('providerName', '?')}")
                else:
                    _fallback_key = process_name.lower()
                    process_impact = PROCESS_IMPACTS.get(_fallback_key, PROCESS_IMPACTS.get(_fallback_key.capitalize(), 1.0))
                    if process_impact == 1.0 and _fallback_key not in PROCESS_IMPACTS and _fallback_key.capitalize() not in PROCESS_IMPACTS:
                        thought_log.append(f"[WARNING] Processo non mappato ('{_fallback_key}'), applicato default generico 1.0")
                    else:
                        thought_log.append(f"[LCA Validation] Processo '{proc_query}' non trovato nel DB, uso default hardcoded ({_fallback_key}).")
            
            # Impatto materiale originale
            db_index = orig_comp.get("db_index")
            orig_match = None
            if db_index is not None:
                thought_log.append(f"Utilizzo record DB precedentemente selezionato (ID: {db_index}) per integrità referenziale.")
                orig_match_score = await provider.get_impact_scores(db_index)
                if orig_match_score is not None:
                    orig_match = orig_match_score
                    # Mock fields expected by downstream
                    orig_match["exact_match_found"] = True
                    orig_match["geo_level_used"] = "exact (ID match)"

            if orig_match is None:
                thought_log.append(f"Termine tradotto: {original_material}")
                orig_match = await provider.find_closest_match(
                    target_product=original_material,
                    target_geography=geography,
                    task_type=task_type,
                    thought_log=thought_log,
                    is_recycled=is_recycled_comp,
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
                    
                    if not display_loc_found:
                        display_loc_found = "Globale/Non Specificato"
                        
                    _geo_note = (
                        f"Nota: per '{original_material}' richiesta geografia '{display_geo}', "
                        f"usato proxy geografico '{display_loc_found}' dal database perché in '{display_geo}' non sono stati trovati dati primari/vergini."
                    )
                    already_exists = False
                    for i, a in enumerate(assumptions):
                        if f"Nota: per '{original_material}'" in a and "proxy geografico" in a:
                            old_is_regional = "Europe" in a or "RER" in a
                            new_is_global = "Globale" in _geo_note or "Rest of World" in _geo_note or "Rest-of-World" in _geo_note
                            if old_is_regional and new_is_global:
                                already_exists = True
                            else:
                                assumptions[i] = _geo_note
                                already_exists = True
                            break
                    if not already_exists:
                        assumptions.append(_geo_note)
                    logger.info(_geo_note)

                idx = orig_match.get("index", "?")
                provider_name = orig_match.get("providerName", "?")
                loc_display = loc_found or orig_match.get("location", "")
                # Anomalia C: se il match (es. percorso db_index) non porta i metadati
                # descrittivi, li recuperiamo dal record reale via db_index così il
                # thought_log mostra processName e location veri invece di "? - ? - -".
                if (idx == "?" or provider_name == "?" or not loc_display) and db_index is not None:
                    _rec = await provider.get_impact_scores(db_index)
                    if _rec:
                        idx = _rec.get("index", idx)
                        provider_name = _rec.get("providerName", provider_name)
                        loc_display = loc_display or _rec.get("location", "")
                val_co2 = orig_match.get("environmental_impact", "?")
                thought_log.append(f"Riga Excel trovata: {idx} - {provider_name} - {loc_display} - {val_co2}")

                mat_impact = orig_match["environmental_impact"]
                # ── ESTRAZIONE processName BASELINE ─────────────────────────
                # 'providerName' in _build_result corrisponde a row["processname"]
                # (la colonna Excel). Lo salviamo per la tracciabilità nel report.
                baseline_ecoinvent_process_name = orig_match.get("providerName", "N/A")
                is_market = orig_match.get("is_market", False)  # T02: campo corretto
                orig_comp["is_market"] = is_market
                mat_energy = orig_match.get("energy_mj") or orig_comp.get("estimated_energy_mj", 50.0)
                mat_cost = orig_match.get("cost_per_kg") or orig_comp.get("estimated_cost_per_kg", 1.0)
                mat_uom = orig_match.get("unit_of_measure", "kg")

            # ── Coerenza Massa ↔ Unità di Misura (UOM) ───────────────────────
            # Il fattore di impatto NON è sempre "al kg": il dataset riporta per
            # ogni record la propria unità funzionale (ultima colonna del file,
            # 'unitOfMeasure': kg, unit, m2, m3, kWh, ...). Scalare ciecamente
            # per mass_kg un valore "a unità" (es. un intero impianto, migliaia
            # di kg CO2 per 'unit') produrrebbe un errore di magnitudo enorme.
            # _resolve_scale_factor garantisce coerenza: massa reale (mass_kg)
            # per record "al kg" — il caso dominante (~14k record) — quantità
            # di output attesa (default 1 componente) per qualunque altra UOM,
            # con assunzione tracciata per audit/trasparenza.
            mat_scale, mat_uom_mismatch = _resolve_scale_factor(mat_uom, mass_kg)
            proc_scale, proc_uom_mismatch = _resolve_scale_factor(process_uom, mass_kg)
            for _uom_label, _uom_value, _uom_mismatch, _uom_scale in (
                (original_material, mat_uom, mat_uom_mismatch, mat_scale),
                (f"{process_name} (processo)", process_uom, proc_uom_mismatch, proc_scale),
            ):
                if _uom_mismatch:
                    _uom_note = (
                        f"[UOM] '{_uom_label}': record ecoinvent espresso in '{_uom_value}' "
                        f"(≠ 'kg'). Per evitare un errore di magnitudo (impatto-per-unità × "
                        f"massa_kg) uso come fattore di scala la quantità di output attesa "
                        f"({_uom_scale:g}) invece del peso del componente ({mass_kg:g} kg)."
                    )
                    assumptions.append(_uom_note)
                    thought_log.append(f"⚖ {_uom_note}")

            # Separazione dei contributi: Materiale, Processo e poi Trasporto.
            # Il fattore di scala è coerente con la UOM del record: massa reale
            # per i record "al kg" (caso dominante), quantità di output attesa
            # per ogni altra unità (vedi blocco di coerenza UOM qui sopra).
            mat_total_impact = mat_impact * mat_scale
            proc_total_impact = process_impact * proc_scale
            
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
                is_recycled_alt = alt.get("is_recycled", False)
                base_mat_alt = alt.get("base_material", alt_name)
                thought_log.append(f"Termine tradotto: {base_mat_alt} (display: {alt_name})")
                alt_match = await provider.find_closest_match(
                    target_product=base_mat_alt,
                    target_geography=geography,
                    task_type=task_type,
                    thought_log=thought_log,
                    is_recycled=is_recycled_alt,
                )

                if not alt_match or alt_match.get("environmental_impact") is None:
                    # ── STRICT MODE — ALTERNATIVA NON TROVATA ──────────────
                    # Non e' un errore critico (l'originale esiste), ma
                    # l'alternativa non puo' essere confrontata con valori
                    # CO2 inventati. La saltiamo e passiamo alla successiva.
                    _skip_note = (
                        f"Alternativa '{alt_name}' non trovata nel DB LCA "
                        f"per '{geography}' (o aree geografiche proxy). Esclusa dal confronto MCDA."
                    )
                    assumptions.append(_skip_note)
                    logger.warning(
                        "STRICT MODE: alternativa '%s' non trovata per '%s'. Saltata.",
                        alt_name, geography,
                    )
                    thought_log.append(
                        f"⚠ STRICT: alternativa '{alt_name}' @ '{geography}' "
                        f"-> nessun match sufficiente. Saltata."
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
                        
                        if not display_loc_found_alt:
                            display_loc_found_alt = "Globale/Non Specificato"

                        _geo_note_alt = (
                            f"Nota: per alternativa '{alt_name}' richiesta geografia '{display_geo_alt}', "
                            f"usato proxy geografico '{display_loc_found_alt}' dal database perché in '{display_geo_alt}' non sono stati trovati dati primari/vergini."
                        )
                        already_exists = False
                        for i, a in enumerate(assumptions):
                            if f"Nota: per alternativa '{alt_name}'" in a and "proxy geografico" in a:
                                old_is_regional = "Europe" in a or "RER" in a
                                new_is_global = "Globale" in _geo_note_alt or "Rest of World" in _geo_note_alt or "Rest-of-World" in _geo_note_alt
                                if old_is_regional and new_is_global:
                                    already_exists = True
                                else:
                                    assumptions[i] = _geo_note_alt
                                    already_exists = True
                                break
                        if not already_exists:
                            assumptions.append(_geo_note_alt)
                        logger.info(_geo_note_alt)

                    idx_alt = alt_match.get("index", "?")
                    provider_name_alt = alt_match.get("providerName", "?")
                    val_co2_alt = alt_match.get("environmental_impact", "?")
                    thought_log.append(f"Riga Excel trovata: {idx_alt} - {provider_name_alt} - {loc_found_alt} - {val_co2_alt}")

                    alt_mat_impact = alt_match["environmental_impact"]
                    # ── ESTRAZIONE processName ALTERNATIVA ──────────────────
                    alt_ecoinvent_process_name = alt_match.get("providerName", "N/A")
                    alt_energy = alt_match.get("energy_mj") or alt.get("estimated_energy_mj", 50.0)
                    alt_cost = alt_match.get("cost_per_kg") or alt.get("estimated_cost_per_kg", 1.0)
                    alt_uom = alt_match.get("unit_of_measure", "kg")

                # L'impatto del trasporto è gestito globalmente alla fine.
                # Per ottimizzazione eseguiamo lo stesso identico calcolo LCA (Materiale + Processo)
                # Stesso principio di coerenza UOM↔massa applicato al materiale
                # originale (vedi _resolve_scale_factor sopra): evita errori di
                # magnitudo quando l'alternativa proviene da un record con UOM ≠ 'kg'.
                alt_scale, alt_uom_mismatch = _resolve_scale_factor(alt_uom, mass_kg)
                if alt_uom_mismatch:
                    _alt_uom_note = (
                        f"[UOM] alternativa '{alt_name}': record ecoinvent espresso in "
                        f"'{alt_uom}' (≠ 'kg'). Uso come fattore di scala la quantità di "
                        f"output attesa ({alt_scale:g}) invece del peso del componente "
                        f"({mass_kg:g} kg) per evitare un errore di magnitudo."
                    )
                    assumptions.append(_alt_uom_note)
                    thought_log.append(f"⚖ {_alt_uom_note}")

                alt_mat_total = alt_mat_impact * alt_scale
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
                    # processName ecoinvent dell'alternativa (per audit LCA)
                    "ecoinvent_process_name": alt_ecoinvent_process_name,
                    # id ecoinvent dell'alternativa (per la sezione Riferimenti ID)
                    "ecoinvent_id": alt_match.get("id"),
                })

            task_type = (state.get("constraints") or {}).get("task_type", "optimization")
            # id ecoinvent del materiale baseline: dal find (orig_match["id"]) oppure,
            # se è stato usato il record DB pre-selezionato, il db_index È l'id stesso.
            baseline_material_id = orig_match.get("id") or db_index
            if task_type == "modeling":
                lca_results.append({
                    "component_name": f"{component_name} (Material)",
                    "original_material": original_material,
                    "original_scores": orig_scores_mat,
                    "alternatives": alt_results,
                    # processName ecoinvent del materiale baseline (per audit LCA)
                    "ecoinvent_process_name": baseline_ecoinvent_process_name,
                    "ecoinvent_id": baseline_material_id,
                    "ecoinvent_details": {
                        "providerName": orig_match.get("providerName", "?"),
                        "location": orig_match.get("location", ""),
                        "index": orig_match.get("index", "?"),
                    }
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
                    # Anomalia A: nome ecoinvent del processo (fallback al label se nessun
                    # match DB). None per i materiali grezzi (process_name == "none"), così
                    # la tabella di audit NON mostra una riga manifattura fittizia "none".
                    "ecoinvent_process_name": (
                        manufacturing_ecoinvent_name or process_name
                    ) if process_name != "none" else None,
                    "ecoinvent_id": manufacturing_ecoinvent_id if process_name != "none" else None,
                })
            else:
                orig_scores_mat["environmental_impact"] = mat_total_impact + proc_total_impact
                orig_scores_mat["process_unit_impact"] = process_impact
                orig_scores_mat["process_total_impact"] = proc_total_impact
                lca_results.append({
                    "component_name": component_name,
                    "original_material": original_material,
                    "original_scores": orig_scores_mat,
                    "alternatives": alt_results,
                    # processName ecoinvent del materiale baseline (per audit LCA)
                    "ecoinvent_process_name": baseline_ecoinvent_process_name,
                    "ecoinvent_id": baseline_material_id,
                    # Anomalia A: in optimization la fase manifattura è ripiegata in
                    # questo unico componente. Persistiamo qui il processo + il suo
                    # nome ecoinvent così il report può renderla come riga a sé,
                    # specularmente a Streamlit (Material / Manufacturing / Transport).
                    # None se materiale grezzo (process_name == "none") → niente riga.
                    "manufacturing_process_label": process_name if process_name != "none" else None,
                    "manufacturing_ecoinvent_process_name": (
                        manufacturing_ecoinvent_name or process_name
                    ) if process_name != "none" else None,
                    "manufacturing_ecoinvent_id": manufacturing_ecoinvent_id if process_name != "none" else None,
                })

        # ── PATCH MIXED LOGISTICS ──
        transport_impact_total = 0.0
        total_tkm = 0.0
        transport_providers = []
        transport_ids = []  # id ecoinvent dei record di trasporto matchati (per audit ID)
        
        constraints = state.get("constraints") or {}
        global_mode = constraints.get("transport_mode")
        # Fallback condizionale: 'lorry' SOLO se c'è trasporto (distance_km > 0) ma manca il mezzo.
        # Se mancano entrambi, global_mode rimane None e i componenti useranno dist=0 (market for).
        # Il text-scan su user_input è rimosso: la fonte di verità è il campo constraints strutturato.
        global_mode = (global_mode or "").lower() or None

        # Usa la stessa snapshot già copiata (non ri-legge state["bom"] grezzo)
        for orig_comp in bom_snapshot:
            mass_kg = orig_comp.get("weight_kg", 1.0)
            comp_mode = (orig_comp.get("transport_mode") or global_mode)
            if comp_mode:
                comp_mode = comp_mode.lower()
            
            eff_dist = orig_comp.get("distance_km") or logistics.get("distance_km") or 0
            
            if eff_dist > 0:
                # Se la distanza c'è ma manca il mezzo: fallback deterministico a 'lorry'
                if comp_mode is None:
                    comp_mode = "lorry"
                    thought_log.append(f"[Trasporto] Componente '{orig_comp.get('name')}': distanza presente ma mezzo non specificato. Fallback a 'lorry'.")
                c_tkm = (mass_kg / 1000.0) * eff_dist
                total_tkm += c_tkm
                
                search_mode = comp_mode
                if any(w in comp_mode for w in ["lorry", "truck", "strada", "camion"]):
                    search_mode = "transport, freight, lorry, unspecified"
                elif any(w in comp_mode for w in ["ship", "nave", "sea", "ocean"]):
                    search_mode = "transport, freight, sea, transoceanic ship"
                elif any(w in comp_mode for w in ["aircraft", "aereo", "plane"]):
                    search_mode = "transport, freight, aircraft"
                
                comp_mode = search_mode # Normalizza anche nello stato del grafo
                
                transp_match = None
                # Proxy geografico per trasporti: se non trova nella geo locale, prova regionali
                proxy_geos = []
                for g in [geography, "RER", "GLO", "RoW"]:
                    if g not in proxy_geos:
                        proxy_geos.append(g)
                for proxy_geo in proxy_geos:
                    transp_match = await provider.find_closest_match(label=comp_mode, location=proxy_geo, has_transport=True)
                    if transp_match and transp_match.get("environmental_impact") is not None:
                        break
                
                if transp_match and transp_match.get("environmental_impact") is not None:
                    c_factor = transp_match["environmental_impact"]
                    t_name = transp_match.get("flowName") or transp_match.get("providerName") or "?"
                    transport_providers.append(t_name)
                    if transp_match.get("id"):
                        transport_ids.append(transp_match.get("id"))
                    thought_log.append(f"[LCA Validation] Trasporto '{comp_mode}' associato al record: {transp_match.get('providerName', '?')}")
                else:
                    c_factor = SHIP_IMPACT_PER_TKM if "sea" in comp_mode else (AIRCRAFT_IMPACT_PER_TKM if "aircraft" in comp_mode else TRANSPORT_IMPACT_PER_TKM)
                    # Anomalia B: anche senza match DB usiamo la modalità normalizzata
                    # reale (es. "transport, freight, lorry, unspecified") come etichetta,
                    # NON il placeholder generico "Mixed Logistics". Così UI e report
                    # mostrano sempre un nome di flusso descrittivo e identico.
                    transport_providers.append(comp_mode)
                    thought_log.append(f"[LCA Validation] Trasporto '{comp_mode}' non trovato nel DB (nemmeno nei proxy), uso fallback.")
                
                component_transport_impact = c_tkm * c_factor
                transport_impact_total += component_transport_impact
            else:
                # DISTANZA ASSENTE O ZERO: Il trasporto è incluso nel 'market for'
                component_transport_impact = 0
                thought_log.append(f"[LCA Validation] Componente '{orig_comp.get('name')}': nessuna distanza specificata. Impatto trasporto extra impostato a 0 (incluso nel dataset 'market for').")

        # Anomalia B: nome trasporto = nomi reali dei flussi ecoinvent usati
        # (deduplicati). transport_providers ora è SEMPRE popolato quando c'è
        # trasporto (sia su match DB sia su fallback), quindi il placeholder
        # "Mixed Logistics (Dinamicamente Calcolata)" è eliminato. Se non c'è
        # trasporto (total_tkm == 0) resta la nota di logistica integrata.
        if transport_providers:
            transport_name = ", ".join(list(dict.fromkeys(transport_providers)))
        else:
            transport_name = "Trasporto integrato nei dataset 'market' (nessun addizionale)"
             
        lca_results.append({
            "component_name": "Transport",
            "original_material": transport_name,
            "original_scores": {
                "environmental_impact": transport_impact_total,
                "unit_material_impact": 0.0,
                "energy_mj": 0.0,
                "cost_tier": 1,
                "cost_per_kg": 0.0,
                "lifespan_years": 10.0,
                "amount": total_tkm,
            },
            "alternatives": [],
            "ecoinvent_process_name": transport_name,
            # id ecoinvent dei record di trasporto (può essere multiplo se più tratte)
            "ecoinvent_id": ", ".join(list(dict.fromkeys(transport_ids))) if transport_ids else None,
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

            if alt_co2 >= orig_co2:
                if ita:
                    thought_log.append(f"Scartata alternativa '{alt['name']}': impatto CO2 ({alt_co2:.3f}) >= baseline ({orig_co2:.3f}).")
                else:
                    thought_log.append(f"Discarded alternative '{alt['name']}': CO2 impact ({alt_co2:.3f}) >= baseline ({orig_co2:.3f}).")
                continue

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
                # processName ecoinvent dell'alternativa (propagato da lca_validator)
                "ecoinvent_process_name": alt.get("ecoinvent_process_name", "N/A"),
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
            thought_log.append("Interview response received. Extracting missing constraints...")
            
            # --- INGESTIONE ATTIVA DEI CONSTRAINTS ---
            llm = ModelFactory.get_model(max_tokens=500)
            chain = llm.with_structured_output(ConstraintsExtract)
            
            messages = [
                SystemMessage(
                    content=(
                        "You are a product design analyst. The user just provided missing "
                        "information for a product (e.g., mass, geography, transport distance). "
                        "Extract these fields from the user's response. "
                        "Return ONLY fields explicitly stated or strongly implied in the response.\n\n"
                        "CRITICAL RULES:\n"
                        "1. Do NOT extract or change 'task_type' unless the user explicitly requests a change of intent (e.g. 'voglio ottimizzare', 'cambia in ottimizzazione'). If the user uses the word 'autonoma' or 'stima', it does NOT mean optimization.\n"
                        "2. If the user mentions a number followed by 'km' (e.g. '600 km', '450km'), you MUST extract that number as 'distance_km'.\n"
                        "3. If the user mentions 'camion' or 'truck', you MUST extract 'transport_mode': 'lorry'."
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
                    old_task_type = current_constraints.get("task_type")
                    current_constraints.update(new_constraints)
                    
                    if old_task_type and new_constraints.get("task_type") and new_constraints.get("task_type") != old_task_type:
                        if not any(w in feedback.lower() for w in ["ottimizza", "ottimizzare", "optimize", "optimization", "cambia"]):
                            current_constraints["task_type"] = old_task_type
                            thought_log.append(f"Restored original task_type '{old_task_type}' (anti-hallucination protection).")
                            
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

        llm = ModelFactory.get_model(max_tokens=1000)

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

        except Exception:
            thought_log.append("Errore di formattazione interno. Ripristino del checkpoint.")
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
