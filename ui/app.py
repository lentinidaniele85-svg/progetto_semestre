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

    async def _consume():
        nonlocal last
        async for event in graph.astream(
            input_state_or_none, config, stream_mode="values"
        ):
            last = event
            st.session_state.graph_state = event
            thoughts: list[str] = event.get("thought_log", [])
            if thoughts:
                status.write(f"**Step {len(thoughts)}:** {thoughts[-1]}")

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

    # Append new thought entries as a 🤖 assistant message in the conversation
    all_thoughts: list[str] = last.get("thought_log", [])
    new_thoughts = all_thoughts[prev_thought_count:]
    if new_thoughts:
        lines = "\n".join(f"• {t}" for t in new_thoughts)
        _add_message("assistant", f"🤖 **Agent steps:**\n{lines}")

    return last


# ---------------------------------------------------------------------------
# Helper: chat history
# ---------------------------------------------------------------------------

def _add_message(role: str, content: str) -> None:
    st.session_state.chat_history.append({"role": role, "content": content})


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def handle_input(user_input: str) -> None:
    """Start a fresh agent run for a new product description."""
    _add_message("user", user_input)
    st.session_state._last_error = None  # clear previous errors
    mode = st.session_state.mode
    graph = _get_graph()
    config = _thread_config()

    initial_state = {
        "user_input": user_input,
        "mode": mode,
        "thought_log": [],
        "chat_history": list(st.session_state.chat_history),
    }

    try:
        _stream(graph, initial_state, config, "🤖 Agent is analysing your product...")
    except _CONNECTION_ERRORS:
        msg = (
            "Impossibile connettersi al modello LLM. "
            "Verifica che la chiave OPENROUTER_API_KEY sia valida e che ci sia connessione internet."
        )
        st.session_state._last_error = msg
        st.session_state.awaiting_approval = None
        return
    except Exception as exc:
        msg = f"An unexpected error occurred: {exc}"
        st.session_state._last_error = msg
        st.session_state.awaiting_approval = None
        return

    if mode == "auto":
        _add_message(
            "assistant",
            "Analysis complete! See the dashboard on the right for the full results.",
        )
        st.session_state.awaiting_approval = None
    else:
        next_node = _next_interrupt(graph, config)
        phase = st.session_state.graph_state.get("current_phase", "")
        if next_node == "human_feedback_processor" and phase == "constraints":
            constraints = st.session_state.graph_state.get("constraints", {})
            if constraints:
                constraints_str = "\n".join([f"- **{k}**: {v}" for k, v in constraints.items()])
            else:
                constraints_str = "- *(Nessun vincolo specifico estratto)*"
            _add_message(
                "assistant",
                f"Ho estratto questi vincoli e specifiche dal tuo input:\n{constraints_str}\n\n"
                "**Vanno bene o vuoi aggiungerne/modificarne alcuni?** Scrivi le tue modifiche o premi **Approve** per continuare."
            )
            st.session_state.awaiting_approval = "constraints"
        elif next_node == "human_feedback_processor" and phase == "interview":
            questions = st.session_state.graph_state.get("pending_feedback", "Can you provide more specific details about dimensions, load, and usage environment?")
            _add_message(
                "assistant",
                f"I need a few more details before proceeding:\n\n{questions}\n\n"
                "**Please reply with the missing specifications.**",
            )
            st.session_state.awaiting_approval = "interview"
        elif next_node == "human_feedback_processor" and phase == "workflow":
            _add_message(
                "assistant",
                "I've mapped the required manufacturing processes and component breakdown.\n\n"
                "**Please verify the workflow above.** Type any modifications or hit **Approve** to proceed with Material Ideation.",
            )
            st.session_state.awaiting_approval = "workflow"
        else:
            _add_message("assistant", "Analysis complete!")
            st.session_state.awaiting_approval = None


