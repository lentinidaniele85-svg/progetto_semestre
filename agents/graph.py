from langgraph.graph import END, START, StateGraph

from agents.nodes import (
    constraint_extractor,
    human_feedback_processor,
    lca_validator,
    mcda_scorer,
)
from agents.workflow_node import workflow_bom_ideator
from agents.material_node import material_ideator
from agents.state import AgentState

# ---------------------------------------------------------------------------
# Helper hardcoded: determina il task_type a partire da keyword sul prompt.
# Bypassa completamente l'LLM per evitare classificazioni errate.
# ---------------------------------------------------------------------------
_OPTIMIZATION_KEYWORDS = (
    "ottimizz",   # ottimizza, ottimizzazione, ottimizzare …
    "optimiz",    # optimize, optimization …
    "mcda",
    "multi-criteri",
    "multicriteri",
    "punteggio",  # score / scoring
    "scoring",
    "ranking",
)


def _detect_task_type(state: AgentState) -> str:
    """Rileva il task_type in modo deterministico.

    Priorità:
    1. Keyword nel prompt utente  → forza 'optimization' (override assoluto)
    2. Valore già presente in state['constraints']['task_type']
    3. Default: 'optimization'

    NOTA: il check sulle keyword è il PRIMO passo e sovrascrive qualsiasi
    classificazione LLM errata (Pydantic). Le radici coprono tutte le forme
    flesse italiane e inglesi del termine 'ottimizzazione'/'optimization'.
    """
    import logging as _log
    user_input: str = (state.get("user_input") or "").lower()
    if any(kw in user_input for kw in _OPTIMIZATION_KEYWORDS):
        _log.getLogger(__name__).debug(
            "[ROUTER] Keyword optimization rilevata in user_input → forced 'optimization'"
        )
        return "optimization"

    task_type = (state.get("constraints") or {}).get("task_type", "optimization")
    # Normalizza: qualsiasi valore != 'modeling' viene trattato come ottimizzazione
    return task_type if task_type == "modeling" else "optimization"


def route_after_feedback(state: AgentState) -> str:
    """Routing in uscita dal nodo unificato human_feedback_processor (T08).

    Logica basata su current_phase:
    - 'error'    → END  (blocca il grafo e mostra l'errore in UI)
    - 'workflow' → in base al task_type rilevato:
        - 'modeling'      → lca_validator  (salta material_ideator)
        - 'optimization'  → material_ideator
    - qualsiasi altro (constraints / interview) → workflow_bom_ideator

    NOTA: task_type viene rilevato con _detect_task_type() che controlla
    prima le keyword nel prompt (hardcoded) e poi i constraints dell'LLM.
    """
    phase = state.get("current_phase", "init")
    if phase == "error":
        return END
    if phase == "workflow":
        task_type = _detect_task_type(state)  # hardcoded keyword check
        if task_type == "modeling":
            return "lca_validator"
        return "material_ideator"
    return "workflow_bom_ideator"


def route_after_material(state: AgentState) -> str:
    """Routing in uscita da material_ideator.

    - 'error' → END  (errore tecnico durante ideazione materiali)
    - qualsiasi altro → lca_validator
    """
    phase = state.get("current_phase", "material")
    if phase == "error":
        return END
    return "lca_validator"


def route_after_lca(state: AgentState) -> str:
    """Routing in uscita da lca_validator.

    - 'error'        → END
    - 'modeling'     → END  (solo analisi LCA, nessun ranking MCDA)
    - 'optimization' → mcda_scorer  (pipeline completa)

    NOTA: usa _detect_task_type() per rilevamento hardcoded da keyword.
    """
    phase = state.get("current_phase", "lca")
    if phase == "error":
        return END
    task_type = _detect_task_type(state)  # hardcoded keyword check
    if task_type == "modeling":
        return END
    return "mcda_scorer"

def build_graph(mode: str = "interactive", checkpointer=None):
    """
    Build and compile the Co-Pilot optimization graph.

    Routing esplicito basato su current_phase (T07):
    - "error"    -> END (da qualsiasi nodo, blocca con messaggio di errore)
    - "interview" / "constraints" -> torna a workflow_bom_ideator
    - "workflow" -> material_ideator
    - "material" -> lca_validator
    """
    graph = StateGraph(AgentState)

    graph.add_node("constraint_extractor", constraint_extractor)
    graph.add_node("workflow_bom_ideator", workflow_bom_ideator)
    graph.add_node("material_ideator", material_ideator)
    graph.add_node("lca_validator", lca_validator)
    graph.add_node("mcda_scorer", mcda_scorer)
    graph.add_node("human_feedback_processor", human_feedback_processor)  # T08: nodo unificato

    graph.add_edge(START, "constraint_extractor")
    graph.add_edge("constraint_extractor", "human_feedback_processor")  # checkpoint: constraints

    # Routing in uscita dal nodo unificato:
    #   phase='error'       -> END (blocca il grafo)
    #   phase='workflow'    -> material_ideator
    #   phase='constraints' / 'interview' -> workflow_bom_ideator
    graph.add_conditional_edges(
        "human_feedback_processor",
        route_after_feedback,
        {
            END: END,
            "workflow_bom_ideator": "workflow_bom_ideator",
            "material_ideator": "material_ideator",
            "lca_validator": "lca_validator",
        },
    )

    # Edge: workflow ideator -> sempre human_feedback_processor
    graph.add_edge("workflow_bom_ideator", "human_feedback_processor")

    # Routing in uscita da material_ideator:
    #   phase='error'   -> END (errore tecnico)
    #   qualsiasi altro -> lca_validator
    graph.add_conditional_edges(
        "material_ideator",
        route_after_material,
        {
            END: END,
            "lca_validator": "lca_validator",
        },
    )

    graph.add_conditional_edges(
        "lca_validator",
        route_after_lca,
        {
            END: END,
            "mcda_scorer": "mcda_scorer",
        },
    )
    graph.add_edge("mcda_scorer", END)

    interrupts = ["human_feedback_processor"] if mode == "interactive" else []  # T08: unico interrupt

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupts,
    )
