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


def route_after_feedback(state: AgentState) -> str:
    """Routing in uscita dal nodo unificato human_feedback_processor (T08).

    Logica basata su current_phase:
    - 'error'    → END  (blocca il grafo e mostra l'errore in UI)
    - 'workflow' → material_ideator  (approvazione BOM+workflow completata)
    - qualsiasi altro (constraints / interview) → workflow_bom_ideator
    """
    phase = state.get("current_phase", "init")
    if phase == "error":
        return END
    if phase == "workflow":
        task_type = (state.get("constraints") or {}).get("task_type", "optimization")
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
    - Se modeling, salta mcda_scorer.
    """
    phase = state.get("current_phase", "lca")
    if phase == "error":
        return END
    task_type = (state.get("constraints") or {}).get("task_type", "optimization")
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
