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


def check_interview_complete(state: AgentState):
    """Routing basato su current_phase esplicito (T07/T08).
    Torna sempre a 'human_feedback_processor' — il nodo usa current_phase
    internamente per distinguere intervista da approvazione workflow.
    """
    return "human_feedback_processor"


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

    # Conditional edge: workflow ideator → sempre human_feedback_processor
    # (il nodo distingue interview vs approvazione workflow via current_phase)
    graph.add_conditional_edges(
        "workflow_bom_ideator",
        check_interview_complete,
        {"human_feedback_processor": "human_feedback_processor"},
    )
    graph.add_edge("material_ideator", "lca_validator")
    graph.add_edge("lca_validator", "mcda_scorer")
    graph.add_edge("mcda_scorer", END)

    interrupts = ["human_feedback_processor"] if mode == "interactive" else []  # T08: unico interrupt

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupts,
    )
