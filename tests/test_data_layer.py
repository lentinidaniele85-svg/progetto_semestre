import asyncio
from pathlib import Path

import pytest

from data.csv_lca_client import CSVLcaClient

@pytest.fixture
def client() -> CSVLcaClient:
    return CSVLcaClient()


# ---------------------------------------------------------------------------
# search_materials tests
# ---------------------------------------------------------------------------

def test_search_materials_returns_results(client: CSVLcaClient) -> None:
    results = asyncio.run(client.search_materials("electricity"))
    assert len(results) > 0, "Expected at least one electricity material"


def test_search_materials_keys(client: CSVLcaClient) -> None:
    results = asyncio.run(client.search_materials("electricity"))
    expected_keys = {"id", "providerName", "flowName", "location"}
    for row in results:
        assert expected_keys.issubset(row.keys()), f"Missing keys in row: {row}"


def test_search_materials_no_match(client: CSVLcaClient) -> None:
    results = asyncio.run(client.search_materials("xyzzy_nonexistent_material"))
    assert results == []


def test_search_materials_location_filter(client: CSVLcaClient) -> None:
    results = asyncio.run(client.search_materials("electricity", location="eu"))
    for row in results:
        assert "eu" in row["location"].lower(), f"Unexpected location: {row['location']}"


def test_search_materials_case_insensitive(client: CSVLcaClient) -> None:
    lower = asyncio.run(client.search_materials("wood"))
    upper = asyncio.run(client.search_materials("WOOD"))
    assert len(lower) == len(upper)


# ---------------------------------------------------------------------------
# get_impact_scores tests
# ---------------------------------------------------------------------------

def test_get_impact_scores_known_id(client: CSVLcaClient) -> None:
    scores = asyncio.run(client.get_impact_scores("mat-001"))
    _assert_valid_scores(scores)


def test_get_impact_scores_unknown_id_still_returns(client: CSVLcaClient) -> None:
    scores = asyncio.run(client.get_impact_scores("unknown-id-999"))
    _assert_valid_scores(scores)


def test_get_impact_scores_deterministic(client: CSVLcaClient) -> None:
    scores_a = asyncio.run(client.get_impact_scores("mat-004"))
    scores_b = asyncio.run(client.get_impact_scores("mat-004"))
    assert scores_a == scores_b, "Impact scores must be deterministic for same id"


def test_get_impact_scores_wood_lower_co2_than_aluminum(client: CSVLcaClient) -> None:
    wood_scores = asyncio.run(client.get_impact_scores("wood"))
    aluminum_scores = asyncio.run(client.get_impact_scores("aluminum"))
    assert wood_scores["environmental_impact"] < aluminum_scores["environmental_impact"], (
        "Wood should have lower impact than primary aluminum"
    )


def test_get_impact_scores_recycled_lower_than_primary_aluminum(
    client: CSVLcaClient,
) -> None:
    recycled = asyncio.run(client.get_impact_scores("recycled aluminum"))
    primary = asyncio.run(client.get_impact_scores("aluminum"))
    assert recycled["environmental_impact"] < primary["environmental_impact"], (
        "Recycled aluminum should have lower impact than primary aluminum"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_valid_scores(scores: dict) -> None:
    assert "environmental_impact" in scores
    assert "energy_mj" in scores
    assert "water_l" in scores
    assert "cost_tier" in scores
    assert scores["environmental_impact"] > 0
    assert scores["energy_mj"] >= 0
    assert scores["water_l"] >= 0
    assert 0 <= scores["cost_tier"] <= 4
