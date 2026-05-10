from typing import TypedDict, Literal


class AgentState(TypedDict, total=False):
    user_input: str
    mode: Literal["auto", "interactive"]

    # Fase corrente del grafo — usata per routing esplicito (T07)
    # Valori: "init" | "constraints" | "interview" | "workflow" |
    #         "material" | "lca" | "mcda" | "complete" | "error"
    current_phase: str
    error_message: str  # Messaggio di errore da mostrare in UI

    constraints: dict
    workflow_steps: list[dict]
    bom: list[dict]
    semantic_alternatives: list[dict]
    lca_results: list[dict]
    mcda_scores: list[dict]
    chat_history: list[dict]
    thought_log: list[str]
    pending_feedback: str  # Messaggio utente iniettato dalla UI prima di riprendere
    current_lca_step: int  # Avanzamento 1-7 del tracker visivo
    detected_geometry: str
    logistics_data: dict
    assumptions_list: list[str]
