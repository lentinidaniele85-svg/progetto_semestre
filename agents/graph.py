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


def route_after_feedback(state: AgentState):
    """Routing in uscita dal nodo unificato human_feedback_processor (T08).
    - 'workflow' → procedi alla material ideation (approvazione BOM+workflow)
    - 'constraints' / 'interview' → torna al workflow ideator
    """
    if state.get("current_phase") == "workflow":
        return "material_ideator"
    return "workflow_bom_ideator"

def build_graph(mode: str = "interactive", checkpointer=None):
    """
    Build and compile the Co-Pilot optimization graph.
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
    #   phase='workflow'    → material_ideator
    #   phase='constraints' o 'interview' → workflow_bom_ideator
    graph.add_conditional_edges(
        "human_feedback_processor",
        route_after_feedback,
        {
            "workflow_bom_ideator": "workflow_bom_ideator",
            "material_ideator": "material_ideator",
        },
    )

    # Edge: workflow ideator → sempre human_feedback_processor
    graph.add_edge("workflow_bom_ideator", "human_feedback_processor")
    graph.add_edge("material_ideator", "lca_validator")
    graph.add_edge("lca_validator", "mcda_scorer")
    graph.add_edge("mcda_scorer", END)

    interrupts = ["human_feedback_processor"] if mode == "interactive" else []  # T08: unico interrupt

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupts,
    )
