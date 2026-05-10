from typing import TypedDict, Literal


class AgentState(TypedDict, total=False):
    user_input: str
    mode: Literal["auto", "interactive"]
    constraints: dict
    workflow_steps: list[dict]
    bom: list[dict]
    semantic_alternatives: list[dict]
    lca_results: list[dict]
    mcda_scores: list[dict]
    chat_history: list[dict]
    thought_log: list[str]
    pending_feedback: str  # Last user message injected by UI before resuming from interrupt
    current_lca_step: int
    detected_geometry: str
    logistics_data: dict
    assumptions_list: list[str]
