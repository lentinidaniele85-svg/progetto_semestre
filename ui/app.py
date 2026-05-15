"""
Streamlit "Glass Box" UI for the Sustainable Product Optimization Agent.

Dual-pane layout:
  Left  (1/3) — chat conversation with co-pilot mode and live thought logs
  Right (2/3) — live dashboard: thought log, BOM table, CO2 chart, MCDA table

In Interactive (Co-Pilot) mode the chat input is NEVER disabled.  When the
graph is paused at a checkpoint, the placeholder tells the user to type
modifications or 'Approve'.  The human_feedback_processor node reads the
pending_feedback field and applies changes before resuming the pipeline.
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uuid

import pandas as pd
import streamlit as st
from langgraph.checkpoint.memory import MemorySaver

from agents.graph import build_graph
from reports.generator import generate_html_report, generate_pdf_report
from core.config import settings

_conn_err_types: list[type[Exception]] = [ConnectionError, OSError]
try:
    import httpx as _httpx
    _conn_err_types.append(_httpx.ConnectError)
except ImportError:
    pass
try:
    from openai import APIConnectionError as _OAIConnError
    _conn_err_types.append(_OAIConnError)
except ImportError:
    pass
_CONNECTION_ERRORS = tuple(_conn_err_types)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Sustainable Product Optimizer",
    layout="wide",
    page_icon="🌿",
)

st.title("🌿 Sustainable Product Optimization Agent")
st.caption("Powered by LangGraph · Glass Box Mode — watch the agent think in real time")

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

_DEFAULTS: dict = {
    "chat_history": [],
    "graph_state": {},
    "thread_id": str(uuid.uuid4()),
    "graph": None,
    "checkpointer": None,
    "awaiting_approval": None,  # None | "bom" | "alternatives" | "interview"
    "mode": "interactive",
    "_graph_mode": "interactive",
    "_last_error": None,  # persists error messages across st.rerun()
}

for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ---------------------------------------------------------------------------
# Helper: graph lifecycle
# ---------------------------------------------------------------------------

def _get_graph():
    """Return the compiled graph, rebuilding when mode has changed."""
    mode = st.session_state.mode
    # Force rebuild if graph contains old nodes
    if st.session_state.graph is not None:
        if "bom_decomposer" in st.session_state.graph.nodes or "holistic_ideator" in st.session_state.graph.nodes:
            st.session_state.graph = None

    if st.session_state.graph is None or st.session_state._graph_mode != mode:
        cp = MemorySaver()
        st.session_state.checkpointer = cp
        st.session_state.graph = build_graph(mode=mode, checkpointer=cp)
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state._graph_mode = mode
    return st.session_state.graph


def _thread_config() -> dict:
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def _next_interrupt(graph, config) -> str | None:
    """Return the name of the next interrupted node, or None if done."""
    try:
        snap = graph.get_state(config)
        return snap.next[0] if snap.next else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helper: streaming with live status + thought log injected into chat
# ---------------------------------------------------------------------------

import asyncio

def _stream(graph, input_state_or_none, config, status_label: str) -> dict:
    """
    Stream graph execution, show live thought updates in an st.status block,
    append NEW thought entries to chat_history as an assistant message, and
    return the last emitted state snapshot.

    Uses graph.astream() (async) because several nodes are async functions.
    nest_asyncio (applied at module level) patches loop.run_until_complete() so
    it can be called re-entrantly when Streamlit already has a running loop.
    We prefer run_until_complete() over asyncio.run() because asyncio.run()
    creates+closes a brand-new loop, bypassing nest_asyncio's patch on the
    *current* loop and causing a deadlock on some Streamlit/Windows combos.
    """
    last: dict = {}
    prev_thought_count = len(st.session_state.graph_state.get("thought_log", []))
    prev_assumptions_count = len(st.session_state.graph_state.get("assumptions_list", []))

    async def _consume():
        nonlocal last, prev_assumptions_count
        async for event in graph.astream(
            input_state_or_none, config, stream_mode="values"
        ):
            last = event
            st.session_state.graph_state = event
            thoughts: list[str] = event.get("thought_log", [])
            if thoughts:
                status.write(f"**Step {len(thoughts)}:** {thoughts[-1]}")
            
            assumptions: list[str] = event.get("assumptions_list", [])
            if len(assumptions) > prev_assumptions_count:
                new_assumptions = assumptions[prev_assumptions_count:]
                for a in new_assumptions:
                    if "fallback" in a.lower() or "3.5" in a:
                        st.toast(f"Data fallback applied: {a}", icon="🚨")
                    else:
                        st.toast(f"Assumption made: {a}", icon="💡")
                prev_assumptions_count = len(assumptions)

    with st.status(status_label, expanded=True) as status:
        # Use the running loop when available (nest_asyncio patches its
        # run_until_complete to allow nesting).  Fall back to asyncio.run()
        # only when no loop exists yet (e.g. first run in a fresh thread).
        try:
            loop = asyncio.get_running_loop()
            loop.run_until_complete(_consume())
        except RuntimeError:
            import sniffio
            sniffio.current_async_library_cvar.set("asyncio")
            asyncio.run(_consume())
        status.update(label="✓ Done", state="complete", expanded=False)

    # Append new thought entries as a "thought" role message in the conversation
    all_thoughts: list[str] = last.get("thought_log", [])
    new_thoughts = all_thoughts[prev_thought_count:]
    if new_thoughts:
        _add_message("thought", new_thoughts)

    return last


# ---------------------------------------------------------------------------
# Helper: chat history
# ---------------------------------------------------------------------------

def _add_message(role: str, content: any) -> None:
    st.session_state.chat_history.append({"role": role, "content": content})


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _process_agent_run(user_input: str, is_feedback: bool = False) -> None:
    _add_message("user", user_input)
    st.session_state._last_error = None
    graph = _get_graph()
    config = _thread_config()
    mode = st.session_state.mode

    if is_feedback:
        graph.update_state(
            config,
            {
                "pending_feedback": user_input,
                "chat_history": list(st.session_state.chat_history),
            },
        )
        stream_input = None
        status_msg = "🤖 Processing your feedback..."
    else:
        stream_input = {
            "user_input": user_input,
            "mode": mode,
            "thought_log": [],
            "chat_history": list(st.session_state.chat_history),
        }
        status_msg = "🤖 Agent is analysing your product..."

    try:
        _stream(graph, stream_input, config, status_msg)
    except _CONNECTION_ERRORS:
        msg = (
            "Unable to connect to the LLM model. "
            "Check that the OPENROUTER_API_KEY is valid and that you have an internet connection."
        )
        st.session_state._last_error = msg
        st.session_state.awaiting_approval = None
        return
    except Exception as exc:
        msg = f"An unexpected error occurred: {exc}"
        st.session_state._last_error = msg
        st.session_state.awaiting_approval = None
        return

    if not is_feedback and mode == "auto":
        _add_message(
            "assistant",
            "Analysis complete! See the dashboard on the right for the full results.",
        )
        st.session_state.awaiting_approval = None
        return

    next_node = _next_interrupt(graph, config)
    phase = st.session_state.graph_state.get("current_phase", "")

    if next_node == "human_feedback_processor" and phase == "constraints":
        constraints = st.session_state.graph_state.get("constraints", {})
        if constraints:
            constraints_str = "\n".join([f"- **{k}**: {v}" for k, v in constraints.items()])
        else:
            constraints_str = "- *(No specific constraints extracted)*"
        
        msg = f"I've extracted these constraints and specifications from your input:\n{constraints_str}\n\n**Are these okay or would you like to add/modify some?** Type your changes or press **Approve** to continue."
        _add_message("assistant", msg)
        st.session_state.awaiting_approval = "constraints"
    elif next_node == "human_feedback_processor" and phase == "interview":
        questions = st.session_state.graph_state.get("pending_feedback", "More details needed.")
        if not is_feedback and questions == "More details needed.":
             questions = "Can you provide more specific details about dimensions, load, and usage environment?"
        msg = f"Some additional details are needed before proceeding:\n\n{questions}\n\n**Please provide the missing specifications.**"
        _add_message("assistant", msg)
        st.session_state.awaiting_approval = "interview"
    elif next_node == "human_feedback_processor" and phase == "workflow":
        msg = "I've mapped the required manufacturing processes and component breakdown.\n\n**Please verify the workflow above.** Type any modifications or hit **Approve** to proceed with Material Ideation."
        _add_message("assistant", msg)
        st.session_state.awaiting_approval = "workflow"
    elif phase == "error":
        msg = "An error occurred during analysis. Please press **Restart Session** to try again."
        _add_message("assistant", msg)
        st.session_state.awaiting_approval = None
    else:
        task_type = st.session_state.graph_state.get("constraints", {}).get("task_type", "optimization")
        if not is_feedback:
            msg = "Analysis complete!" if task_type == "modeling" else "Optimisation complete!"
        else:
            msg = "Analysis complete! Final report is shown in the dashboard." if task_type == "modeling" else "Optimisation complete! Final MCDA scores and the best material recommendations are shown in the dashboard."
        _add_message("assistant", msg)
        st.session_state.awaiting_approval = None


def handle_input(user_input: str) -> None:
    """Start a fresh agent run for a new product description."""
    # Hard Reset e Statelessness: Tabula Rasa
    st.session_state.graph = None
    st.session_state.graph_state = {}
    st.session_state.awaiting_approval = None
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.pop("cached_pdf_bytes", None)
    st.session_state.pop("cached_pdf_state_id", None)
    _process_agent_run(user_input, is_feedback=False)


def handle_feedback(user_input: str) -> None:
    """
    Resume the graph from its current checkpoint.
    Injects the user's message as pending_feedback so human_feedback_processor
    can apply modifications or simply pass through on approval.
    """
    _process_agent_run(user_input, is_feedback=True)


def handle_reject() -> None:
    """Reset the entire session so the user can start over."""
    st.session_state.graph = None
    st.session_state.graph_state = {}
    st.session_state.awaiting_approval = None
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.pop("cached_pdf_bytes", None)
    st.session_state.pop("cached_pdf_state_id", None)
    msg = "Session reset. Please describe a new product to optimise."
    _add_message("assistant", msg)

@st.dialog("Restart Session")
def _confirm_restart():
    st.write("Are you sure? This will delete all current progress.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Yes, restart", type="primary", use_container_width=True):
            handle_reject()
            st.rerun()
    with col2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

left_col, right_col = st.columns([1, 2])

# ── Left Column ─────────────────────────────────────────────────────────────
with left_col:
    # Inject Premium CSS
    st.html("""
        <style>
        /* Import premium font */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
        }
        
        /* Glassmorphism for expanders and dataframes */
        .streamlit-expanderHeader {
            background-color: rgba(255,255,255,0.05);
            border-radius: 8px;
        }
        
        /* Modern gradients for primary buttons */
        div.stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            color: white;
            border: none;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            transition: all 0.3s ease;
        }
        div.stButton > button[kind="primary"]:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0, 0, 0, 0.15);
        }

        /* Destructive styling for secondary buttons */
        div.stButton > button[kind="secondary"] {
            background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
            color: white;
            border: none;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            transition: all 0.3s ease;
        }
        div.stButton > button[kind="secondary"]:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0, 0, 0, 0.15);
        }
        
        /* Chat bubbles */
        .stChatMessage {
            background-color: rgba(0,0,0,0.03);
            border-radius: 12px;
            padding: 10px;
            margin-bottom: 10px;
        }
        
        /* Dashboard styling */
        h3 {
            color: #059669;
            font-weight: 600;
        }
        </style>
    """)


    # Chat history
    for msg in st.session_state.chat_history:
        if msg["role"] == "thought":
            with st.expander("🧠 Agent Reasoning", expanded=False):
                for i, thought in enumerate(msg["content"], 1):
                    st.markdown(f"**{i}.** {thought}")
        else:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Quick-action buttons — contextual grouping based on agent state
    if st.session_state.awaiting_approval:
        if st.session_state.awaiting_approval == "interview":
            stage_label = "Interview"
        elif st.session_state.awaiting_approval == "constraints":
            stage_label = "Constraints"
        else:
            stage_label = "Workflow"
        st.divider()
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            btn_lbl = f"✅ Approve {stage_label}"
            if st.button(
                btn_lbl,
                width="stretch",
                type="primary",
            ):
                handle_feedback("Approve")
                if not st.session_state._last_error:
                    st.rerun()
        with btn_col2:
            btn_restart = "❌ Restart Session"
            if st.button(btn_restart, width="stretch"):
                _confirm_restart()
    elif st.session_state.graph_state:
        st.divider()
        btn_restart = "❌ Restart Session"
        if st.button(btn_restart, width="stretch"):
            _confirm_restart()

    # Show persisted error message (survives st.rerun)
    if st.session_state._last_error:
        st.error(st.session_state._last_error)

    # Chat input — NEVER disabled; placeholder adapts to co-pilot state
    if st.session_state.awaiting_approval:
        if st.session_state.awaiting_approval == "interview":
            stage = "interview questions"
        elif st.session_state.awaiting_approval == "constraints":
            stage = "constraints"
        else:
            stage = "Workflow"
            
        placeholder = f"Please answer the {stage} or type 'Approve' to continue…"
    else:
        placeholder = "Describe a product to optimise (e.g. 'An office chair')…"

    user_input = st.chat_input(placeholder)
    if user_input:
        if st.session_state.awaiting_approval:
            handle_feedback(user_input)
        else:
            handle_input(user_input)
        if not st.session_state._last_error:
            st.rerun()


# ── Right Column — Live Dashboard ────────────────────────────────────────────
with right_col:
    state = st.session_state.graph_state
    phase = state.get("current_phase", "init")

    # Inizializzazione sicura delle variabili di stato
    lca_results = state.get("lca_results", [])
    mcda_scores = state.get("mcda_scores", [])
    bom = state.get("bom", [])

    # Se c'è un errore nello stato, forziamo la fase a 'error' per fermare il rendering
    if state.get("error") or state.get("error_message"):
        phase = "error"

    # ── Phase ordering helper ────────────────────────────────────────────────
    _PHASE_ORDER = ["init", "constraints", "interview", "workflow", "material", "lca", "mcda", "complete"]

    def _phase_gte(current: str, target: str) -> bool:
        try:
            return _PHASE_ORDER.index(current) >= _PHASE_ORDER.index(target)
        except ValueError:
            return False

    # ── Welcome screen (init only) ───────────────────────────────────────────
    if phase == "init":
        msg = (
            "👋 **Welcome to the Sustainable Product Optimizer!**\n\n"
            "Describe a product on the left to begin. The dashboard will progressively reveal insights, "
            "BOM decomposition, and environmental metrics as the analysis advances."
        )
        st.info(msg, icon="ℹ️")

    else:
        # ── Dynamic 7-Steps SOP Tracker ─────────────────────────────────────
        _SOP_STEPS = [
            ("1", "Entity Analysis",       "constraints"),
            ("2", "Data Collection",       "interview"),
            ("3", "Workflow & BOM",        "workflow"),
            ("4", "Material Alternatives", "material"),
            ("5", "LCA Validation",        "lca"),
            ("6", "MCDA Scoring",          "mcda"),
            ("7", "Final Report",          "complete"),
        ]

        st.subheader("🏁 7-Steps SOP Progress")
        step_cols = st.columns(len(_SOP_STEPS))
        for col, (num, label, step_phase) in zip(step_cols, _SOP_STEPS):
            with col:
                if phase == step_phase:
                    st.markdown(
                        f"<div style='font-size:11px;color:#f59e0b;'><b>🔄 {num}. {label}</b></div>",
                        unsafe_allow_html=True,
                    )
                elif _phase_gte(phase, step_phase):
                    st.markdown(
                        f"<div style='font-size:11px;color:#10b981;'><b>✅ {num}. {label}</b></div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"<div style='font-size:11px;color:#6b7280;'><b>⏳ {num}. {label}</b></div>",
                        unsafe_allow_html=True,
                    )

        # Live st.status showing current SOP step
        _phase_label_map = {
            "constraints": "1 · Entity Analysis",
            "interview":   "2 · Data Collection",
            "workflow":    "3 · Workflow & BOM",
            "material":    "4 · Material Alternatives",
            "lca":         "5 · LCA Validation",
            "mcda":        "6 · MCDA Scoring",
            "complete":    "7 · Final Report",
            "error":       "❌ Error",
        }
        _current_label = _phase_label_map.get(phase, phase)

        if phase == "complete":
            st.status(f"✅ Step {_current_label}", state="complete", expanded=False)
        elif phase == "error":
            st.status(f"❌ {_current_label}", state="error", expanded=False)
        else:
            with st.status(f"⚙️ Running Step {_current_label}…", state="running", expanded=False):
                thought_log: list[str] = state.get("thought_log", [])
                if thought_log:
                    st.write(f"**Latest thought:** {thought_log[-1]}")

        st.divider()

        # ── Error banner ─────────────────────────────────────────────────────
        if phase == "error":
            err_msg = state.get("error_message") or state.get("error", "Unknown error.")
            st.error(f"❌ **Error during analysis:** {err_msg}\n\nPress **Restart Session** to try again.", icon="🔴")

        # ── Assumptions ──────────────────────────────────────────────────────
        assumptions = state.get("assumptions_list", [])
        if assumptions:
            unique_assumptions = list(dict.fromkeys(assumptions))
            filtered_assumptions = [
                a for a in unique_assumptions
                if "nessuna assunzione" not in a.lower() and "no assumption" not in a.lower()
            ]
            if filtered_assumptions:
                st.markdown("**Assunzioni e Dati Esterni:**")
                for a in filtered_assumptions:
                    if "fallback" in a.lower() or "3.5" in a:
                        st.error(f"⚠️ Data fallback: {a}", icon="🔴")
                    else:
                        st.warning(f"ℹ️ Assumption: {a}", icon="🟡")

        # ── Thought Log — visible during early phases ─────────────────────
        if phase in ("constraints", "interview"):
            thoughts: list[str] = state.get("thought_log", [])
            if thoughts:
                with st.expander("🧠 Thought Log — Agent Reasoning", expanded=True):
                    for i, t in enumerate(thoughts, 1):
                        st.markdown(f"**{i}.** {t}")
            else:
                st.caption("⏳ Waiting for agent thoughts…")

        # ── Constraints (phase >= constraints) ───────────────────────────
        if _phase_gte(phase, "constraints"):
            st.subheader("📋 Extracted Constraints")
            constraints = state.get("constraints", {})
            if constraints:
                for k, v in constraints.items():
                    st.markdown(f"- **{k}**: {v}")
            else:
                st.caption("Constraints will appear here once extracted from your input…")

        # ── Manufacturing Workflow + BOM (phase >= workflow) ──────────────
        if _phase_gte(phase, "workflow"):
            st.subheader("⚙️ Manufacturing Workflow")
            workflow = state.get("workflow_steps", [])
            if workflow:
                st.markdown("The creation of the object has been broken down into the following processes:")
                for idx, step in enumerate(workflow, 1):
                    if isinstance(step, dict):
                        p_name = step.get("process_name", "")
                        p_out  = step.get("process_output", "")
                        st.markdown(f"**Phase {idx}**: {p_name} ➔ **Output**: {p_out}")
                    else:
                        st.markdown(f"**Phase {idx}**: {step}")
            else:
                st.caption("The manufacturing workflow will appear here…")

            st.subheader("📋 Bill of Materials")
            if bom:
                _col_map = {
                    "name":            "Component",
                    "material":        "Material",
                    "weight_kg":       "Weight (kg)",
                    "functional_role": "Functional Role",
                    "baseline_environmental_impact": f"Impact ({settings.environmental_impact_unit}/kg)",
                    "baseline_cost":   "Cost Tier",
                    "lifespan_years":  "Lifespan (yr)",
                }
                task_type = state.get("constraints", {}).get("task_type", "optimization")
                display_bom = []
                for item in bom:
                    if task_type == "modeling":
                        mat_row = item.copy()
                        mat_row["name"] = f"{item.get('name', '')} (Material)"
                        display_bom.append(mat_row)
                        
                        proc = item.get("manufacturing_process")
                        if proc:
                            proc_row = item.copy()
                            proc_row["name"] = f"{item.get('name', '')} (Manufacturing)"
                            proc_row["material"] = proc
                            proc_row["functional_role"] = "Processing"
                            from core.config import PROCESS_IMPACTS
                            proc_row["baseline_environmental_impact"] = PROCESS_IMPACTS.get(proc, 1.0)
                            proc_row["baseline_cost"] = 0.0
                            display_bom.append(proc_row)
                    else:
                        display_bom.append(item)
                
                if task_type == "modeling":
                    transport_comp = next((c for c in state.get("lca_results", []) if c.get("component_name") == "Transport"), None)
                    if transport_comp:
                        mat_name = transport_comp.get("original_material", "Lorry transport")
                        amount = transport_comp.get("original_scores", {}).get("amount", "-")
                        if amount != "-":
                            mat_name += f" (Amount: {amount:.1f})"
                        impact = transport_comp.get("original_scores", {}).get("unit_material_impact", 0.0)
                        
                        display_bom.append({
                            "name": "Transport",
                            "material": mat_name,
                            "weight_kg": "-",
                            "functional_role": "Logistics",
                            "baseline_environmental_impact": impact,
                            "baseline_cost": 0.0,
                            "lifespan_years": "-",
                        })
                    else:
                        dist = state.get("logistics_data", {}).get("distance_km", 0.0)
                        if dist:
                            from core.config import TRANSPORT_IMPACT_PER_TKM
                            display_bom.append({
                                "name": "Transport",
                                "material": f"Lorry transport ({dist} km)",
                                "weight_kg": "-",
                                "functional_role": "Logistics",
                                "baseline_environmental_impact": TRANSPORT_IMPACT_PER_TKM,
                                "baseline_cost": 0.0,
                                "lifespan_years": "-",
                            })
                        
                bom_df = pd.DataFrame(display_bom)
                bom_df = bom_df.rename(columns={k: v for k, v in _col_map.items() if k in bom_df.columns})
                if "Weight (kg)" in bom_df.columns:
                    bom_df["Weight (kg)"] = bom_df["Weight (kg)"].astype(str)
                if "Lifespan (yr)" in bom_df.columns:
                    bom_df["Lifespan (yr)"] = bom_df["Lifespan (yr)"].astype(str)
                _display_cols = [v for k, v in _col_map.items() if v in bom_df.columns]
                st.dataframe(bom_df[_display_cols], width="stretch", hide_index=True)
            else:
                st.caption("The BOM will appear after the agent decomposes the product…")

        # ── Material Alternatives (phase >= material) ─────────────────────
        if _phase_gte(phase, "material"):
            task_type = state.get("constraints", {}).get("task_type", "optimization")

            if lca_results and not mcda_scores:
                st.subheader("🔬 Material Alternatives (LCA Validated)")
                for comp in lca_results:
                    with st.expander(
                        f"**{comp['component_name']}** — original: {comp['original_material']} "
                        f"({comp['original_scores']['environmental_impact']:.3f} {settings.environmental_impact_unit})"
                    ):
                        if task_type == "modeling":
                            st.info("Nessuna alternativa cercata in modalità Modeling. Per confrontare diversi materiali e ricevere suggerimenti di miglioramento automatici, utilizza la modalità Optimization.")
                        else:
                            rows = []
                            for alt in comp.get("alternatives", []):
                                rows.append({
                                    "Alternative": alt["name"],
                                    f"Impact ({settings.environmental_impact_unit})": alt["scores"]["environmental_impact"],
                                    "Energy (MJ)": alt["scores"]["energy_mj"],
                                    "Cost Tier": alt["scores"]["cost_tier"],
                                    "Aesthetic Match": f"{alt['aesthetic_match']:.0%}",
                                    "Structural Match": f"{alt['structural_match']:.0%}",
                                })
                            if rows:
                                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        # ── LCA Chart + MCDA Table (phase >= mcda) ───────────────────────
        if _phase_gte(phase, "mcda"):
            if mcda_scores and lca_results:
                st.subheader("🌍 Environmental Impact: Original vs. Optimised")
                orig_co2_lookup: dict[str, float] = {
                    r["component_name"]: r["original_scores"]["environmental_impact"]
                    for r in lca_results
                }
                chart_rows = []
                for comp in mcda_scores:
                    name = comp["component_name"]
                    orig = orig_co2_lookup.get(name, 0.0)
                    best = comp.get("best_alternative")
                    optimised = orig * (1 - best["impact_reduction_pct"] / 100) if best else orig
                    chart_rows.append({
                        "Component": name,
                        f"Original ({settings.environmental_impact_unit})": round(orig, 3),
                        f"Optimised ({settings.environmental_impact_unit})": round(optimised, 3),
                    })
                if chart_rows:
                    chart_df = pd.DataFrame(chart_rows).set_index("Component")
                    st.bar_chart(chart_df, color=["#e05555", "#33b86c"])

                st.subheader("🏆 MCDA Recommendations")
                summary_rows = []
                for comp in mcda_scores:
                    best = comp.get("best_alternative")
                    if best:
                        summary_rows.append({
                            "Component":            comp["component_name"],
                            "Original Material":    comp["original_material"],
                            "Best Alternative":     best["name"],
                            "Impact Reduction (%)": f"{best['impact_reduction_pct']:.1f}%",
                            "Cost Δ (tier)":        best["cost_delta"],
                            "MCDA Score":           round(best["mcda_score"], 3),
                            "Justification":        best["justification"],
                        })
                if summary_rows:
                    st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)

        # ── Download buttons — ONLY when complete ────────────────────────
        if phase == "complete":
            st.divider()
            task_type = state.get("constraints", {}).get("task_type", "optimization")
            if task_type == "modeling":
                st.success("✅ Analysis complete! Download the full report below.")
            else:
                st.success("✅ Optimisation complete! Download the full report below.")

            import hashlib, json
            _pdf_key         = "cached_pdf_bytes"
            _pdf_state_key   = "cached_pdf_state_id"
            _pdf_key_content = hashlib.md5(
                json.dumps(state.get("mcda_scores", []), sort_keys=True).encode()
            ).hexdigest()

            if (
                _pdf_key not in st.session_state
                or st.session_state.get(_pdf_state_key) != _pdf_key_content
            ):
                st.session_state[_pdf_key]       = generate_pdf_report(state)
                st.session_state[_pdf_state_key] = _pdf_key_content
            pdf_bytes = st.session_state[_pdf_key]

            if pdf_bytes is not None:
                col_pdf, col_html = st.columns(2)
                with col_pdf:
                    st.download_button(
                        label="📄 Download Optimization Report (PDF)",
                        data=pdf_bytes,
                        file_name="optimization_report.pdf",
                        mime="application/pdf",
                        width="stretch",
                        type="primary",
                    )
                with col_html:
                    st.download_button(
                        label="📥 Download Optimization Report (HTML)",
                        data=generate_html_report(state),
                        file_name="optimization_report.html",
                        mime="text/html",
                        width="stretch",
                    )
            else:
                st.warning(
                    "PDF export is unavailable: WeasyPrint system dependencies "
                    "(Cairo/Pango/GTK3) are not installed on this machine. "
                    "See README for installation instructions."
                )
                st.download_button(
                    label="📥 Download Optimization Report (HTML)",
                    data=generate_html_report(state),
                    file_name="optimization_report.html",
                    mime="text/html",
                    width="stretch",
                )

