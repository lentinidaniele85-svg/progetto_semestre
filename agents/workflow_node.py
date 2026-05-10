import json
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from agents.state import AgentState
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
    assumptions = list(state.get("assumptions_list", []))
    thought_log.append("Esecuzione Workflow & BOM Ideator (7 Passi)...")

    # T05: Step 2 — Lookup Aggregato
    llm = ModelFactory.get_model()
    constraints = state.get("constraints", {})

    system_prompt = ModelFactory.get_system_prompt("semantic_ideation_api").format(
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
            # T05: step rimane a 2 (non avanzare) durante l'intervista
            thought_log.append("Interrupt: dati mancanti. In attesa della risposta utente.")
            missing_text = "Non posso procedere perché mancano queste informazioni:\n"
            for q in result.interview_questions:
                missing_text += f"- {q}\n"
            return {
                "pending_feedback": missing_text,
                "thought_log": thought_log,
                "assumptions_list": assumptions,
                "current_lca_step": 2,          # T05: blocco alla fase 2
                "current_phase": "interview",   # T07: routing esplicito
            }

        # T05: Step 3 — Selezione Materiale (inferenza LLM completata)
        thought_log.append("Passo 3: Selezione materiale completata.")

        bom = []
        provider = get_lca_provider()

        # T05: Step 4 — Vincolo Geometrico & Fuzzy Match materiali
        thought_log.append("Passo 4: Mapping geometria → processo manifatturiero.")

        for comp_data in result.components or []:
            comp = comp_data.model_dump()
            mat = comp.get("material", "unknown")

            # 1. Fuzzy Match del materiale nel DataSet.xlsx
            best_match = provider.find_closest_match(mat)
            if best_match:
                comp["material_source"] = best_match["flowName"]
                comp["unit_impact_value"] = best_match["environmental_impact"]
            else:
                comp["material_source"] = "Profilo Generico (Fallback)"
                comp["unit_impact_value"] = 3.5  # fallback
                # T04: fallback reso visibile
                assumptions.append(
                    f"Dati LCA non trovati per '{mat}': "
                    f"usato valore di fallback 3.5 kg CO₂/kg (impatto PP vergine)."
                )

            # 2. Mapping Geometria → Processo
            geom = comp.get("geometry", "Pezzi Pieni Complessi")
            comp["manufacturing_process"] = GEOMETRY_MAPPING.get(geom, "Injection moulding")

            # Baseline per compatibilità schema
            comp["baseline_environmental_impact"] = comp["unit_impact_value"]
            comp["baseline_cost"] = 1.0
            comp["lifespan_years"] = 10.0

            bom.append(comp)

        # T05: Step 5 — Scomposizione BOM completata
        thought_log.append(f"Passo 5: BOM generata con {len(bom)} componenti.")

        workflow = [w.model_dump() for w in (result.workflow_steps or [])]

        # T05: Step 6 — Calcolo Logistica
        thought_log.append("Passo 6: Calcolo logistica (tkm).")
        mass = result.total_mass_kg or 1.0

        # T08: Distanza di default con assunzione esplicita se non specificata
        geography = result.geography or "Non specificata"
        if result.geography and any(
            char.isdigit() for char in (result.geography or "")
        ):
            # Tenta estrazione numero dalla stringa (es. "Milano, 200 km")
            import re
            numbers = re.findall(r"\d+(?:\.\d+)?", result.geography)
            dist_km = float(numbers[0]) if numbers else 500.0
        else:
            dist_km = 500.0
            # T08: notifica l'assunzione all'utente
            assumptions.append(
                "Distanza logistica non specificata: usato valore di default 500 km. "
                "Specifica la distanza dal fornitore per un calcolo preciso."
            )

        tkm = (mass / 1000.0) * dist_km
        logistics = {
            "geography": geography,
            "distance_km": dist_km,
            "tkm": tkm,
        }

        # Aggiungi assunzioni LLM alle nostre
        if result.assumptions_made:
            assumptions.extend(result.assumptions_made)

        return {
            "bom": bom,
            "workflow_steps": workflow,
            "thought_log": thought_log,
            "current_lca_step": 6,          # T05: Step 6 completato
            "current_phase": "workflow",    # T07: routing esplicito
            "detected_geometry": result.components[0].geometry if result.components else "Sconosciuta",
            "logistics_data": logistics,
            "assumptions_list": assumptions,
        }

    except Exception as exc:
        logger.error(f"Workflow Ideation fallito: {exc}")
        thought_log.append(f"⚠ Errore durante l'analisi ({exc}).")

        return {
            "pending_feedback": "Si è verificato un errore durante l'analisi. Riprovare.",
            "thought_log": thought_log,
            "assumptions_list": assumptions,
            "current_phase": "error",       # T07: routing esplicito su errore
            "error_message": str(exc),
        }
