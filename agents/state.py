from typing import TypedDict, Literal, Optional


class AgentState(TypedDict, total=False):
    """
    Stato condiviso del grafo LangGraph.

    Nota: TypedDict non impone valori di default a runtime.
    Il valore iniziale di `current_phase` è "init"; viene impostato
    esplicitamente da ogni nodo prima di restituire il dizionario.
    """
    user_input: str
    mode: Literal["auto", "interactive"]
    target_product: str
    target_geography: str

    # Fase corrente del grafo — usata per routing esplicito (T07)
    # Valori: "init" | "constraints" | "interview" | "workflow" |
    #         "material" | "lca" | "mcda" | "complete" | "error"
    # Default logico: "init" (impostato da constraint_extractor al primo run)
    current_phase: str
    error_message: Optional[str]  # Messaggio di errore da mostrare in UI (None se nessun errore)

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
    interview_attempt_count: int  # Contatore tentativi intervista (inizia a 0)
    supplier_country: str          # Nazione fornitore estratta
    destination_country: str       # Nazione destinazione estratta
