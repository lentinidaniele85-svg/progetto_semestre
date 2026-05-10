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
    if state.get("pending_feedback") is not None and not state.get("bom"):
        return "human_feedback_processor_interview"
    return "human_feedback_processor_workflow"

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
    graph.add_node("human_feedback_processor_constraints", human_feedback_processor)
    graph.add_node("human_feedback_processor_interview", human_feedback_processor)
    graph.add_node("human_feedback_processor_workflow", human_feedback_processor)

    graph.add_edge(START, "constraint_extractor")
    graph.add_edge("constraint_extractor", "human_feedback_processor_constraints")
    graph.add_edge("human_feedback_processor_constraints", "workflow_bom_ideator")
    
    # Conditional edge after workflow/bom ideation
    graph.add_conditional_edges("workflow_bom_ideator", check_interview_complete)
    
    graph.add_edge("human_feedback_processor_interview", "workflow_bom_ideator")
    
    # After user approves workflow, proceed to material ideation
    graph.add_edge("human_feedback_processor_workflow", "material_ideator")
    graph.add_edge("material_ideator", "lca_validator")
    graph.add_edge("lca_validator", "mcda_scorer")
    graph.add_edge("mcda_scorer", END)

    interrupts = [
        "human_feedback_processor_constraints",
        "human_feedback_processor_interview",
        "human_feedback_processor_workflow"
    ] if mode == "interactive" else []

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupts,
    )