def handle_feedback(user_input: str) -> None:
    """
    Resume the graph from its current checkpoint.
    Injects the user's message as pending_feedback so human_feedback_processor
    can apply modifications or simply pass through on approval.
    """
    _add_message("user", user_input)
    graph = _get_graph()
    config = _thread_config()

    st.session_state._last_error = None  # clear previous errors

    # Inject feedback into graph state before resuming
    graph.update_state(
        config,
        {
            "pending_feedback": user_input,
            "chat_history": list(st.session_state.chat_history),
        },
    )

    try:
        _stream(graph, None, config, "🤖 Processing your feedback...")
    except _CONNECTION_ERRORS:
        msg = (
            "Impossibile connettersi al modello LLM. "
            "Verifica che la chiave OPENROUTER_API_KEY sia valida e che ci sia connessione internet."
        )
        st.session_state._last_error = msg
        st.session_state.awaiting_approval = None
        return
    except Exception as exc:
        msg = f"An unexpected error occurred: {exc}"
        st.session_state._last_error = msg
        st.session_state.awaiting_approval = None
        return

    next_node = _next_interrupt(graph, config)
    phase = st.session_state.graph_state.get("current_phase", "")

    if next_node == "human_feedback_processor" and phase == "constraints":
        constraints = st.session_state.graph_state.get("constraints", {})
        if constraints:
            constraints_str = "\n".join([f"- **{k}**: {v}" for k, v in constraints.items()])
        else:
            constraints_str = "- *(Nessun vincolo specifico estratto)*"
        _add_message(
            "assistant",
            f"Ho estratto questi vincoli e specifiche dal tuo input:\n{constraints_str}\n\n"
            "**Vanno bene o vuoi aggiungerne/modificarne alcuni?** Scrivi le tue modifiche o premi **Approve** per continuare."
        )
        st.session_state.awaiting_approval = "constraints"
    elif next_node == "human_feedback_processor" and phase == "interview":
        questions = st.session_state.graph_state.get("pending_feedback", "More details needed.")
        _add_message(
            "assistant",
            f"Still missing some details:\n\n{questions}\n\n**Please provide the specs.**",
        )
        st.session_state.awaiting_approval = "interview"
    elif next_node == "human_feedback_processor" and phase == "workflow":
        _add_message(
            "assistant",
            "I've mapped the required manufacturing processes and component breakdown.\n\n"
            "**Please verify the workflow above.** Type any modifications or hit **Approve** to proceed with Material Ideation.",
        )
        st.session_state.awaiting_approval = "workflow"
    else:
        _add_message(
            "assistant",
            "Optimisation complete! Final MCDA scores and the best material "
            "recommendations are shown in the dashboard.",
        )
        st.session_state.awaiting_approval = None


