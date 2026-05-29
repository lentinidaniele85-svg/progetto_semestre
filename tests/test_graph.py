"""
End-to-end tests for the LangGraph agent pipeline.

All LLM calls are mocked so tests run without any active model.
The LCA Data Layer (CSV client) runs for real to verify the full pipeline.

Node sequence (current graph):
  constraint_extractor
  → [interrupt] human_feedback_processor_constraints
  → workflow_bom_ideator
  → [interrupt] human_feedback_processor_interview | human_feedback_processor_workflow
  → material_ideator
  → lca_validator
  → mcda_scorer
"""
import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from agents.graph import build_graph
from agents.schemas import (
    BOMComponent,
    ConstraintsExtract,
    MaterialAlternative,
    ComponentAlternatives,
    WorkflowStep,
    BOMExtract,
    WorkflowPlannerOutput,
    WorkflowAndBOMResponse,
    MaterialIdeationResponse,
)
from agents.state import AgentState

# ---------------------------------------------------------------------------
# Fake LLM stubs
# ---------------------------------------------------------------------------

_FAKE_BOM_COMPONENTS = [
    BOMComponent(name="legs", material="steel", weight_kg=2.0, functional_role="load-bearing frame", material_source="steel sheet", geometry="Profili/Tubi", manufacturing_process="Extrusion", unit_impact_value=2.5),
    BOMComponent(name="armrests", material="plastic", weight_kg=0.5, functional_role="comfort padding", material_source="plastic pellet", geometry="Pezzi Pieni Complessi", manufacturing_process="Injection moulding", unit_impact_value=3.5),
]

_FAKE_WORKFLOW_RESPONSE = WorkflowAndBOMResponse(
    is_material_only=False,
    is_interview_complete=True,
    interview_questions=[],
    geography="Global",
    total_mass_kg=2.5,
    assumptions_made=[],
    workflow_steps=[
        WorkflowStep(process_name="Injection Moulding", process_output="plastic armrests"),
        WorkflowStep(process_name="Steel Welding",      process_output="steel frame"),
    ],
    components=_FAKE_BOM_COMPONENTS,
)

_FAKE_MATERIAL_RESPONSE = MaterialIdeationResponse(
    components=[
        ComponentAlternatives(
            component_name="legs",
            alternatives=[
                MaterialAlternative(name="recycled steel",   justification="Lower carbon than virgin steel.", aesthetic_match=0.9, structural_match=0.95, estimated_cost_change="Same"),
                MaterialAlternative(name="aluminum",         justification="Lighter with good strength.",     aesthetic_match=0.8, structural_match=0.85, estimated_cost_change="More Expensive"),
                MaterialAlternative(name="bamboo composite", justification="Natural, renewable option.",      aesthetic_match=0.7, structural_match=0.65, estimated_cost_change="Cheaper"),
            ],
        ),
        ComponentAlternatives(
            component_name="armrests",
            alternatives=[
                MaterialAlternative(name="wood",             justification="Warm feel, renewable.",           aesthetic_match=0.85, structural_match=0.70, estimated_cost_change="Same"),
                MaterialAlternative(name="recycled plastic", justification="Lower-impact circular option.",   aesthetic_match=0.80, structural_match=0.80, estimated_cost_change="Cheaper"),
                MaterialAlternative(name="hemp composite",   justification="Bio-based, low carbon.",          aesthetic_match=0.65, structural_match=0.60, estimated_cost_change="Same"),
            ],
        ),
    ]
)


class _FakeChain:
    """Sync + async chain stub returning a fixed value."""

    def __init__(self, return_value: Any) -> None:
        self._val = return_value

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return self._val

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        return self._val


class FakeLLM:
    """Drop-in replacement for any BaseChatModel in node tests."""

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        from langchain_core.messages import AIMessage
        return AIMessage(content="{}")

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        from langchain_core.messages import AIMessage
        return AIMessage(content="{}")

    def with_structured_output(self, schema: type) -> Any:
        if schema is ConstraintsExtract:
            return _FakeChain(ConstraintsExtract(
                budget="standard", 
                aesthetics="professional",
                dimensions="50x50x50",
                mechanical_load="100kg",
                usage_environment="office",
                target_lifespan="10 years"
            ))
        if schema is BOMExtract:
            return _FakeChain(BOMExtract(components=_FAKE_BOM_COMPONENTS))
        if schema is WorkflowAndBOMResponse:
            return _FakeChain(_FAKE_WORKFLOW_RESPONSE)
        if schema is ComponentAlternatives:
            return _FakeChain(_FAKE_MATERIAL_RESPONSE.components[0])
        if schema.__name__ == "MaterialIdeationResponse":
            return _FakeChain(_FAKE_MATERIAL_RESPONSE)
        if schema.__name__ == "SearchQueryList":
            # Usiamo __name__ per evitare circular imports
            return _FakeChain(schema(queries=["steel", "plastic", "aluminum", "bamboo"]))
        if schema.__name__ == "MassEstimation":
            # Usiamo __name__ per evitare circular imports
            return _FakeChain(schema(mass_kg=10.0, reasoning="Fake LLM estimation for testing"))
        raise ValueError(f"FakeLLM: unexpected schema {schema}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INITIAL_STATE: AgentState = {
    "user_input": (
        "A standard office chair with steel legs and plastic armrests. "
        "Dimensions: 50x50x90cm. Load: 120kg. Environment: indoor office. "
        "Target lifespan: 10 years."
    ),
    "mode": "auto",
    "thought_log": [],
    "bom": [],
    "workflow_steps": [],
    "semantic_alternatives": [],
    "lca_results": [],
    "mcda_scores": [],
    "constraints": {},
    "chat_history": [],
}


def _run_graph(mode: str = "auto") -> AgentState:
    graph = build_graph(mode=mode)
    fake_llm = FakeLLM()
    with (
        patch("agents.nodes.ModelFactory") as MockNodesFactory,
        patch("agents.workflow_node.ModelFactory") as MockWorkflowFactory,
        patch("agents.material_node.ModelFactory") as MockMaterialFactory,
        patch("core.llm_factory.ModelFactory") as MockCoreFactory,
    ):
        for mock in (MockNodesFactory, MockWorkflowFactory, MockMaterialFactory, MockCoreFactory):
            mock.get_model.return_value = fake_llm
            mock.get_system_prompt.return_value = "You are a sustainability expert. {user_input} {constraints}"
        return asyncio.run(graph.ainvoke(dict(_INITIAL_STATE)))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_thought_log_populated() -> None:
    result = _run_graph()
    assert len(result["thought_log"]) >= 4, (
        f"Expected at least 4 thought entries, got: {result['thought_log']}"
    )


def test_bom_created() -> None:
    result = _run_graph()
    bom = result["bom"]
    assert len(bom) == 2
    names = {c["name"] for c in bom}
    assert names == {"legs", "armrests"}


def test_workflow_steps_populated() -> None:
    result = _run_graph()
    steps = result.get("workflow_steps", [])
    assert len(steps) >= 1
    assert all("process_name" in s for s in steps)


def test_semantic_alternatives_generated() -> None:
    result = _run_graph()
    alts = result["semantic_alternatives"]
    assert len(alts) == 2
    assert all("component_name" in a for a in alts)
    assert all(len(a["alternatives"]) > 0 for a in alts)


def test_lca_results_have_scores() -> None:
    result = _run_graph()
    for comp in result["lca_results"]:
        assert "original_scores" in comp
        scores = comp["original_scores"]
        assert scores["environmental_impact"] >= 0
        assert scores["energy_mj"] >= 0
        for alt in comp["alternatives"]:
            assert "scores" in alt
            assert alt["scores"]["environmental_impact"] >= 0


def test_mcda_scores_calculated() -> None:
    result = _run_graph()
    mcda = result["mcda_scores"]
    assert len(mcda) > 0
    for comp in mcda:
        assert "best_alternative" in comp
        # If all alternatives are discarded (e.g. they have higher CO2 impact than baseline),
        # best_alternative will legitimately be None and alternatives list will be empty.
        if comp["best_alternative"] is not None:
            for alt in comp["alternatives"]:
                assert "mcda_score" in alt
                assert isinstance(alt["mcda_score"], float)
                assert "impact_reduction_pct" in alt
                assert "cost_delta" in alt


def test_mcda_best_is_highest_score() -> None:
    result = _run_graph()
    for comp in result["mcda_scores"]:
        if len(comp["alternatives"]) > 1 and comp["best_alternative"] is not None:
            scores = [a["mcda_score"] for a in comp["alternatives"]]
            assert scores == sorted(scores, reverse=True), (
                "Alternatives must be sorted by MCDA score descending"
            )
            assert comp["best_alternative"]["mcda_score"] == scores[0]


def test_constraints_extracted() -> None:
    result = _run_graph()
    assert isinstance(result["constraints"], dict)
    assert len(result["constraints"]) > 0


def test_interactive_mode_graph_compiles() -> None:
    """Verify interactive graph builds without error (no run needed)."""
    graph = build_graph(mode="interactive")
    assert graph is not None