def handle_reject() -> None:
    """Reset the entire session so the user can start over."""
    st.session_state.graph = None
    st.session_state.graph_state = {}
    st.session_state.awaiting_approval = None
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.pop("cached_pdf_bytes", None)
    st.session_state.pop("cached_pdf_state_id", None)
    _add_message(
        "assistant",
        "Session reset. Please describe a new product to optimise.",
    )


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
        
        /* Modern gradients for buttons */
        div.stButton > button {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            color: white;
            border: none;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            transition: all 0.3s ease;
        }
        div.stButton > button:hover {
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
            if st.button(
                f"✅ Approve {stage_label}",
                width="stretch",
                type="primary",
            ):
                handle_feedback("Approve")
                if not st.session_state._last_error:
                    st.rerun()
        with btn_col2:
            if st.button("❌ Restart Session", width="stretch"):
                handle_reject()
                st.rerun()
    elif st.session_state.graph_state:
        st.divider()
        if st.button("❌ Restart Session", width="stretch"):
            handle_reject()
            st.rerun()

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
        placeholder = (
            f"Please answer the {stage} or type 'Approve' to continue…"
        )
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

    # ── 0. 7-Steps LCA Workflow Tracker ─────────────────────────────────────
    current_step = state.get("current_lca_step", 1)
    st.subheader("🏁 7-Steps Progress")
    steps = [
        "Analisi Entità",
        "Lookup Aggregato",
        "Selezione Materiale",
        "Vincolo Geometrico",
        "Scomposizione BOM",
        "Calcolo Logistica",
        "Validazione & Gap Analysis"
    ]
    cols = st.columns(len(steps))
    for i, col in enumerate(cols):
        step_num = i + 1
        with col:
            if step_num < current_step:
                st.markdown(f"<div style='font-size:12px; color:#10b981;'><b>✅ {step_num}. {steps[i]}</b></div>", unsafe_allow_html=True)
            elif step_num == current_step:
                st.markdown(f"<div style='font-size:12px; color:#f59e0b;'><b>🔄 {step_num}. {steps[i]}</b></div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='font-size:12px; color:#9ca3af;'><b>⏳ {step_num}. {steps[i]}</b></div>", unsafe_allow_html=True)
    
    st.divider()

    assumptions = state.get("assumptions_list", [])
    if assumptions:
        st.warning(
            "**Assunzioni e semplificazioni effettuate dall'IA:**\n\n"
            + "\n".join([f"- {a}" for a in assumptions]),
            icon="⚠️",
        )

    # T07: mostra errore esplicito se current_phase == "error"
    if state.get("current_phase") == "error":
        st.error(
            f"❌ **Errore durante l'analisi:** {state.get('error_message', 'Errore sconosciuto.')}\n\n"
            "Premi **Ricomincia Sessione** per riprovare.",
            icon="🔴",
        )

    # ── 1. Thought Log ──────────────────────────────────────────────────────
    thought_log: list[str] = state.get("thought_log", [])
    with st.expander(
        f"🧠 Agent Thought Log ({len(thought_log)} steps)",
        expanded=bool(thought_log),
    ):
        if thought_log:
            for i, thought in enumerate(thought_log, 1):
                st.markdown(f"**{i}.** {thought}")
        else:
            st.caption("Thoughts will appear here as the agent runs…")

    # ── 1.5 Workflow Produttivo ──────────────────────────────────────────────
    st.subheader("⚙️ Workflow Produttivo")
    workflow = state.get("workflow_steps", [])
    if workflow:
        st.markdown("La creazione dell'oggetto è stata suddivisa nei seguenti processi:")
        for idx, step in enumerate(workflow, 1):
            if isinstance(step, dict):
                p_name = step.get('process_name', '')
                p_out = step.get('process_output', '')
                st.markdown(f"**Fase {idx}**: {p_name} ➔ **Output**: {p_out}")
            else:
                st.markdown(f"**Fase {idx}**: {step}")
    else:
        st.caption("Il workflow produttivo (Fase 1) apparirà qui…")

    # ── 2. Bill of Materials ─────────────────────────────────────────────────
    st.subheader("📋 Bill of Materials")
    bom: list[dict] = state.get("bom", [])
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
        bom_df = pd.DataFrame(bom)
        bom_df = bom_df.rename(columns={k: v for k, v in _col_map.items() if k in bom_df.columns})
        _display_cols = [v for k, v in _col_map.items() if v in bom_df.columns]
        st.dataframe(bom_df[_display_cols], width="stretch", hide_index=True)
    else:
        st.caption("The BOM will appear after the agent decomposes the product…")

    # ── 3. LCA Alternatives (shown mid-flow in interactive mode) ─────────────
    lca_results: list[dict] = state.get("lca_results", [])
    mcda_scores: list[dict] = state.get("mcda_scores", [])

    if lca_results and not mcda_scores:
        st.subheader("🔬 Material Alternatives (LCA Validated)")
        for comp in lca_results:
            with st.expander(
                f"**{comp['component_name']}** — original: {comp['original_material']} "
                f"({comp['original_scores']['environmental_impact']:.3f} {settings.environmental_impact_unit})"
            ):
                rows = []
                for alt in comp.get("alternatives", []):
                    rows.append(
                        {
                            "Alternative": alt["name"],
                            f"Impact ({settings.environmental_impact_unit})": alt["scores"]["environmental_impact"],
                            "Energy (MJ)": alt["scores"]["energy_mj"],
                            "Cost Tier": alt["scores"]["cost_tier"],
                            "Aesthetic Match": f"{alt['aesthetic_match']:.0%}",
                            "Structural Match": f"{alt['structural_match']:.0%}",
                        }
                    )
                if rows:
                    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # ── 4. Environmental Impact Chart + MCDA Recommendations ──────────────────────────
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
            chart_rows.append(
                {
                    "Component": name,
                    f"Original ({settings.environmental_impact_unit})": round(orig, 3),
                    f"Optimised ({settings.environmental_impact_unit})": round(optimised, 3),
                }
            )

        if chart_rows:
            chart_df = pd.DataFrame(chart_rows).set_index("Component")
            st.bar_chart(chart_df, color=["#e05555", "#33b86c"])

        # MCDA Recommendations table
        st.subheader("🏆 MCDA Recommendations")
        summary_rows = []
        for comp in mcda_scores:
            best = comp.get("best_alternative")
            if best:
                summary_rows.append(
                    {
                        "Component": comp["component_name"],
                        "Original Material": comp["original_material"],
                        "Best Alternative": best["name"],
                        "Impact Reduction (%)": f"{best['impact_reduction_pct']:.1f}%",
                        "Cost Δ (tier)": best["cost_delta"],
                        "MCDA Score": round(best["mcda_score"], 3),
                        "Justification": best["justification"],
                    }
                )
        if summary_rows:
            st.dataframe(
                pd.DataFrame(summary_rows),
                width="stretch",
                hide_index=True,
            )

        st.divider()
        st.success("✅ Optimisation complete! Download the full report below.")

        # Cache PDF bytes in session state so WeasyPrint only runs once per
        # completed analysis, not on every Streamlit rerender.
        _pdf_key = "cached_pdf_bytes"
        _pdf_state_key = "cached_pdf_state_id"
        if (
            _pdf_key not in st.session_state
            or st.session_state.get(_pdf_state_key) != id(state.get("mcda_scores"))
        ):
            st.session_state[_pdf_key] = generate_pdf_report(state)
            st.session_state[_pdf_state_key] = id(state.get("mcda_scores"))
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

    elif not mcda_scores and not lca_results:
        st.caption(
            "Environmental impact comparison and material recommendations will appear "
            "after the full analysis completes…"
        )
